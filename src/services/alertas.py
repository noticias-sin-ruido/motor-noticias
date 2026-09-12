"""
A quién le avisa el motor. **Lo elige el operador, no el `.env`.**

Punto 9 del backlog. Sigue la forma de `services/entrega`: una fila de
configuración, una sola función de escritura que valida, y las credenciales
—`SMTP_*`— quedándose en el entorno.

**Este punto no se abrió porque faltara una función, sino porque el canal nunca
se comprobó.** El motor tiene nueve puntos de llamada a `enviar_alerta`, y en el
log del contenedor no hay una sola línea de envío: ni exitoso ni fallido. La
casilla que se usaba para probar la deshabilitó su proveedor y nadie se enteró,
porque un envío fallido sólo deja un `logger.error` que nadie mira.

De ahí las dos cosas que este módulo agrega y que el backlog no pedía: **varios
destinos** y **una prueba explícita** que deja fecha. Configurar un destino no
prueba nada; mandar un mail y verlo llegar, sí.
"""

import logging
import re
from typing import List, Sequence

from sqlmodel import Session

from ..models.alertas import MAX_DESTINOS, MAX_LARGO_MAIL, FILA_UNICA, ConfiguracionAlertas
from ..tiempo import ahora_utc

logger = logging.getLogger(__name__)


class DestinoInvalido(Exception):
    """Lo que se quiso guardar no sirve como destino, y el mensaje dice por qué."""


# **No valida direcciones de correo en serio, y es a propósito.** La gramática
# real del RFC 5322 acepta cosas que ningún proveedor entrega y ninguna regexp
# corta la describe; intentarlo produce validadores que rechazan direcciones
# legítimas, que es el peor error posible acá — dejar sin avisos a quien escribió
# bien su casilla.
#
# Lo que sí se puede hacer es atajar lo que **seguro** no es una dirección: sin
# arroba, con espacios, sin punto en el dominio. El resto lo dictamina el único
# juez que importa, que es el servidor de correo — y para eso está la prueba de
# envío.
_FORMA_MINIMA = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validar_destinos(destinos: Sequence[str]) -> List[str]:
    """
    Normaliza y valida la lista. Levanta `DestinoInvalido` con el motivo.

    La lista vacía **es válida** y significa "no avisar por mail": el motor cae
    al log, que es lo que `alerts.enviar_alerta` ya hace cuando no hay a dónde
    mandar. No es lo mismo que estar sin configurar — es una decisión.
    """
    limpios: List[str] = []
    for crudo in destinos:
        destino = (crudo or "").strip()
        if not destino:
            # Un renglón en blanco en un textarea no es un error de nadie.
            continue
        if len(destino) > MAX_LARGO_MAIL:
            raise DestinoInvalido(
                f"La dirección tiene {len(destino)} caracteres y el máximo es "
                f"{MAX_LARGO_MAIL}."
            )
        if not _FORMA_MINIMA.match(destino):
            raise DestinoInvalido(
                f"'{destino}' no parece una dirección de correo: hace falta un "
                f"arroba, sin espacios, y un punto en el dominio."
            )
        # Se deduplica sin distinguir mayúsculas: la parte del dominio no las
        # distingue nunca, y ningún proveedor real las distingue en la otra.
        if destino.lower() not in {d.lower() for d in limpios}:
            limpios.append(destino)

    if len(limpios) > MAX_DESTINOS:
        raise DestinoInvalido(
            f"Son {len(limpios)} direcciones y el máximo es {MAX_DESTINOS}. Esto "
            f"son las alertas de operación de un motor, no una lista de difusión: "
            f"para avisarle a más gente conviene un alias del lado del correo."
        )
    return limpios


def configuracion(session: Session) -> ConfiguracionAlertas:
    """
    La fila de configuración, creándola vacía si no existe.

    Al vuelo y no asumiendo que la migración la dejó, por el mismo motivo que en
    `entrega.configuracion`: las bases de test se crean con
    `SQLModel.metadata.create_all`, que no corre migraciones.
    """
    fila = session.get(ConfiguracionAlertas, FILA_UNICA)
    if fila is None:
        fila = ConfiguracionAlertas(id=FILA_UNICA, destinos=[])
        session.add(fila)
        session.commit()
        session.refresh(fila)
    return fila


def destinos_de_alerta(session: Session) -> List[str]:
    """A quién avisar. Lista vacía si nadie."""
    return list(configuracion(session).destinos or [])


def guardar_destinos(session: Session, destinos: Sequence[str]) -> ConfiguracionAlertas:
    """Cambia la lista de destinos. Valida antes de escribir."""
    limpios = validar_destinos(destinos)

    fila = configuracion(session)
    anteriores = list(fila.destinos or [])
    fila.destinos = limpios
    fila.actualizado_en = ahora_utc()
    session.add(fila)
    session.commit()
    session.refresh(fila)

    logger.info(
        "Destinos de alerta: %d -> %d direcciones", len(anteriores), len(limpios)
    )
    return fila


def marcar_prueba_ok(session: Session) -> ConfiguracionAlertas:
    """
    Deja constancia de que un envío llegó a salir.

    **Es lo que distingue "hay un mail configurado" de "el mail sale"**, que es
    justamente la confusión que abrió este punto. Sin esta fecha, una casilla
    dada de baja hace meses se ve igual que una que anda.

    Dice que el servidor de correo **aceptó** el mensaje, no que alguien lo haya
    recibido: que llegue a la bandeja es lo único que no se puede comprobar desde
    acá, y por eso la interfaz pide mirar.
    """
    fila = configuracion(session)
    fila.ultima_prueba_ok = ahora_utc()
    session.add(fila)
    session.commit()
    session.refresh(fila)
    return fila
