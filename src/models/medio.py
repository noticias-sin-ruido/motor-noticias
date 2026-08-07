from typing import List, Optional, TYPE_CHECKING

from sqlmodel import Field, Relationship, SQLModel
 
if TYPE_CHECKING:
    from .noticia import Noticia
 
 
class Medio(SQLModel, table=True):
    """Representa un medio de comunicación fuente de noticias (ej. Clarín, La Nación, Página 12)."""
 
    id: Optional[int] = Field(default=None, primary_key=True)
    nombre: str = Field(index=True)
    url_base: str
    feed_rss: str
    activo: bool = Field(default=True)
 
    noticias: List["Noticia"] = Relationship(back_populates="medio")
 