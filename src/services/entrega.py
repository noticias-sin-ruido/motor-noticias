"""
La configuración del destino de entrega: leerla, validarla y cambiarla.

Separado de `webhook_delivery.py`, que es el mecanismo. Acá vive **qué destino**
y allá **cómo se entrega**; el que cambia la URL desde la API no necesita saber
nada de firmas ni de reintentos.

Ver specs/roadmap.md, punto 11.
"""
import ipaddress
import logging
from typing import Optional

from sqlmodel import Session

from ..models import ConfiguracionEntrega
from ..models.entrega import FILA_UNICA
from ..tiempo import ahora_utc
from .medios import FeedInservible, _direcciones_efectivas, _validar_forma_de_url
from .proveedores.base import _resolver

logger = logging.getLogger(__name__)

# Rangos que el destino de entrega no puede alcanzar nunca.
#
# **Sólo link-local y NAT64: la red privada SÍ está permitida, y es una decisión
# medida, no un olvido.** El plan de este punto decía reusar
# `medios.REDES_PROHIBIDAS`, que bloquea todo lo interno, con el argumento de que
# "acá no hay caso legítimo". Es falso: el destino real de este despliegue es
# `http://localhost:3011`, o sea que la regla habría rechazado la única
# configuración que el motor tuvo alguna vez, y la migración habría sembrado una
# fila que el endpoint nunca aceptaría volver a guardar.
#
# **El razonamiento** —no el mecanismo— sale de
# `proveedores.base._exigir_host_declarado`: *"la regla queda invertida respecto
# de `services/medios.py` —allá lo interno es lo sospechoso— porque lo que se
# protege es otra cosa"*. Allá, que no nos usen de escáner de la red interna;
# acá, que el producto firmado no se vaya lejos. Un back-end en la misma máquina
# es el caso donde las síntesis no salen de la máquina.
#
# **Lo que sí se evaluó y se descartó es su mecanismo**: exigir que un destino
# PÚBLICO figure en una lista del entorno, como aquella función hace con
# `MODELO_HOSTS_PERMITIDOS`. Protege más —nadie desvía el producto firmado a un
# dominio de afuera sin tocar el servidor— y se descartó igual, porque obligaría
# a editar el `.env` y reiniciar el contenedor para cambiar la URL a un dominio
# público, que es exactamente la fricción que el punto 11 existe para sacar.
# Queda anotado acá y no perdido: si algún día este motor se despliega para
# terceros, es la primera pieza a reconsiderar.
#
# `169.254.0.0/16` en cambio no tiene ningún uso legítimo como back-end y es
# donde viven los metadata de las nubes, que reparten credenciales.
# `64:ff9b::/96` es NAT64: encapsula una IPv4 y hay que desenvolverlo.
#
# **Qué NO cierra esto, y hay que decirlo.** Quien tenga el token de operador
# puede apuntar la entrega a la red interna y usar `POST /deliver` como sonda
# ciega: el conteo de `rechazadas` contra `fallidas` distingue "hay algo
# escuchando ahí" de "no hay nada". Se asume a cambio de que el operador pueda
# entregarle a su propio back-end, y lo que sostiene la defensa es que estos
# endpoints **exigen token siempre** (ver `auth.exigir_token_estricto`), incluso
# en un despliegue que dejó la API abierta.
REDES_PROHIBIDAS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("64:ff9b::/96"),
)


class DestinoInvalido(Exception):
    """
    La URL de entrega no sirve como destino.

    Se distingue de `FeedInservible` —de la que reusa las comprobaciones de
    forma— porque el criterio de fondo es distinto: un feed tiene que estar en
    internet, un back-end puede estar en la máquina de al lado.
    """


