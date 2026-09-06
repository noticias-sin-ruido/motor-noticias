"""
El registro de lo que hace el pipeline: cuándo corrió, cuánto tardó, qué produjo.

Hasta el punto 14 el motor no sabía en qué andaba: `GET /` devuelve salud, base
y hora, y nada del scheduler. La duración y la utilización del ciclo se medían
en cada corrida y se tiraban al log.
"""
import logging
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from ..config import settings
from ..models import Corrida
from ..tiempo import ahora_utc, iso_local

logger = logging.getLogger(__name__)


def iniciar_corrida(session: Session) -> int:
    """
    Abre la fila de esta corrida y devuelve **su id**, no el objeto.

    **Devolver el id y no la fila es deliberado.** Entre este momento y el
    cierre corren ocho pasos, y `_correr_paso` hace `session.rollback()` cuando
    uno falla; sostener un objeto vivo a través de eso es exactamente la trampa
    que el punto 14 dejó anotada en el roadmap y que ya nos costó un
    `DetachedInstanceError` en una corrida real. Un `int` no se expira.
    """
    corrida = Corrida(inicio=ahora_utc(), pasos={})
    session.add(corrida)
    # Commit inmediato: si quedara pendiente, el primer `rollback` de un paso
    # que falla se la llevaría puesta y la corrida no quedaría registrada
    # justo cuando más interesa saber que pasó algo.
    session.commit()
    session.refresh(corrida)
    return corrida.id


def cerrar_corrida(
    session: Session,
    corrida_id: int,
    pasos: Dict[str, Any],
    duracion_segundos: float,
    utilizacion: float,
) -> None:
    """
    Completa la fila al terminar. Si falla, **no se lleva puesta la corrida**.

    El registro es observabilidad: que no se pueda escribir es un problema, pero
    hacerlo explotar hacia arriba convertiría un fallo de contabilidad en un
    fallo del pipeline. Se loguea y se sigue.
    """
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


def listar_corridas(session: Session, limite: int = 20) -> List[dict]:
    """Las últimas corridas, de la más reciente a la más vieja."""
    corridas = session.exec(
        select(Corrida).order_by(Corrida.inicio.desc()).limit(limite)
    ).all()
    return [_vista_de_corrida(c) for c in corridas]


def ultima_corrida(session: Session) -> Optional[dict]:
    """La última corrida registrada, o `None` si el motor nunca corrió."""
    corrida = session.exec(
        select(Corrida).order_by(Corrida.inicio.desc()).limit(1)
    ).first()
    return None if corrida is None else _vista_de_corrida(corrida)
