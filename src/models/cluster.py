from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from sqlmodel import Field, Relationship, SQLModel

from ..tiempo import ahora_utc

if TYPE_CHECKING:
    from .noticia import Noticia
    from .sintesis import Sintesis


class Cluster(SQLModel, table=True):
    """
    Representa un evento noticioso: un grupo de noticias de distintos medios
    que tratan sobre el mismo hecho, agrupadas por similitud semántica de sus embeddings.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    titulo_evento: str = Field(index=True)
    fecha_creacion: datetime = Field(default_factory=ahora_utc)
    # "abierto"    -> sigue aceptando noticias nuevas
    # "procesado"  -> cerrado con cobertura suficiente, listo para sintetizar
    # "descartado" -> cerrado sin alcanzar el mínimo de medios distintos:
    #                 sin dos voces no hay enfoques editoriales que comparar
    estado: str = Field(default="abierto", index=True)

    # Noticias que tenía el cluster la última vez que se intentó sintetizarlo.
    # Es la guarda contra el reintento infinito: si ningún ángulo alcanzó el
    # mínimo de medios no se crea ninguna fila de `Sintesis`, y sin esta marca
    # el cluster sería indistinguible de uno nunca intentado.
    #
    # Se cuentan noticias y no medios porque el conteo de medios no distingue
    # "no pasó nada nuevo" de "llegó material nuevo de los mismos medios", y ese
    # segundo caso sí puede dar un ángulo publicable: si TN y La Nación ya están
    # en el cluster y los dos publican después sobre los homenajes de la AFA,
    # eso es un ángulo nuevo con dos medios aunque no haya entrado ninguno.
    #
    # No alcanza por sí sola para decidir: quién dispara la síntesis es la
    # cobertura de las noticias todavía no asociadas a ningún ángulo. Ver
    # `services/synthesis.py`.
    #
    # Tres estados posibles:
    #   None  -> nunca se intentó
    #   >= 0  -> se intentó, y este era el conteo de noticias en ese momento
    #   -1    -> caducó sin intentarse nunca (`MARCA_CADUCADO`)
    #
    # El -1 existe para que el descarte por caducidad sea **reversible**: si se
    # marcara con el conteo real, subir `HORAS_MAXIMAS_SIN_SINTETIZAR` —que es
    # lo que recomienda la propia alerta— devolvería el cluster a la ventana de
    # fecha pero la guarda anti-bucle lo saltearía igual, y la recomendación
    # sería mentira.
    noticias_al_sintetizar: Optional[int] = Field(default=None)

    noticias: List["Noticia"] = Relationship(back_populates="cluster")
    sintesis: List["Sintesis"] = Relationship(back_populates="cluster")