def validar_url_de_entrega(url: str) -> str:
    """
    La URL de entrega normalizada, o levanta explicando qué tiene de malo.

    Cuatro comprobaciones. Las tres primeras salen de
    `medios._validar_forma_de_url`, **reusada y no copiada**: esquema http/https
    (sin esto entran `file://` y compañía), dominio presente, y sin credenciales
    embebidas (`https://usuario:clave@host`, que además es una segunda puerta
    para meter un secreto en la base). La cuarta es `REDES_PROHIBIDAS`.

    **No comprueba que el destino responda.** Es deliberado: el back-end puede
    estar apagado cuando se lo configura —de hecho es lo normal si todavía no
    existe— y hacer que la configuración dependa de que el otro lado esté vivo
    convierte un cambio de ajustes en una carrera. Lo que pasa si no responde ya
    está resuelto: la síntesis queda pendiente y se reintenta.

    **No cierra el TOCTOU.** Entre esta resolución de DNS y el POST real hay una
    segunda, así que un dominio que cambie de respuesta se lo saltea. Es el
    mismo límite que `proveedores.base` y `medios` ya asumen y documentan.
    """
    try:
        limpia, partes = _validar_forma_de_url(url, "La URL de entrega")
    except FeedInservible as error:
        raise DestinoInvalido(str(error)) from error

    for direccion in _resolver(partes.hostname):
        # `_direcciones_efectivas` desenvuelve la IPv4 que puede venir escondida
        # adentro de una IPv6. **Sin eso el filtro se evade con una línea**:
        # `::ffff:169.254.169.254` apunta a los metadata pero como objeto es un
        # `IPv6Address`, y no pertenece a ninguna red IPv4. Está verificado en
        # `medios._direcciones_efectivas` que dentro de `python:3.12-slim` —el
        # destino real de despliegue— esa forma conecta.
        for efectiva in _direcciones_efectivas(direccion):
            if any(efectiva in red for red in REDES_PROHIBIDAS):
                comose = "" if efectiva == direccion else f" (via {direccion})"
                raise DestinoInvalido(
                    f"La URL de entrega apunta a una dirección link-local "
                    f"({efectiva}){comose}, que es donde viven los metadata de "
                    f"las nubes. Un back-end en localhost o en la red interna sí "
                    f"está permitido. Ver `REDES_PROHIBIDAS` en services/entrega.py."
                )

    return limpia


def configuracion(session: Session) -> ConfiguracionEntrega:
    """
    La fila de configuración, creándola vacía si no existe.

    Se crea al vuelo en vez de asumir que la migración la dejó: la app corre
    también contra bases de test creadas con `SQLModel.metadata.create_all`, que
    no ejecuta migraciones. Sin esto, cada lector tendría que manejar el `None`.
    """
    fila = session.get(ConfiguracionEntrega, FILA_UNICA)
    if fila is None:
        fila = ConfiguracionEntrega(id=FILA_UNICA, url=None)
        session.add(fila)
        session.commit()
        session.refresh(fila)
    return fila


def url_de_entrega(session: Session) -> Optional[str]:
    """A dónde entregar, o `None` si no hay destino configurado."""
    return configuracion(session).url or None


def guardar_url(session: Session, url: Optional[str]) -> ConfiguracionEntrega:
    """
    Cambia el destino. **Sólo `None` lo desconfigura**, y la entrega deja de correr.

    **No toca `Sintesis.enviado_backend`, y eso es la decisión de fondo del
    punto.** La alternativa —marcar todo como no enviado para que el destino
    nuevo reciba el histórico— se evaluó y se descartó: son cientos de síntesis
    firmadas saliendo de golpe hacia un back-end que quizás recién se está
    levantando, disparadas por lo que para quien lo hace es "corregir un tipeo en
    la URL". Un reenvío masivo tiene que ser una acción con ese nombre, no el
    efecto secundario de otra. Ya existe: `POST /deliver?forzar=true`.

    El destino nuevo recibe **desde la próxima síntesis**.
    """
    # **La cadena vacía NO borra: levanta, igual que cualquier otra URL que no
    # sirve.** Antes sí borraba, y eso dejaba abierta por otra puerta la misma
    # clase de accidente que `CambioEntrega.url` cierra al ser obligatorio: `{}`
    # daba 422 y protegía el destino, pero `{"url": ""}` daba 200 y lo apagaba —
    # que es exactamente lo que manda un formulario con el campo vaciado.
    # Verificado antes del arreglo: 200 y el destino en `None`.
    #
    # Borrar el destino apaga la entrega, así que tiene que costar decirlo:
    # `null` explícito y nada más.
    limpia = None if url is None else validar_url_de_entrega(url)

    fila = configuracion(session)
    anterior = fila.url
    fila.url = limpia
    fila.actualizado_en = ahora_utc()
    session.add(fila)
    session.commit()
    session.refresh(fila)

    # Se registra que cambió y no a qué cambió: el log va a archivo y a stdout,
    # y la URL de entrega es de las cosas que este punto decidió no repartir.
    logger.info(
        "Destino de entrega %s (antes %s)",
        "configurado" if limpia else "borrado",
        "había uno" if anterior else "no había",
    )
    return fila
