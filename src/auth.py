"""
Token de operador para la API. **Opcional, y esa es la decisión de diseño.**

Los once endpoints del motor son todos del operador: el back-end recibe las
síntesis por push y no consulta nada, así que **nadie más consume esta API**.
Eso hace que protegerla entera no rompa ninguna integración.

Pero el motor es software que otros despliegan, y cómo lo exponen es decisión
suya: quien lo corre en su notebook para probarlo no debería pelearse con un
token, y quien lo pone en un VPS con IP pública necesita uno sí o sí. Por eso la
regla es una sola y la elige el operador:

    `API_TOKEN` definido  -> se exige en todos los endpoints menos la salud
    `API_TOKEN` sin definir -> la API queda abierta, y el motor lo avisa al arrancar

No hay comportamiento que dependa del entorno ni modos a medias. Un solo
interruptor, del lado de quien despliega.

**Qué protege esto y qué no.** Cierra el acceso a los endpoints; no reemplaza a
un firewall ni al TLS. El token viaja en texto plano si la API se expone por
HTTP sin proxy adelante, así que en producción va detrás de HTTPS.
"""
import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException

from .config import settings

logger = logging.getLogger(__name__)

# Rutas que nunca piden token.
#
# `/` es el healthcheck y **lo llama Docker desde adentro del contenedor**
# (`curl -f http://localhost:8000/` en el `HEALTHCHECK` del Dockerfile). Pedirle
# token lo rompería, o forzaría a meter la credencial en el `Dockerfile`. Lo que
# devuelve es si el servicio vive y si la base responde: útil para un
# orquestador, inútil para un atacante.
#
# Las de documentación se dejan abiertas a propósito: exponen la **forma** de la
# API, no sus datos, y este repo existe también para ser leído.
RUTAS_ABIERTAS = frozenset({"/", "/docs", "/redoc", "/openapi.json"})


def hay_token() -> bool:
    return bool(settings.API_TOKEN)


def avisar_si_esta_abierta() -> None:
    """
    Deja dicho en el arranque si la API quedó sin token.

    Se avisa **una vez, y fuerte**. Un despliegue sin token es una decisión
    válida —una notebook, una red privada— pero tiene que ser una decisión y no
    un descuido, y la diferencia entre las dos cosas es que alguien lo haya
    leído.
    """
    if hay_token():
        logger.info("API con token de operador: los endpoints exigen Authorization.")
        return

    logger.warning(
        "API SIN TOKEN: cualquiera que alcance el puerto puede dar de alta "
        "modelos, disparar síntesis (que cuestan plata) y hacer que el motor "
        "salga a buscar los feeds con tu identidad. Está bien para una red "
        "privada; si la exponés, definí API_TOKEN en el entorno."
    )


def exigir_token(authorization: Optional[str] = Header(default=None)) -> None:
    """
    Comprueba el token, si es que hay uno configurado.

    Se compara con `hmac.compare_digest` y no con `==`: la comparación de
    strings de Python corta en el primer byte distinto, así que el tiempo de
    respuesta filtra cuántos caracteres del token acertó quien prueba. Es el
    ataque de libro contra un secreto comparado ingenuamente, y evitarlo cuesta
    una función.
    """
    if not hay_token():
        return

    esperado = f"Bearer {settings.API_TOKEN}"
    if not authorization or not hmac.compare_digest(authorization, esperado):
        # 401 y no 403: lo que falta es la credencial, no el permiso. El detalle
        # no dice si el token vino mal o no vino — desde afuera son el mismo
        # problema, y distinguirlos solo le sirve a quien está probando.
        raise HTTPException(
            status_code=401,
            detail="Falta el token de operador o no es válido. Mandalo como "
                   "`Authorization: Bearer <API_TOKEN>`.",
            headers={"WWW-Authenticate": "Bearer"},
        )
