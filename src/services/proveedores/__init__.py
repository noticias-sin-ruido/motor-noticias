"""
Los proveedores de IA que el motor sabe usar para sintetizar.

**Por qué existe este paquete.** Hasta la etapa 4 del backlog el motor hablaba
Gemini directo, y eso obligaba a todo el que desplegara el motor a usar Gemini —
con su cuenta, sus términos y su tier. El objetivo de acá es que cada operador
use **el modelo que paga, aquel donde tiene créditos, o uno local que no manda
nada afuera**. Ver specs/roadmap.md, backlog punto 2.

El contrato con el resto del motor es angosto y ya existía de hecho:

    (prompt: str) -> RespuestaSintesis

Todo lo caro y lo pensado —el prompt, el esquema, las validaciones
anti-alucinación, la lógica de clusters— es agnóstico y no se toca. Lo que
cambia entre proveedores son unas 50 líneas: cómo se autentica, cómo se le pide
el JSON estructurado y cómo dice que bloqueó la respuesta.

**Lo que NO se puede hacer acá**: cargar adaptadores desde la base. El registro
es un diccionario en código y `Adaptador` es un enum cerrado. Guardar una ruta de
import en una fila sería dejar que quien escriba en la base elija qué se ejecuta
en el proceso, y la API todavía no tiene autenticación.
"""
from typing import Callable, Dict

from ...models import Adaptador
from .base import (
    PREFIJO_API_KEY_ENV,
    REDES_PROHIBIDAS,
    AdaptadorNoImplementado,
    ErrorDeProveedor,
    Proveedor,
    ProveedorNoConfigurado,
    RespuestaBloqueada,
    leer_api_key,
    validar_base_url,
)
from .openai_compatible import OpenAICompatible

# Un adaptador por protocolo, no por proveedor. En la etapa 1 solo entra el
# compatible con OpenAI, que es el que cubre casi todo; `gemini` y `anthropic`
# nativos llegan en las etapas 2 y 3 y hasta entonces sus valores del enum se
# rechazan con un mensaje explícito en vez de fallar con un KeyError.
REGISTRO: Dict[Adaptador, Callable[..., Proveedor]] = {
    Adaptador.OPENAI_COMPATIBLE: OpenAICompatible,
}

__all__ = [
    "REGISTRO",
    "PREFIJO_API_KEY_ENV",
    "REDES_PROHIBIDAS",
    "AdaptadorNoImplementado",
    "ErrorDeProveedor",
    "Proveedor",
    "ProveedorNoConfigurado",
    "RespuestaBloqueada",
    "leer_api_key",
    "validar_base_url",
    "OpenAICompatible",
]
