"""
El contrato que cumple todo proveedor, y la lectura segura de credenciales.
"""
import ipaddress
import logging
import os
import socket
from typing import Optional, Protocol
from urllib.parse import urlparse

from dotenv import dotenv_values

from ...models import ModeloIA
from ...models.modelo_ia import PREFIJO_API_KEY_ENV, VARIABLE_UNICA

logger = logging.getLogger(__name__)

# `PREFIJO_API_KEY_ENV` y `VARIABLE_UNICA` se definen junto al modelo —son parte
# de la forma del dato— y se re-exportan acá porque es donde se aplican.
#
# **Alcance exacto de lo que protegen, porque es fácil atribuirles de más.**
#
# Lo que SÍ hacen: impedir que una fila nombre una variable ajena. Sin la regla,
# `api_key_env: "WEBHOOK_SECRET"` más un `base_url` propio hacen que el motor le
# mande ese secreto a quien lo pida, como Bearer token. Hoy la primera barrera
# es más fuerte todavía: el alta no acepta el campo.
#
# Lo que NO hacen: impedir que la key **de IA** salga hacia un destino elegido
# por quien llama. Quien pueda dar de alta un modelo puede apuntar `base_url` a
# su propio servidor, y el sondeo le entrega la credencial igual. Eso **solo lo
# cierra la autenticación**; hasta entonces la mitigación es no publicar
# `base_url` en las respuestas (ver `main.py`) y desplegar la API donde solo
# llegue el operador. Ver specs/roadmap.md, punto 9.

