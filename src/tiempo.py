"""
Manejo del tiempo. Un solo lugar donde se decide qué zona se usa y para qué.

**La regla: se guarda en UTC, se muestra en UTC-3.**

Suena a burocracia hasta que se ve lo que cuesta mezclarlas. Tres razones
concretas por las que el almacenamiento no puede pasarse a hora local:

1. `fecha_publicacion` llega del RSS en UTC, y se compara contra "ahora" para
   decidir la ventana de 12 h del cluster. Si una punta quedara en local, la
   ventana se correría 3 horas sin que nada falle a la vista.
2. `specs/webhook_contract.md` ya le prometió al back-end fechas UTC con `Z`.
   Es un contrato con otro equipo.
3. Ya nos pasó: la firma del webhook se calculaba con
   `datetime.utcnow().timestamp()`, que interpreta un datetime naive como hora
   **local**. El resultado salía corrido 3 horas y el receptor rechazaba todo
   con un 401. Es exactamente la clase de error que produce mezclar zonas.

Lo que sí cambia es todo lo que lee una persona: la API y los reportes muestran
UTC-3 con el offset explícito (`-03:00`), para no tener que hacer la resta
mentalmente ni adivinar en qué zona está un número.

Argentina no aplica horario de verano desde 2009, así que el offset fijo es
correcto y no hace falta arrastrar una base de zonas horarias.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

# Hora oficial argentina. Fija: sin DST desde 2009.
ZONA_LOCAL = timezone(timedelta(hours=-3), name="ART")


def ahora_utc() -> datetime:
    """
    "Ahora" en UTC, sin zona, que es como se guarda en la base.

    Reemplaza a `datetime.utcnow()`, que quedó deprecado en Python 3.12 y llena
    la salida de los tests de warnings. El comportamiento es idéntico a
    propósito: mismo valor, mismo tipo naive. Cambiar a un datetime *con* zona
    obligaría a migrar las columnas, que están declaradas sin ella.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ahora_local() -> datetime:
    """"Ahora" en UTC-3, con zona. Para mostrar, nunca para guardar."""
    return datetime.now(ZONA_LOCAL)


def a_local(momento: Optional[datetime]) -> Optional[datetime]:
    """
    Pasa a UTC-3 un momento guardado en la base.

    Los datetime naive se asumen UTC, que es como los guarda todo el motor.
    """
    if momento is None:
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(ZONA_LOCAL)


def iso_local(momento: Optional[datetime]) -> Optional[str]:
    """
    ISO 8601 en UTC-3 con el offset explícito: `2026-08-09T21:17:15-03:00`.

    El offset no es decorativo: sin él, del otro lado la fecha se interpreta con
    la zona de quien la lee.
    """
    local = a_local(momento)
    return None if local is None else local.isoformat()


def formatear(momento: Optional[datetime]) -> str:
    """Legible para un humano, en hora argentina. Para logs y reportes."""
    local = a_local(momento)
    return "—" if local is None else local.strftime("%d/%m %H:%M:%S")
