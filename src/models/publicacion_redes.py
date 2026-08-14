from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import Column, DateTime
from sqlmodel import Field, Relationship, SQLModel

from .tipos import JSONVariant

from ..tiempo import ahora_utc

if TYPE_CHECKING:
    from .sintesis import Sintesis


class PublicacionRedes(SQLModel, table=True):
    """
    Copy para redes sociales (Twitter/Facebook) de un ángulo ya sintetizado.

    Tabla aparte y no columnas en `Sintesis` porque no es 1:1 con toda síntesis:
    Gemini la genera solo para el subconjunto que marca de relevancia nacional
    (`AnguloGenerado.relevancia_social` -- ver `services/synthesis.py`), así
    que la mayoría de las filas de `Sintesis` no tendrían nada que poner acá.
    Ver specs/change_logs.md, "Copy para redes sociales".

    A diferencia de `titulo_angulo`/`topicos`, esto NO se congela: es
    contenido de marketing, descartable, no la identidad publicada del
    ángulo, así que una resíntesis lo puede reemplazar sin romper nada del
    lado del back-end.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    sintesis_id: int = Field(foreign_key="sintesis.id", unique=True, index=True)

    resumen_redes: str
    hashtags: List[str] = Field(default_factory=list, sa_column=Column(JSONVariant))

    fecha_generacion: datetime = Field(
        default_factory=ahora_utc, sa_column=Column(DateTime, nullable=False)
    )

    sintesis: "Sintesis" = Relationship(back_populates="publicacion_redes")
