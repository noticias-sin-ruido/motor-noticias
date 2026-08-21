"""
El adaptador que habla `/v1/chat/completions`, el estándar de hecho.

Cubre OpenAI, Azure, OpenRouter, Groq, Together, Fireworks, DeepSeek, Mistral,
xAI, Perplexity, vLLM, LM Studio, llama.cpp, Ollama, LocalAI **y Gemini**, que
expone su propia capa compatible. Todos entran cambiando `base_url`, sin código.
"""
import json
import logging
import re
from typing import Optional, Type

import httpx
from pydantic import BaseModel

from ...models import ModeloIA, ModoEstructura
from .base import (
    ErrorDeProveedor,
    RespuestaBloqueada,
    leer_api_key,
    validar_base_url,
    validar_opciones,
)

logger = logging.getLogger(__name__)

# Más largo que los 10 s de una página de artículo y por un motivo distinto: acá
# no se está bajando un HTML sino esperando a que un modelo razone y escriba una
# síntesis entera. Medido sobre Gemini, un cluster tarda ~8,7 s; un modelo local
# grande puede tardar bastante más sin que eso sea un fallo.
TIMEOUT_SEGUNDOS = 120.0

# El sondeo tiene el suyo, **mucho más corto, y es una defensa y no una
# prolijidad**. `POST /modelos` no tiene autenticación y prueba dos modos, así
# que con el timeout de una síntesis real serían 240 s por request contra un
# host que no contesta. El endpoint es sync y corre en el threadpool: cuarenta
# pedidos a un agujero negro dejaban **toda la API** sin responder.
#
# Un pedido que pide "un solo elemento, textos de una o dos palabras" no
# necesita más que esto.
TIMEOUT_SONDEO_SEGUNDOS = 15.0

# Nombre de la herramienta cuando se usa el modo `TOOLS`. Da igual cuál sea
# mientras sea estable: el modelo lo ve en el prompt de tool-calling.
NOMBRE_HERRAMIENTA = "entregar_sintesis"

# Cerca de markdown alrededor del JSON. Los modelos chicos —justamente los que
# uno corre localmente— la agregan seguido aunque se les pida JSON puro.
# Pelarla es leniencia deliberada: rechazarlos sería correcto en sentido
# estricto y dejaría afuera el caso de uso que motiva todo este backlog.
_CERCA = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

# **Ninguna, y es una postura y no un olvido.** Este adaptador habla con
# decenas de proveedores distintos; una palanca que funcione en uno no tiene por
# qué existir en otro, y aceptarla acá sería prometer un efecto que depende de
# contra quién apunte el `base_url` de esa fila. Las palancas específicas viven
# en los adaptadores nativos, que sí saben con quién están hablando.
OPCIONES_ACEPTADAS = frozenset()

# Los dos mecanismos: cuál sirve lo descubre el sondeo. Ver `ModoEstructura`.
MODOS_SOPORTADOS = (ModoEstructura.RESPONSE_FORMAT, ModoEstructura.TOOLS)


