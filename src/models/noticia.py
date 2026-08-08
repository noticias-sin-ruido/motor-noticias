from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .medio import Medio
    from .cluster import Cluster

# Dimensión del vector de embedding.
# Modelo elegido: "paraphrase-multilingual-MiniLM-L12-v2" (384 dims) —
# multilingüe, necesario porque las noticias son en español y los modelos
# tipo "all-MiniLM-L6-v2" están entrenados esencialmente en inglés.
# Alternativa equivalente de 384 dims: "intfloat/multilingual-e5-small".
EMBEDDING_DIM = 384


class Noticia(SQLModel, table=True):
    """Representa una noticia individual, ya extraída y limpiada, proveniente de un medio."""

    id: Optional[int] = Field(default=None, primary_key=True)

    medio_id: int = Field(foreign_key="medio.id", index=True)
    cluster_id: Optional[int] = Field(default=None, foreign_key="cluster.id", index=True)

    titulo: str
    url: str = Field(sa_column=Column(Text, unique=True, index=True, nullable=False))
    # Identificador estable del item en el RSS de origen (tag <guid>), usado para
    # deduplicar: es más confiable que la URL, que puede cambiar por parámetros
    # de tracking o redirecciones entre distintas lecturas del mismo feed.
    guid: str = Field(sa_column=Column(Text, unique=True, index=True, nullable=False))
    contenido_limpio: str = Field(sa_column=Column(Text, nullable=False))
    fecha_publicacion: datetime = Field(sa_column=Column(DateTime, nullable=False, index=True))

    # Vector semántico de la noticia, generado en la fase de vectorización.
    # Queda en None hasta que la noticia sea procesada.
    embedding: Optional[List[float]] = Field(
        default=None, sa_column=Column(Vector(EMBEDDING_DIM), nullable=True)
    )

    medio: "Medio" = Relationship(back_populates="noticias")
    cluster: Optional["Cluster"] = Relationship(back_populates="noticias")