# Rangos que `base_url` no puede alcanzar nunca.
#
# **Solo link-local**, y es una decisión con costo asumido. Bloquear todas las
# direcciones privadas sería lo habitual contra SSRF, pero rompería justo el
# caso que motiva este backlog: un modelo local en `localhost:11434` o un vLLM
# en la red interna es el escenario donde los cuerpos de los artículos **no
# salen de la máquina**. `169.254.0.0/16` en cambio no tiene ningún uso legítimo
# acá y es donde viven los metadata de las nubes, que reparten credenciales.
#
# No es hermético: entre esta comprobación y el request real hay una segunda
# resolución de DNS, así que un dominio que cambie de respuesta se lo saltea.
# Cierra el caso directo, no al atacante decidido; para eso hace falta auth.
REDES_PROHIBIDAS = (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fe80::/10"))


class ErrorDeProveedor(Exception):
    """
    El proveedor respondió, pero no sirve.

    Se distingue de una excepción de red cualquiera porque lleva el mensaje del
    proveedor tal cual: cuando un alta falla, lo que necesita ver el operador es
    "modelo inexistente" o "cuota agotada" dicho por quien sabe, no una
    traducción nuestra.
    """


class ProveedorNoConfigurado(Exception):
    """
    Falta la credencial, o el `api_key_env` no cumple la convención.

    **No hereda de `ErrorDeProveedor` a propósito**: aquél significa "el
    proveedor falló y quizá ande en el intento siguiente", y éste significa
    "está mal configurado y va a fallar igual las veces que lo intentes". Río
    arriba, esa diferencia decide si `tenacity` reintenta o no.
    """


class AdaptadorNoImplementado(ErrorDeProveedor):
    """
    El adaptador que pide la fila todavía no existe en el registro.

    Hereda de `ErrorDeProveedor` porque para el alta es un error del proveedor
    como cualquier otro, pero se distingue para que la síntesis **no lo
    reintente**: que falte un adaptador no cambia entre un intento y el
    siguiente.
    """


class RespuestaBloqueada(Exception):
    """
    El proveedor se negó a responder por sus filtros de contenido.

    Vive acá y no en `synthesis` para no invertir la dependencia: los
    adaptadores no importan nada de la capa de síntesis, así que se los puede
    probar y reusar sueltos. `llamar_modelo` la traduce a `SintesisBloqueada` en
    la frontera, que es el vocabulario que ya entiende `sintetizar_pendientes`
    para no reintentar lo que va a volver a fallar igual.
    """


# El nombre que llevaba la credencial de Gemini antes de que el modelo fuera
# configurable. Se sigue leyendo como **último recurso** para que actualizar el
# motor no corte la síntesis de un día para el otro en un despliegue que ya
# existe: el `.env` de esa instancia dice `GEMINI_API_KEY` y nadie le avisó.
#
# Es deuda con fecha, no una segunda forma válida: cada lectura por esta vía
# loguea un aviso, y se saca cuando el punto 2 quede cerrado y difundido.
VARIABLE_HEREDADA = "GEMINI_API_KEY"


def _del_entorno(nombre: str) -> Optional[str]:
    """
    El valor de una variable, del entorno del proceso o del `.env`.

    **Los dos, y en ese orden.** `pydantic-settings` lee el `.env` para llenar
    los campos que `Settings` declara, pero **no lo vuelca a `os.environ`**:
    verificado, una variable puesta ahí es invisible para `os.environ.get`. Como
    `MODELO_API_KEY` no es un campo de `Settings`, sin esto el `.env` —que es
    donde cualquiera la pondría, y donde `.env.example` dice que va— simplemente
    no funcionaba.

    El entorno real gana sobre el archivo, que es la precedencia que espera
    cualquiera: en producción la credencial la inyecta Docker o el orquestador,
    y un `.env` olvidado en la imagen no debe pisarla.
    """
    del_proceso = os.environ.get(nombre)
    if del_proceso:
        return del_proceso
    return dotenv_values(".env").get(nombre)


def _heredada() -> Optional[str]:
    """
    La credencial vieja de Gemini, si es que está y la nueva no.

    Existe para que actualizar no rompa: ver `VARIABLE_HEREDADA`. Avisa cada vez
    porque un fallback silencioso es indistinguible de una configuración
    correcta, y entonces nadie migra nunca.
    """
    valor = _del_entorno(VARIABLE_HEREDADA)
    if valor and not valor.startswith("tu_"):
        logger.warning(
            f"Usando {VARIABLE_HEREDADA} porque {VARIABLE_UNICA} no está "
            f"definida. Es compatibilidad temporal: renombrá la variable en tu "
            f"`.env`, que ahora la credencial es una sola para el proveedor que "
            f"uses, sea cual sea."
        )
        return valor
    return None


def leer_api_key(modelo: ModeloIA) -> str:
    """
    La credencial de este modelo, leída del entorno o del `.env`.

    Nunca de la base: se respalda, se dumpea y se lee desde endpoints que todavía
    no tienen autenticación. Ver `models/modelo_ia.py`.
    """
    nombre = modelo.api_key_env or ""

    # Dos formas válidas y ninguna libre: el nombre único, que es el que usan
    # todas las filas hoy, o la forma con sufijo que va a necesitar multimodelo.
    # `MODELO_API_KEYX` no entra por ninguna de las dos — el borde importa, es
    # lo que separa "empieza parecido" de "es una de las nuestras".
    if nombre != VARIABLE_UNICA and not nombre.startswith(PREFIJO_API_KEY_ENV):
        raise ProveedorNoConfigurado(
            f"`api_key_env` tiene que ser {VARIABLE_UNICA!r} o empezar con "
            f"{PREFIJO_API_KEY_ENV!r}, y vino {nombre!r}. Es la restricción que "
            f"impide que una fila pueda nombrar cualquier variable del entorno."
        )

    valor = _del_entorno(nombre)
    if not valor and nombre == VARIABLE_UNICA:
        valor = _heredada()
    if not valor:
        raise ProveedorNoConfigurado(
            f"La variable {nombre!r} no está definida o está vacía. Definila en "
            f"el entorno del proceso o en el `.env`, nunca en la base."
        )
    return valor


def validar_opciones(opciones: Optional[dict], aceptadas, quien: str) -> dict:
    """
    Las opciones de este modelo, comprobadas contra lo que su adaptador acepta.

    **Es la llave del bolsillo.** `ModeloIA.opciones` existe para las palancas
    que solo entiende un proveedor —hoy el razonamiento de Gemini—, y sin una
    lista cerrada sería un camino directo desde un endpoint sin autenticación
    hasta el cuerpo del request que sale hacia afuera.

    Se valida **al construir el adaptador**, no al usarlo, para que una opción
    inválida sea un 422 en el alta y no una síntesis que falla cada 15 minutos
    sin que el operador sepa por qué.

    Un adaptador que no acepte ninguna opción pasa `aceptadas` vacío y con eso
    rechaza todo, que es lo correcto: guardar una opción que nadie va a leer es
    prometerle al operador un efecto que no va a ocurrir.
    """
    opciones = dict(opciones or {})
    de_mas = set(opciones) - set(aceptadas)
    if de_mas:
        disponibles = sorted(aceptadas)
        raise ErrorDeProveedor(
            f"{quien} no acepta {sorted(de_mas)} en `opciones`. "
            + (
                f"Las válidas son: {disponibles}."
                if disponibles
                else "Este adaptador no acepta ninguna opción."
            )
        )
    return opciones


def validar_base_url(url: str) -> str:
    """
    La `base_url` normalizada, o levanta explicando qué tiene de malo.

    Tres comprobaciones, cada una por un motivo distinto:

    1. **Esquema http/https.** Sin esto entran `file://`, `gopher://` y demás,
       que son la vía clásica para convertir un SSRF en lectura de archivos.
    2. **Sin credenciales embebidas** (`https://user:pass@host`). Es una segunda
       puerta para meter un secreto en la base, justo lo que `api_key_env`
       existe para evitar — y encima se devolvería en cualquier listado.
    3. **Sin direcciones link-local.** Ver `REDES_PROHIBIDAS`.
    """
    limpia = (url or "").strip().rstrip("/")
    if not limpia:
        raise ErrorDeProveedor(
            "`base_url` es obligatorio en el adaptador compatible con OpenAI: "
            "es lo que identifica al proveedor."
        )

    partes = urlparse(limpia)
    if partes.scheme not in ("http", "https"):
        raise ErrorDeProveedor(
            f"`base_url` tiene que ser http o https, y vino {partes.scheme or '(nada)'!r}."
        )
    if not partes.hostname:
        raise ErrorDeProveedor(f"`base_url` no tiene host: {limpia!r}")
    if partes.username or partes.password:
        raise ErrorDeProveedor(
            "`base_url` no puede llevar credenciales embebidas. La credencial "
            f"va en la variable de entorno {VARIABLE_UNICA}, no en la URL."
        )

    for familia in _resolver(partes.hostname):
        if any(familia in red for red in REDES_PROHIBIDAS):
            raise ErrorDeProveedor(
                f"`base_url` apunta a una dirección link-local ({familia}), que "
                f"es donde viven los metadata de las nubes. Un modelo local en "
                f"localhost o en la red interna sí está permitido."
            )
    return limpia


def _resolver(host: str):
    """
    Las direcciones IP del host. Vacío si no resuelve — de eso se encarga el
    request, que va a fallar con un mensaje mucho más claro que el nuestro.
    """
    try:
        return [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)]
    except (socket.gaierror, ValueError):
        return []


