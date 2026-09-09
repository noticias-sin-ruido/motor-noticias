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
# devuelve es si el servicio vive y si la base responde.
#
# **Qué revela, dicho con precisión.** Hasta el punto 14 acá decía "inútil para
# un atacante", y era exacto. Ahora `GET /` devuelve además `exige_token`, que le
# dice a cualquiera **sin credencial** si esta instancia tiene el candado puesto
# — justo lo que busca quien escanea por instancias abiertas.
#
# Se aceptó igual, y el motivo es que el dato ya era obtenible: alcanzaba con
# pegarle a cualquier endpoint protegido y ver si contesta 401. Lo que cambia es
# el costo de averiguarlo, de un intento de escritura —con su rastro en el log— a
# un GET sin efectos. Es una degradación de higiene de reconocimiento, no una
# fuga; a cambio, quien instala la cabina contra un motor con la API abierta deja
# de tener que inventar un token para pasar de la primera pantalla.
#
# **Lo que sí sería una fuga y no pasa**: la URL del destino de entrega no sale
# de acá, ni recortada. Sólo el booleano `entrega_configurada`. Lo fija
# `tests/test_api.py::test_la_salud_no_filtra_la_url_del_destino`.
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


def exigir_token_estricto(authorization: Optional[str] = Header(default=None)) -> None:
    """
    Como `exigir_token`, pero **sin la salida de emergencia**: acá el token no
    es opcional aunque el despliegue haya elegido dejar la API abierta.

    La regla general de este módulo —`API_TOKEN` sin definir, API abierta— es
    razonable para endpoints que leen o que disparan trabajo propio. Deja de
    serlo para los que **redirigen la salida del motor**: quien cambie la URL de
    entrega hace que las síntesis firmadas lleguen a donde él diga, y una firma
    válida es justamente lo que hace que el receptor las trate como legítimas.
    No hay despliegue tan chico como para que eso pueda quedar sin credencial.

    Es la misma clase de excepción que el punto 9 del backlog dejó anotada, y por
    eso se resuelve con una dependencia declarada ruta por ruta en vez de con una
    lista: una lista de rutas estrictas al lado de `RUTAS_ABIERTAS` obligaría a
    leer dos listas para saber qué protege qué, y el modo de fallo sería que una
    ruta nueva nazca laxa. Declararla en el decorador la hace evidente donde se
    define el endpoint.

    **503 y no 403.** No es que falte permiso: falta configuración *del servidor*,
    y quien la puede arreglar es quien lo despliega, no quien llama. El mensaje
    dice exactamente qué hacer.
    """
    if not hay_token():
        raise HTTPException(
            status_code=503,
            detail=(
                "Este endpoint cambia a dónde el motor entrega las síntesis "
                "firmadas, así que exige token aunque el resto de la API esté "
                "abierta. Definí API_TOKEN en el entorno del motor y reiniciá el "
                "contenedor."
            ),
        )
    exigir_token(authorization)
