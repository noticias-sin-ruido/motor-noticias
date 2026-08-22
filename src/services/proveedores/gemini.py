"""
El adaptador nativo de Gemini, vía el SDK `google-genai`.

**Por qué existe habiendo uno compatible con OpenAI.** Gemini expone su propia
capa compatible, así que se lo puede usar con `OpenAICompatible` y un `base_url`
— y de hecho así se lo probó. Lo que esa capa no expone es `thinking_config`, la
única palanca de costo que tenemos sobre el paso más caro del pipeline: los
tokens de razonamiento **se facturan como salida**. Medido en su momento:
MINIMAL y LOW gastan 0, MEDIUM 349, HIGH 448.

Con el gasto en APIs como límite duro del proyecto, perder esa palanca al
desacoplar habría sido pagar el desacoplamiento con dinero. Por eso el nativo.
"""
import json
import logging
from typing import Optional, Type

from pydantic import BaseModel

from ...models import ModeloIA, ModoEstructura
from .base import (
    ErrorDeProveedor,
    RespuestaBloqueada,
    leer_api_key,
    validar_opciones as _validar_claves,
)

logger = logging.getLogger(__name__)

# Mismo criterio que el adaptador compatible: no se está bajando un HTML sino
# esperando a que un modelo razone y escriba una síntesis entera.
TIMEOUT_SEGUNDOS = 120.0

# El del sondeo **no se define acá**: `services/modelos.sondear` usa uno solo
# para todos los adaptadores. Tener una copia propia con el mismo valor era
# una trampa — el día que una cambiara, la otra se lo comía sin avisar.

# Las palancas que este adaptador acepta en `ModeloIA.opciones`, **y ninguna
# más**. La lista es la llave del bolsillo: sin ella, `opciones` sería un camino
# directo desde un endpoint sin autenticación hasta el cuerpo del request.
#
# `thinking_level` gradúa el razonamiento por nivel; `thinking_budget` lo hace
# por cantidad de tokens. Son las dos formas que ofrece el SDK y se excluyen
# entre sí, pero de eso se encarga el proveedor: acá solo se controla que nadie
# meta una clave que no sea una de estas dos.
OPCIONES_ACEPTADAS = frozenset({"thinking_level", "thinking_budget"})

NIVELES_DE_RAZONAMIENTO = ("MINIMAL", "LOW", "MEDIUM", "HIGH")

# **Un solo mecanismo, y por eso se declara.** `sondear` prueba los modos que el
# adaptador dice soportar; sin esta lista probaría también `TOOLS`, fallaría
# igual que la primera vez y le diría al operador que su proveedor no respeta el
# esquema por ninguno de los dos mecanismos — una conclusión falsa sobre un
# mecanismo que nunca se intentó de verdad.
MODOS_SOPORTADOS = (ModoEstructura.RESPONSE_FORMAT,)


def validar_opciones(opciones: Optional[dict]) -> dict:
    """
    Las opciones tal cual, o levanta diciendo qué tienen de malo.

    Dos capas: las **claves** las filtra el validador común —la llave del
    bolsillo, compartida por todos los adaptadores—, y los **valores** se miran
    acá, porque qué es un nivel de razonamiento válido solo lo sabe Gemini.
    """
    opciones = _validar_claves(opciones, OPCIONES_ACEPTADAS, "El adaptador de Gemini")

    nivel = opciones.get("thinking_level")
    if nivel is not None and str(nivel).upper() not in NIVELES_DE_RAZONAMIENTO:
        raise ErrorDeProveedor(
            f"`thinking_level` tiene que ser uno de "
            f"{list(NIVELES_DE_RAZONAMIENTO)}, y vino {nivel!r}."
        )

    presupuesto = opciones.get("thinking_budget")
    if presupuesto is not None and (
        not isinstance(presupuesto, int)
        or isinstance(presupuesto, bool)
        or presupuesto < 0
    ):
        raise ErrorDeProveedor(
            f"`thinking_budget` tiene que ser un entero de tokens >= 0, "
            f"y vino {presupuesto!r}."
        )
    return opciones


