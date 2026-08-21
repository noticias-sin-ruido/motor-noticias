from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


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

    **Si no hay ninguna fila activa, la síntesis usa el camino histórico de
    Gemini tal cual está.** Eso no es un descuido: es la red de seguridad. Una
    base sin filas se comporta exactamente como antes de que esta tabla
    existiera, así que el desacoplamiento no puede romper lo que ya funciona.
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
    # Y el nombre tampoco es libre: tiene que empezar con el prefijo reservado
    # (ver `services/proveedores`). Sin esa restricción, alguien podría crear una
    # fila con `base_url` apuntando a un servidor propio y `api_key_env` en
    # `WEBHOOK_SECRET` o `DATABASE_URL`, y el motor le mandaría ese valor como
    # Bearer token. Son dos campos inofensivos por separado que juntos arman una
    # primitiva de exfiltración.
    api_key_env: str

    # Lo descubre el sondeo, no el operador. Ver `ModoEstructura`.
    modo_estructura: ModoEstructura = Field(default=ModoEstructura.RESPONSE_FORMAT)

    temperatura: float = Field(default=0.3)

    # Techo de tokens de salida. **`None` = sin techo, y ese es el default a
    # propósito**: un límite arbitrario corta la síntesis a mitad, el JSON queda
    # partido y se reintenta tres veces lo mismo, porque la causa no es
    # transitoria. Existe igual porque el adaptador nuevo no tiene ninguna
    # palanca de costo —el camino histórico sí, vía `thinking_level`— y el gasto
    # en APIs es un límite duro del proyecto. Quien lo configure está eligiendo
    # acotar el gasto sabiendo el riesgo; el corte se detecta y se avisa con
    # todas las letras en vez de fallar de forma opaca.
    max_tokens: Optional[int] = Field(default=None)

    activo: bool = Field(default=False, index=True)

    # Menor primero. Hoy solo desempata cuál se usa; deja lista la cadena de
    # fallback —si un proveedor pega contra su rate limit, cae al siguiente—,
    # que es la salida natural para el punto 6 del backlog.
    prioridad: int = Field(default=100)
