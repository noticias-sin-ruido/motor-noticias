from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from sqlalchemy import Column, DateTime, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .cluster import Cluster

# JSONB en PostgreSQL (producción); JSON genérico en SQLite (tests en memoria),
# ya que SQLite no soporta el tipo JSONB.
JSONVariant = JSONB().with_variant(JSON(), "sqlite")


class Sintesis(SQLModel, table=True):
    """
    Síntesis neutra generada a partir de un cluster de noticias: un resumen
    objetivo junto con la comparativa de enfoques de cada medio involucrado.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    cluster_id: int = Field(foreign_key="cluster.id", index=True)

    resumen_neutro: str
    puntos_clave: List[str] = Field(default_factory=list, sa_column=Column(JSONVariant))
    comparativa_enfoques: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONVariant))

    fecha_generacion: datetime = Field(
        default_factory=datetime.utcnow, sa_column=Column(DateTime, nullable=False)
    )

    cluster: "Cluster" = Relationship(back_populates="sintesis")
