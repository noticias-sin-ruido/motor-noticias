from enum import Enum
from typing import Any, Dict, Optional

from sqlalchemy import JSON, Column, text
from sqlmodel import Field, SQLModel

# La variable de entorno donde va la credencial del proveedor. **Una sola, con
# nombre fijo**, y el operador no la elige ni la escribe en ninguna fila.
#
# Se llegó acá descartando la alternativa: un nombre por proveedor
# (`MODELO_API_KEY_GEMINI`, `..._GROQ`) hacía que el `.env` creciera para
# siempre a cambio de sostener una función que no existe —tener dos proveedores
# vivos a la vez para saltar de uno a otro—, y esa función quedó explícitamente
# diferida a su propio punto del backlog. Cambiar de modelo es raro; cuando
# pasa, se cambia **el valor** de esta variable y listo.
#
# La contrapartida está asumida y tiene su defensa: como el nombre ya no
# distingue proveedores, que la variable exista dejó de ser evidencia de que sea
# la credencial correcta. Por eso activar un modelo **sondea contra el
# proveedor** en vez de mirar el entorno. Ver `main.activar_modelo`.
VARIABLE_UNICA = "MODELO_API_KEY"

# La forma con sufijo sigue siendo válida —`MODELO_API_KEY_GROQ`— aunque hoy
# nada la use: es lo que va a necesitar el punto de multimodelo, y admitirla
# cuesta una condición en `leer_api_key`. Lo que **no** hace es aparecer en el
# alta: `POST /modelos` ya no acepta `api_key_env`, así que mientras ese punto
# no se implemente, todas las filas nacen con `VARIABLE_UNICA`.
PREFIJO_API_KEY_ENV = "MODELO_API_KEY_"


class Adaptador(str, Enum):
    """
    Los protocolos que el motor sabe hablar. **Es un enum cerrado a propósito.**

    La tentación al leer "que cada operador use el modelo que quiera" es guardar
    en la base la ruta de import del adaptador. Eso es **ejecución remota de
    código**: quien pueda escribir una fila elige qué se ejecuta dentro del
    proceso. Y hoy la API no tiene autenticación en ninguno de sus endpoints, así
    que "quien pueda escribir una fila" es cualquiera que alcance el puerto.

    No hace falta, además. `OPENAI_COMPATIBLE` no es un proveedor sino **un
    protocolo**, y es el estándar de hecho: OpenAI, Azure, OpenRouter, Groq,
    Together, DeepSeek, Mistral, xAI, vLLM, LM Studio, Ollama y el propio Gemini
    lo exponen. Cientos de modelos entran cambiando `base_url`, sin código nuevo.

    Un proveedor que no hable ninguno de estos se resuelve poniéndole un gateway
    adelante (LiteLLM, OpenRouter) que traduzca, no agregando un adaptador.
    """

    OPENAI_COMPATIBLE = "openai_compatible"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"


class ModoEstructura(str, Enum):
    """
    Cómo se le pide a este proveedor que devuelva JSON con nuestra forma.

    No lo declara el operador: **lo descubre el sondeo del alta**. Existe porque
    "compatible con OpenAI" no garantiza que `response_format` funcione —
    verificado el 20/08/2026 contra la documentación de Anthropic, cuya capa de
    compatibilidad lo **ignora en silencio**: responde 200, devuelve texto
    correcto, y descarta el esquema. Un alta que solo comprobara "¿contesta?"
    habría aceptado ese modelo y el fallo habría aparecido recién en la síntesis.

    `TOOLS` es el plan B y es más portable de lo que parece: muchos servidores
    compatibles soportan tool-calling sin soportar `response_format`.
    """

    RESPONSE_FORMAT = "response_format"
    TOOLS = "tools"


