from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from sqlmodel import Field, Relationship, SQLModel

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
    fecha_creacion: datetime = Field(default_factory=datetime.utcnow)
    estado: str = Field(default="abierto", index=True)  # "abierto" | "procesado"

    noticias: List["Noticia"] = Relationship(back_populates="cluster")
    sintesis: List["Sintesis"] = Relationship(back_populates="cluster")