class GeminiNativo:
    """
    Gemini por su propio protocolo.

    A diferencia del adaptador compatible, **no tiene `base_url`**: el endpoint
    lo fija el SDK. Eso lo deja fuera de toda la superficie de SSRF — no hay
    destino que elegir, así que no hay nada que validar.
    """

    OPCIONES_ACEPTADAS = OPCIONES_ACEPTADAS
    MODOS_SOPORTADOS = MODOS_SOPORTADOS

    def __init__(self, modelo: ModeloIA):
        self.modelo = modelo
        # Se rechaza en vez de ignorarse, por el mismo motivo que los campos de
        # más en el alta: quien lo puso cree que el motor va a hablar con ese
        # destino, y no es así. Un silencio acá deja al operador convencido de
        # que sus datos van a un lado al que nunca fueron.
        if modelo.base_url:
            raise ErrorDeProveedor(
                "El adaptador nativo de Gemini no usa `base_url`: su endpoint "
                "lo fija el SDK. Si querés apuntar a otro destino compatible, "
                "usá el adaptador `openai_compatible`."
            )
        self.api_key = leer_api_key(modelo)
        self.opciones = validar_opciones(modelo.opciones)
        # El cliente es **de la instancia y no global**. El camino histórico
        # cacheaba uno en un módulo, y eso significaba que la key quedaba
        # congelada en el proceso: cambiar `MODELO_API_KEY` no tenía efecto
        # hasta reiniciar. Ahora el adaptador se arma por llamada y la
        # credencial se relee, que es lo que hace que cambiar de proveedor no
        # necesite un redeploy.
        from google import genai

        self._genai = genai
        self.cliente = genai.Client(api_key=self.api_key)

    def _config(self, esquema: Type[BaseModel], timeout: Optional[float]) -> dict:
        """
        La configuración del pedido.

        `response_schema` toma la clase de Pydantic directamente: es la ventaja
        del protocolo nativo, que hace el JSON válido **por construcción** en
        vez de pedirlo por convención como hace el sobre de OpenAI.
        """
        config = {
            "response_mime_type": "application/json",
            "response_schema": esquema,
            "temperature": self.modelo.temperatura,
            # El SDK lo cuenta en milisegundos, no en segundos.
            "http_options": {"timeout": int((timeout or TIMEOUT_SEGUNDOS) * 1000)},
        }

        if self.modelo.max_tokens:
            config["max_output_tokens"] = self.modelo.max_tokens

        pensamiento = {}
        if "thinking_level" in self.opciones:
            pensamiento["thinking_level"] = str(self.opciones["thinking_level"]).upper()
        if "thinking_budget" in self.opciones:
            pensamiento["thinking_budget"] = self.opciones["thinking_budget"]
        if pensamiento:
            config["thinking_config"] = pensamiento

        return config

    def generar(self, prompt: str, esquema: Type[BaseModel]) -> BaseModel:
        """El JSON del modelo, ya validado contra el esquema."""
        crudo = self.probar(prompt, esquema)
        try:
            return esquema.model_validate(json.loads(crudo))
        except json.JSONDecodeError as error:
            raise ErrorDeProveedor(
                f"El modelo no devolvió JSON válido: {error}"
            ) from error

    def probar(
        self, prompt: str, esquema: Type[BaseModel], timeout: Optional[float] = None
    ) -> str:
        """Lo mismo que `generar`, pero sin validar. Ver `Proveedor.probar`."""
        try:
            respuesta = self.cliente.models.generate_content(
                model=self.modelo.modelo,
                contents=prompt,
                config=self._config(esquema, timeout),
            )
        except self._genai.errors.APIError as error:
            # El mensaje del proveedor viaja tal cual: es lo que necesita leer
            # quien da de alta un modelo ("modelo inexistente", "cuota
            # agotada"). Acá no hace falta sanearlo como en el adaptador
            # compatible, porque el destino no lo elige quien llama: siempre es
            # Google, así que no hay servicio interno cuyo cuerpo se pueda
            # reflejar.
            raise ErrorDeProveedor(
                f"HTTP {getattr(error, 'status', '?')}: {error.message}"
            ) from error
        except Exception as error:  # red, DNS, TLS, timeout
            raise ErrorDeProveedor(
                f"No se pudo hablar con Gemini: {type(error).__name__}: {error}"
            ) from error

        return self._leer(respuesta)

    def _leer(self, respuesta) -> str:
        # La normalización que le toca a este adaptador: Gemini dice que bloqueó
        # por `finish_reason`, y río arriba solo se entiende `RespuestaBloqueada`.
        candidatos = getattr(respuesta, "candidates", None) or []
        if candidatos:
            motivo = str(getattr(candidatos[0], "finish_reason", "") or "").upper()
            if "SAFETY" in motivo or "BLOCK" in motivo or "PROHIBITED" in motivo:
                raise RespuestaBloqueada(
                    f"{self.modelo.nombre} bloqueó la respuesta ({motivo})"
                )
            # Mismo criterio que el adaptador compatible: el corte por techo de
            # tokens se detecta y se dice, porque reintentarlo da lo mismo.
            if "MAX_TOKENS" in motivo:
                raise ErrorDeProveedor(
                    f"La respuesta se cortó por `max_tokens` "
                    f"({self.modelo.max_tokens}). Subilo o sacalo: el JSON queda "
                    f"partido y reintentar da el mismo resultado."
                )

        texto = getattr(respuesta, "text", None)
        if not texto:
            raise ErrorDeProveedor("El modelo devolvió una respuesta vacía.")

        uso = getattr(respuesta, "usage_metadata", None)
        if uso is not None:
            logger.info(
                f"Tokens ({self.modelo.nombre}): "
                f"entrada={uso.prompt_token_count} "
                f"salida={uso.candidates_token_count} "
                f"razonamiento={getattr(uso, 'thoughts_token_count', None) or 0}"
            )

        return texto

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuración
        return f"<GeminiNativo {self.modelo.nombre} ({self.modelo.modelo})>"