class Proveedor(Protocol):
    """
    Lo único que el motor necesita de un proveedor.

    Se expresa con `Protocol` y no con una clase base abstracta a propósito: no
    hay estado ni comportamiento que compartir entre adaptadores —cada uno habla
    su propio protocolo— y una jerarquía de herencia solo agregaría ceremonia.

    Las responsabilidades que **no** son opcionales:

    - Devolver una instancia del esquema ya validada, o levantar. El motor no
      inspecciona respuestas crudas de nadie.
    - **Normalizar el bloqueo por filtros de contenido a `RespuestaBloqueada`.**
      Cada proveedor lo dice a su manera (`finish_reason` en Gemini,
      `content_filter` en OpenAI, `stop_reason` en Anthropic) y traducirlo es
      del adaptador, porque río arriba `sintetizar_pendientes` decide con esa
      excepción si reintentar o no.
    - Conservar el mensaje de error del proveedor tal cual dentro de
      `ErrorDeProveedor`, porque es lo que lee quien da de alta un modelo.

    El esquema se recibe por parámetro y no se importa: así los adaptadores no
    dependen de `synthesis` y se pueden probar sueltos.
    """

    def generar(self, prompt: str, esquema):  # -> BaseModel
        """La respuesta validada contra el esquema."""
        ...

    def probar(self, prompt: str, esquema, timeout: Optional[float] = None) -> str:
        """
        Lo mismo pero crudo, sin validar, para el sondeo del alta.

        **`timeout` no es opcional de verdad**: `sondear` se lo pasa siempre,
        con el suyo, que es mucho más corto que el de una síntesis. Un
        adaptador que lo omitiera de su firma reventaría con `TypeError` en
        el alta. Lleva default solo para que `generar` pueda llamarlo sin
        argumentos.

        Existe porque el sondeo comprueba que el proveedor respete la **forma**
        del esquema, no que haya producido contenido que cumpla las reglas de
        negocio. Ver `services/modelos.sondear`.
        """
        ...
