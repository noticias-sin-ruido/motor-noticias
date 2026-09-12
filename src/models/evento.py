from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

# Tope de la clave. Las que existen hoy son cortas (`webhook:agotadas`) pero dos
# se arman con el nombre de un medio o un host (`ingesta:{medio}`,
# `robots:{base}`), así que el largo lo decide un dato de afuera.
MAX_LARGO_CLAVE = 200

# Tope del texto. Los mensajes del motor son de una o dos líneas; el corte está
# para que un error con un traceback pegado adentro no llegue a la base entero.
MAX_LARGO_MENSAJE = 2000


class Evento(SQLModel, table=True):
    """
    Algo que el motor consideró digno de avisar. Punto 19 del backlog.

    **La tabla existe porque el aviso no puede depender del mail.** El
    12/09/2026 se descubrió que las nueve alertas del motor no le llegaban a
    nadie desde hacía meses: la casilla estaba deshabilitada y el único rastro
    de cada fallo era un `logger.error` en el log de Docker, que rota a los
    50 MB y sólo se lee desde una terminal.

    **Una fila por clave, con contador, y no una por ocurrencia.** Un feed caído
    toda la noche produciría 96 filas idénticas que tapan todo lo demás — el
    mismo problema que el cooldown evita en el mail. Lo que se pierde es el
    texto de las ocurrencias intermedias; se guarda el último, que en la
    práctica es el que describe el estado actual.

    **Y la fila se escribe aunque el cooldown silencie el envío.** Son dos
    consumidores del mismo hecho con necesidades opuestas: el mail se calla para
    no inundar la casilla, el panel tiene que seguir contando. Si el evento se
    registrara sólo cuando el mail sale, un feed que falló cuarenta veces
    aparecería una.
    """

    id: Optional[int] = Field(default=None, primary_key=True)

    # La clave que `alerts.enviar_alerta` ya usa para agrupar. Es única: la fila
    # se actualiza en vez de insertarse.
    clave: str = Field(index=True, unique=True, max_length=MAX_LARGO_CLAVE)

    # Lo que va antes del `:` en la clave -- `ingesta`, `webhook`, `sintesis`.
    # Se guarda derivado en vez de calcularse al leer, para poder filtrar por
    # categoría sin un `LIKE 'x:%'` que no usa el índice.
    categoria: str = Field(index=True, max_length=MAX_LARGO_CLAVE)

    asunto: str = Field(max_length=MAX_LARGO_CLAVE)
    mensaje: str = Field(max_length=MAX_LARGO_MENSAJE)

    veces: int = Field(default=1)
    primera_vez: datetime
    ultima_vez: datetime = Field(index=True)

    # Si el aviso es de los que **no** esperan al cooldown. Los tres que pasan
    # `ignorar_cooldown=True` son los terminales -- informan algo que ya no se
    # puede deshacer, como una síntesis abandonada. No hace falta un campo de
    # severidad aparte: esta bandera ya distingue lo grave de lo pasajero.
    terminal: bool = Field(default=False)
