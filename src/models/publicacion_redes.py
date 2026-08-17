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
    # `nullable=False` explícito: la base ya lo tiene así desde la migración
    # que creó la tabla, pero el `Column()` sin declararlo dejaba el metadata
    # diciendo lo contrario y `alembic check` proponía una migración para
    # aflojar la restricción. La lista siempre tiene valor (`default_factory`),
    # así que lo correcto es que el modelo diga lo que la base ya exige.
    hashtags: List[str] = Field(
        default_factory=list, sa_column=Column(JSONVariant, nullable=False)
    )

    fecha_generacion: datetime = Field(
        default_factory=ahora_utc, sa_column=Column(DateTime, nullable=False)
    )

    sintesis: "Sintesis" = Relationship(back_populates="publicacion_redes")