class ModeloIA(SQLModel, table=True):
    """
    Un modelo de IA que este despliegue puede usar para sintetizar.

    Existe para que **el operador de la instancia elija con qué modelo trabaja**
    —el que paga, aquel donde tiene créditos, o uno local que no manda nada
    afuera— sin editar código ni redeployar. Ver specs/roadmap.md, backlog
    punto 2.

    **Sin ninguna fila activa la síntesis no corre.** Hasta la etapa 3 del punto
    2 ese caso caía a un camino histórico que hablaba Gemini directo; se retiró
    en la 4, porque mientras existiera el motor tenía un proveedor privilegiado
    escondido en el código —justo lo que este punto venía a sacar— y encima era
    la única llamada del pipeline sin timeout.

    A los despliegues que ya existían la migración `5f80e67d5404` les convierte
    su configuración de entorno en una fila, **apagada**: la regla es que
    ninguna migración elige proveedor. Prenderla es un `PATCH` explícito.

    Y **hay como mucho una activa**: prender un modelo apaga a los demás (ver
    `main._apagar_los_demas`). Con una sola credencial, dos prendidos es un
    estado que no se puede usar.
    """

    __tablename__ = "modelo_ia"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Nombre que le pone el operador, para reconocerlo entre varios. No es el
    # nombre del modelo: puede haber dos filas del mismo modelo con distinta
    # temperatura o distinta cuenta.
    nombre: str = Field(index=True, unique=True)

    adaptador: Adaptador

    # El identificador que entiende el proveedor: "gpt-4o", "claude-opus-5",
    # "llama3.1:70b". **No se valida contra ninguna lista nuestra**, y es
    # deliberado: mantenerla sería una cinta de correr y bloquearía cada modelo
    # nuevo el día que sale. Quien valida el nombre es el proveedor, y el sondeo
    # del alta devuelve su mensaje de error tal cual.
    modelo: str

    # Solo para `OPENAI_COMPATIBLE`, donde **es lo que identifica al proveedor**
    # (api.openai.com, openrouter.ai, localhost:11434/v1...). Los otros dos
    # adaptadores tienen endpoint fijo y lo dejan en None.
    base_url: Optional[str] = Field(default=None)

    # **El NOMBRE de la variable de entorno con la key, nunca la key.** La base
    # se respalda, se dumpea y se lee desde endpoints sin autenticación; una
    # credencial ahí adentro se filtra sola.
    #
    # Hoy vale siempre `VARIABLE_UNICA` y **el alta ni siquiera lo acepta**: la
    # columna existe para el punto de multimodelo, que es el único escenario que
    # necesita nombres distintos. Que no sea configurable es lo que cierra la
    # primitiva de exfiltración que abría de par en par: con `base_url` propio y
    # `api_key_env` en `WEBHOOK_SECRET` o `DATABASE_URL`, el motor mandaba ese
    # valor como Bearer token. `leer_api_key` sigue validando el nombre igual,
    # como segunda barrera para el día que el campo vuelva a ser configurable.
    api_key_env: str = Field(default=VARIABLE_UNICA)

    # Lo descubre el sondeo, no el operador. Ver `ModoEstructura`.
    modo_estructura: ModoEstructura = Field(default=ModoEstructura.RESPONSE_FORMAT)

    temperatura: float = Field(default=0.3)

    # Techo de tokens de salida. **`None` = sin techo, y ese es el default a
    # propósito**: un límite arbitrario corta la síntesis a mitad, el JSON queda
    # partido y se reintenta tres veces lo mismo, porque la causa no es
    # transitoria. Existe igual porque el gasto en APIs es un límite duro del
    # proyecto y no todo proveedor ofrece una palanca propia: Gemini gradúa el
    # razonamiento vía `opciones`, pero la mayoría no tiene equivalente y este
    # techo es lo único que les queda. Quien lo configure está eligiendo
    # acotar el gasto sabiendo el riesgo; el corte se detecta y se avisa con
    # todas las letras en vez de fallar de forma opaca.
    max_tokens: Optional[int] = Field(default=None)

    # Palancas que solo entiende el adaptador de este modelo. Hoy la única es
    # `thinking_level` en Gemini, que gradúa cuántos tokens de razonamiento se
    # gastan **y se factura como salida**, o sea la parte cara del paso más caro
    # del pipeline. No hay equivalente fuera de Gemini, así que ponerle una
    # columna propia habría dejado un campo muerto para todos los demás; con
    # Anthropic entrando en la etapa 3 y su `thinking.budget_tokens`, serían dos.
    #
    # **Es un bolsillo con llave, no un pasamanos.** Cada adaptador declara qué
    # claves acepta (`OPCIONES_ACEPTADAS`) y el alta rechaza cualquier otra. Sin
    # esa lista, `opciones` sería un camino directo desde un endpoint sin
    # autenticación hasta el cuerpo del request que sale hacia el proveedor.
    opciones: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )

    activo: bool = Field(default=False, index=True)

    # Menor primero. Hoy solo desempata cuál se usa; deja lista la cadena de
    # fallback —si un proveedor pega contra su rate limit, cae al siguiente—,
    # que es la salida natural para el punto 6 del backlog.
    prioridad: int = Field(default=100)