class OpenAICompatible:
    """
    Un modelo alcanzable por el protocolo de OpenAI.

    Se habla por HTTP directo con `httpx` en vez de traer el SDK de OpenAI como
    dependencia: de todo el SDK usaríamos un solo endpoint, y a cambio
    heredaríamos su ciclo de releases y su manejo de reintentos, que ya tenemos
    resuelto río arriba con `tenacity`.
    """

    OPCIONES_ACEPTADAS = OPCIONES_ACEPTADAS
    MODOS_SOPORTADOS = MODOS_SOPORTADOS

    def __init__(self, modelo: ModeloIA):
        self.modelo = modelo
        self.api_key = leer_api_key(modelo)
        self.url = f"{validar_base_url(modelo.base_url)}/chat/completions"
        validar_opciones(modelo.opciones, OPCIONES_ACEPTADAS, "El adaptador compatible")

    def _cuerpo(self, prompt: str, esquema: Type[BaseModel]) -> dict:
        """
        El pedido, según cómo este proveedor acepte que se le pida estructura.

        Los dos modos mandan **el mismo JSON Schema**; lo que cambia es el sobre.
        Cuál corresponde no lo declara el operador: lo descubrió el sondeo del
        alta. Ver `ModoEstructura`.
        """
        json_schema = esquema.model_json_schema()
        cuerpo = {
            "model": self.modelo.modelo,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.modelo.temperatura,
        }

        # Solo si el operador lo pidió. **El default es sin techo, a propósito**:
        # un límite arbitrario trunca la síntesis a mitad de camino, el JSON
        # queda inválido y se reintenta tres veces lo mismo. Quien lo configure
        # está eligiendo acotar el gasto sabiendo ese riesgo, y el corte por
        # longitud se detecta y se dice con todas las letras (ver `_leer`).
        if self.modelo.max_tokens:
            cuerpo["max_tokens"] = self.modelo.max_tokens

        if self.modelo.modo_estructura == ModoEstructura.TOOLS:
            cuerpo["tools"] = [{
                "type": "function",
                "function": {
                    "name": NOMBRE_HERRAMIENTA,
                    "description": "Entrega la síntesis con la forma pedida.",
                    "parameters": json_schema,
                },
            }]
            # Forzado: sin esto el modelo puede contestar en prosa y perderíamos
            # la estructura, que es justamente lo que veníamos a buscar.
            cuerpo["tool_choice"] = {
                "type": "function",
                "function": {"name": NOMBRE_HERRAMIENTA},
            }
        else:
            cuerpo["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "sintesis",
                    "schema": json_schema,
                    # `strict` queda en False: activarlo exige que el esquema
                    # declare `additionalProperties: false` y que **todos** los
                    # campos estén en `required`, y el nuestro tiene opcionales
                    # (verificado: `anyOf` aparece 2 veces). Sin strict el
                    # esquema es una guía y no una garantía, que es aceptable
                    # porque río arriba la respuesta se valida con Pydantic y
                    # `tenacity` reintenta lo que no valide.
                    "strict": False,
                },
            }
        return cuerpo

    def generar(self, prompt: str, esquema: Type[BaseModel]) -> BaseModel:
        """El JSON del modelo, ya validado contra el esquema."""
        crudo = self.probar(prompt, esquema)
        try:
            return esquema.model_validate(json.loads(crudo))
        except json.JSONDecodeError as error:
            # Error normal y no bloqueo: `tenacity` reintenta, y sin `strict` un
            # JSON mal armado es un resultado esperable de vez en cuando, sobre
            # todo en modelos chicos.
            raise ErrorDeProveedor(
                f"El modelo no devolvió JSON válido: {error}"
            ) from error

    def probar(
        self, prompt: str, esquema: Type[BaseModel], timeout: Optional[float] = None
    ) -> str:
        """
        Lo mismo que `generar`, pero devuelve el texto crudo **sin validar**.

        Es lo que necesita el sondeo del alta: ahí se comprueba que el proveedor
        respete la **forma** del esquema, no que haya inventado una síntesis que
        cumpla las reglas de negocio. Validar de más daría falsos negativos —
        rechazaríamos proveedores que sí sirven. Ver `services/modelos.sondear`.
        """
        try:
            respuesta = httpx.post(
                self.url,
                json=self._cuerpo(prompt, esquema),
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=timeout or TIMEOUT_SEGUNDOS,
            )
        except httpx.HTTPError as error:
            raise ErrorDeProveedor(
                f"No se pudo hablar con {self.url}: {type(error).__name__}: {error}"
            ) from error

        if respuesta.status_code != 200:
            raise ErrorDeProveedor(_mensaje_de_error(respuesta))

        return self._leer(respuesta.json())

    def _leer(self, datos: dict) -> str:
        opciones = datos.get("choices") or []
        if not opciones:
            raise ErrorDeProveedor("La respuesta no trajo ningún `choice`.")

        opcion = opciones[0]
        motivo = opcion.get("finish_reason")

        # La normalización que le toca a este adaptador: el bloqueo por filtros
        # se llama distinto en cada proveedor y río arriba solo se entiende
        # `RespuestaBloqueada`.
        if motivo == "content_filter":
            raise RespuestaBloqueada(
                f"{self.modelo.nombre} bloqueó la respuesta por filtros de contenido"
            )

        # Se detecta y se dice. Sin esto, un corte por longitud devuelve un JSON
        # partido al medio, falla el parseo y se reintenta tres veces **lo
        # mismo**, porque la causa no es transitoria: es el techo configurado.
        if motivo == "length":
            raise ErrorDeProveedor(
                f"La respuesta se cortó por `max_tokens` "
                f"({self.modelo.max_tokens}). Subilo o sacalo: el JSON queda "
                f"partido y reintentar da el mismo resultado."
            )

        mensaje = opcion.get("message") or {}
        crudo = _texto_estructurado(mensaje)
        if not crudo:
            raise ErrorDeProveedor("El modelo devolvió una respuesta vacía.")

        uso = datos.get("usage") or {}
        if uso:
            logger.info(
                f"Tokens ({self.modelo.nombre}): "
                f"entrada={uso.get('prompt_tokens')} "
                f"salida={uso.get('completion_tokens')}"
            )

        return crudo

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuración
        return f"<OpenAICompatible {self.modelo.nombre} ({self.modelo.modelo})>"


