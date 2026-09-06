"""
El registro de lo que hace el pipeline: cuándo corrió, cuánto tardó, qué produjo.

Hasta el punto 14 el motor no sabía en qué andaba: `GET /` devuelve salud, base
y hora, y nada del scheduler. La duración y la utilización del ciclo se medían
en cada corrida y se tiraban al log.
"""
import logging
from typing import Any, Dict, Optional

from sqlmodel import Session, select

from ..config import settings
from ..models import Corrida
from ..tiempo import ahora_utc, iso_local

logger = logging.getLogger(__name__)


def iniciar_corrida(session: Session) -> Optional[int]:
    """
    Abre la fila de esta corrida y devuelve **su id**, o `None` si no pudo.

    **Devolver el id y no la fila es deliberado.** Entre este momento y el
    cierre corren ocho pasos, y `_correr_paso` hace `session.rollback()` cuando
    uno falla; sostener un objeto vivo a través de eso es exactamente la trampa
    que el punto 14 dejó anotada en el roadmap y que ya nos costó un
    `DetachedInstanceError` en una corrida real. Un `int` no se expira.

    **Y no puede tumbar el pipeline, que es la otra mitad.** La primera versión
    de esta función no tenía guarda, y una revisión lo encontró: como corre
    *antes* del primer paso, un fallo al escribir la fila abortaba la corrida
    entera. Verificado con una sonda —**0 de 8 pasos llegaban a correr**—, o
    sea que un problema de contabilidad se convertía en un problema de
    producción, justo lo contrario de lo que este módulo dice hacer.

    El `rollback` del `except` no es cosmético: sin él la sesión queda
    inutilizable y los ocho pasos fallarían igual, por un motivo distinto al
    original.
    """
    try:
        corrida = Corrida(inicio=ahora_utc(), pasos={})
        session.add(corrida)
        # Commit inmediato: si quedara pendiente, el primer `rollback` de un
        # paso que falla se la llevaría puesta y la corrida no quedaría
        # registrada justo cuando más interesa saber que pasó algo.
        session.commit()
        session.refresh(corrida)
        return corrida.id
    except Exception:
        session.rollback()
        logger.exception("No se pudo abrir el registro de la corrida")
        return None


def cerrar_corrida(
    session: Session,
    corrida_id: Optional[int],
    pasos: Dict[str, Any],
    duracion_segundos: float,
    utilizacion: float,
) -> None:
    """
    Completa la fila al terminar. Si falla, **no se lleva puesta la corrida**.

    El registro es observabilidad: que no se pueda escribir es un problema, pero
    hacerlo explotar hacia arriba convertiría un fallo de contabilidad en un
    fallo del pipeline. Se loguea y se sigue.

    `corrida_id` en `None` significa que la apertura tampoco pudo escribirse.
    No hay nada que cerrar y no es un error nuevo: ya se logueó allá.
    """
    if corrida_id is None:
        return

    try:
        corrida = session.get(Corrida, corrida_id)
        if corrida is None:
            logger.warning(f"No se encontró la corrida {corrida_id} para cerrarla")
            return
        corrida.fin = ahora_utc()
        corrida.duracion_segundos = duracion_segundos
        corrida.utilizacion = utilizacion
        corrida.pasos = pasos
        session.add(corrida)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception(f"No se pudo registrar el cierre de la corrida {corrida_id}")


def _vista_de_corrida(corrida: Corrida) -> dict:
    return {
        "id": corrida.id,
        "inicio": iso_local(corrida.inicio),
        "fin": iso_local(corrida.fin),
        "duracion_segundos": corrida.duracion_segundos,
        "utilizacion": corrida.utilizacion,
        "pasos": corrida.pasos,
    }


def estado_del_pipeline(session: Session, historial: int = 5) -> dict:
    """
    En qué anda el pipeline: si hay algo corriendo, la última y las anteriores.

    **`historial` es el total, la última incluida**, no cuántas anteriores.
    Con `historial=3` vuelven una `ultima` y dos en `anteriores`. La primera
    versión lo documentaba al revés y una revisión lo encontró: el parámetro
    hacía lo correcto y el texto mentía.

    **Cómo se decide "está corriendo".** Una corrida sin `fin` es candidata,
    pero no alcanza: si el proceso murió a mitad de camino, esa fila queda
    abierta para siempre y el motor diría "corriendo" eternamente. Así que se
    exige además que sea **reciente**, con el ciclo configurado como vara. Una
    corrida sin cerrar más vieja que un ciclo completo no está viva: quedó
    huérfana, y se informa como tal en vez de mentir.
    """
    corridas = session.exec(
        select(Corrida).order_by(Corrida.inicio.desc()).limit(max(historial, 1))
    ).all()

    if not corridas:
        return {
            "corriendo": False,
            "huerfana": False,
            "intervalo_minutos": settings.INGEST_INTERVAL_MINUTES,
            "ultima": None,
            "anteriores": [],
        }

    ultima = corridas[0]
    sin_cerrar = ultima.fin is None
    antiguedad = (ahora_utc() - ultima.inicio).total_seconds()
    dentro_del_ciclo = antiguedad <= settings.INGEST_INTERVAL_MINUTES * 60

    return {
        "corriendo": sin_cerrar and dentro_del_ciclo,
        # Se informa aparte y no se esconde: una corrida abierta y vieja es el
        # síntoma de que el proceso se cayó, y eso vale saberlo.
        "huerfana": sin_cerrar and not dentro_del_ciclo,
        "intervalo_minutos": settings.INGEST_INTERVAL_MINUTES,
        "ultima": _vista_de_corrida(ultima),
        "anteriores": [_vista_de_corrida(c) for c in corridas[1:]],
    }
