"""
El registro de lo que el motor consideró digno de avisar. Punto 19 del backlog.

**No instrumenta nada nuevo.** Los nueve `alerts.enviar_alerta` ya son los
puntos donde el motor decide "esto amerita avisar", y ya vienen con clave y
texto. Lo único que hace este módulo es que ese momento, además de mandar un
mail, deje una fila que se pueda consultar sin abrir una terminal.
"""

import logging
from typing import List, Optional, Sequence

from sqlmodel import Session, select

from ..models.evento import MAX_LARGO_MENSAJE, Evento
from ..tiempo import ahora_utc

logger = logging.getLogger(__name__)

# Cuánto se conserva. Alcanza para "esto viene pasando desde el mes pasado", que
# es la pregunta que un panel de eventos contesta. Con el volumen medido el
# 12/09/2026 --unos pocos eventos por día-- son cientos de filas, no millones.
DIAS_DE_RETENCION = 90


def categoria_de(clave: str) -> str:
    """
    Lo que va antes del `:`. `ingesta:La Nación` → `ingesta`.

    Las claves ya venían con esa forma antes de que existiera esta tabla, así
    que la categoría no hubo que inventarla: estaba implícita en cómo el motor
    agrupa sus avisos.
    """
    return clave.split(":", 1)[0] if ":" in clave else clave


def registrar(
    session: Session, *, clave: str, asunto: str, mensaje: str, terminal: bool = False
) -> Evento:
    """
    Anota el evento, sumando al contador si esa clave ya estaba.

    **Actualiza en vez de insertar**, y el mensaje que queda es el último. Un
    feed caído toda la noche deja una fila que dice "96 veces" en lugar de 96
    filas iguales que tapan todo lo demás.
    """
    ahora = ahora_utc()
    fila = session.exec(select(Evento).where(Evento.clave == clave)).first()

    if fila is None:
        fila = Evento(
            clave=clave,
            categoria=categoria_de(clave),
            asunto=asunto[:200],
            # El corte existe para que un traceback pegado adentro del cuerpo no
            # entre entero a la base. El log sigue teniéndolo completo.
            mensaje=mensaje[:MAX_LARGO_MENSAJE],
            veces=1,
            primera_vez=ahora,
            ultima_vez=ahora,
            terminal=terminal,
        )
    else:
        fila.veces += 1
        fila.ultima_vez = ahora
        fila.asunto = asunto[:200]
        fila.mensaje = mensaje[:MAX_LARGO_MENSAJE]
        # Una vez terminal, siempre terminal: que la última ocurrencia haya sido
        # pasajera no borra que algo ya se perdió.
        fila.terminal = fila.terminal or terminal

    session.add(fila)
    session.commit()
    session.refresh(fila)
    return fila


def listar(
    session: Session, *, categoria: Optional[str] = None, limite: int = 50
) -> List[Evento]:
    """Los eventos más recientes primero, opcionalmente de una categoría."""
    consulta = select(Evento)
    if categoria:
        consulta = consulta.where(Evento.categoria == categoria)
    return list(
        session.exec(consulta.order_by(Evento.ultima_vez.desc()).limit(limite)).all()
    )


def categorias(session: Session) -> List[str]:
    """Las categorías que existen de verdad, para armar el filtro."""
    filas = session.exec(select(Evento.categoria).distinct()).all()
    return sorted(filas)


def purgar_viejos(session: Session, dias: int = DIAS_DE_RETENCION) -> int:
    """
    Borra los eventos que no se repiten desde hace `dias`. Devuelve cuántos.

    **Se mira `ultima_vez` y no `primera_vez`**: un problema que empezó hace
    cuatro meses y sigue ocurriendo es exactamente el que no hay que borrar.
    """
    from datetime import timedelta

    corte = ahora_utc() - timedelta(days=dias)
    viejos = session.exec(select(Evento).where(Evento.ultima_vez < corte)).all()
    for fila in viejos:
        session.delete(fila)
    if viejos:
        session.commit()
        logger.info("Purga de eventos: %d borrados (sin repetirse hace %dd)", len(viejos), dias)
    return len(viejos)


def registrar_sin_romper(
    clave: str, asunto: str, mensaje: str, terminal: bool = False
) -> None:
    """
    Anota el evento abriendo su propia sesión, y **nunca propaga**.

    `alerts.enviar_alerta` no recibe sesión: se la llama desde cinco módulos,
    algunos con una a mano y otros no. Mismo problema y misma salida que
    `alerts._destinos`.

    **Que no propague no es prolijidad: es la regla del avisador.** Fallar al
    registrar un fallo no puede ser lo que tire la corrida — y si la base es
    justamente lo que está roto, este import va a explotar, que es el momento en
    que menos se puede permitir romper nada.
    """
    try:
        from ..database import get_engine

        with Session(get_engine()) as sesion:
            registrar(
                sesion, clave=clave, asunto=asunto, mensaje=mensaje, terminal=terminal
            )
    except Exception as error:
        logger.warning("No se pudo registrar el evento '%s': %s", clave, error)


__all__: Sequence[str] = (
    "DIAS_DE_RETENCION",
    "categoria_de",
    "categorias",
    "listar",
    "purgar_viejos",
    "registrar",
    "registrar_sin_romper",
)