def _texto_estructurado(mensaje: dict) -> str:
    """
    El JSON de la respuesta, venga por donde venga y envuelto como venga.

    Con `response_format` llega en `content`; con `tools`, dentro de los
    `arguments` de la llamada a la herramienta. Se miran los dos porque un
    proveedor puede contestar por tool-calling aunque se le haya pedido por
    `response_format`, y al revés.

    Y `content` no siempre es un string: **el formato nativo de Anthropic es una
    lista de bloques** y varios gateways la dejan pasar sin aplanar. Antes eso
    llegaba tal cual a `json.loads`, que con una lista tira `TypeError` —no
    `JSONDecodeError`—, se escapaba de todos los `except` y salía como HTTP 500.
    """
    llamadas = mensaje.get("tool_calls") or []
    if llamadas:
        crudo = (llamadas[0].get("function") or {}).get("arguments") or ""
    else:
        crudo = mensaje.get("content") or ""

    if isinstance(crudo, list):
        crudo = "".join(
            b.get("text", "") for b in crudo if isinstance(b, dict) and b.get("text")
        )
    if not isinstance(crudo, str):
        return ""

    cercado = _CERCA.match(crudo)
    return cercado.group(1) if cercado else crudo


def _mensaje_de_error(respuesta: httpx.Response) -> str:
    """
    El error del proveedor, **saneado**: solo su campo `error.message`.

    Hay una tensión real acá y esto la resuelve por el lado seguro. Por un lado,
    quien da de alta un modelo necesita leer "model not found" o "quota
    exceeded" dicho por quien sabe, no una interpretación nuestra. Por otro,
    este texto termina en la respuesta de `POST /modelos`, que **no tiene
    autenticación y acepta cualquier `base_url`**: devolver el cuerpo crudo
    convertía un SSRF a ciegas en lectura de servicios internos — apuntabas a un
    servicio de la red y el 422 te traía su respuesta.

    Devolver **solo** `error.message`, y únicamente si la respuesta trae la
    forma de error de OpenAI, conserva lo accionable y corta el eco. Un servicio
    interno que no hable ese formato no filtra nada: queda el status y listo.
    El cuerpo completo va al log, donde el operador sí puede verlo.
    """
    detalle = None
    try:
        error = respuesta.json().get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            detalle = error["message"][:300]
    except Exception:
        pass

    if detalle is None:
        logger.warning(
            f"Respuesta de error sin forma OpenAI desde {respuesta.request.url}: "
            f"HTTP {respuesta.status_code}: {respuesta.text[:500]}"
        )
        return (
            f"HTTP {respuesta.status_code}. El cuerpo de la respuesta no tiene la "
            f"forma de error de OpenAI; quedó en el log del motor."
        )
    return f"HTTP {respuesta.status_code}: {detalle}"
