from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, Relationship, SQLModel

# Import en tiempo de ejecución (no bajo TYPE_CHECKING) porque `link_model` lo
# necesita resuelto. No genera ciclo: `sintesis.py` importa `Noticia` solo para
# anotaciones de tipo.
from .sintesis import SintesisNoticia

if TYPE_CHECKING:
    from .medio import Medio
    from .cluster import Cluster
    from .sintesis import Sintesis

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

    # Cuándo se purgó el cuerpo, o `None` si todavía lo tiene. Ver
    # `services/purga.py` (backlog punto 8).
    #
    # `contenido_limpio` sigue siendo `NOT NULL` — una purgada guarda `''`, no
    # `NULL` — y esta columna es la que distingue "se purgó el 4/9" de "nunca
    # tuvo cuerpo" (que hoy no pasa: `ingestion.py` descarta antes de insertar
    # cualquier nota sin `content:encoded`, salvo que venga por extracción). Sin
    # esta marca, `''` sería ambiguo y la retención de texto de terceros
    # dejaría de ser medible.
    #
    # Se lee junto con `contenido_limpio`, nunca sola: el par es lo que importa.
    purgado_en: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True, index=True)
    )

    # Vector semántico de la noticia, generado en la fase de vectorización.
    # Queda en None hasta que la noticia sea procesada.
    embedding: Optional[List[float]] = Field(
        default=None, sa_column=Column(Vector(EMBEDDING_DIM), nullable=True)
    )

    medio: "Medio" = Relationship(back_populates="noticias")
    cluster: Optional["Cluster"] = Relationship(back_populates="noticias")
    # Ángulos que esta nota respalda. Es una lista porque una misma noticia
    # puede sostener varios (un minuto a minuto cubre el hecho y sus reacciones).
    sintesis: List["Sintesis"] = Relationship(
        back_populates="noticias", link_model=SintesisNoticia
    )