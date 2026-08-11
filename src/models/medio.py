from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import Column
from sqlmodel import Field, Relationship, SQLModel

from .tipos import JSONVariant

if TYPE_CHECKING:
    from .noticia import Noticia


class Medio(SQLModel, table=True):
    """Representa un medio de comunicación fuente de noticias (ej. Clarín, La Nación, Página 12)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    nombre: str = Field(index=True)
    url_base: str

    # **Varios** feeds por medio, no uno.
    #
    # El feed general de un diario grande no es una muestra de todo lo que
    # publica: es una selección de portada. Medido sobre La Nación, el general
    # trae 89 items cuando entre sus secciones hay 342 — vemos el 26%. En TN,
    # solo la sección deportes aporta 62 notas que el general no tiene.
    #
    # Eso importa porque un par se pierde cuando dos medios cubren el mismo
    # hecho y ninguna de las dos notas entró a la portada. Los feeds por sección
    # traen `content:encoded` con cuerpo completo igual que el general, así que
    # es más material sin cambiar el criterio de admisión.
    #
    # Los medios chicos o monotemáticos (El Cronista, Ciudad Magazine) quedan
    # con un solo feed: ahí el general ya trae todo lo que publican.
    feeds_rss: List[str] = Field(
        default_factory=list, sa_column=Column(JSONVariant, nullable=False)
    )

    activo: bool = Field(default=True)

    noticias: List["Noticia"] = Relationship(back_populates="medio")
