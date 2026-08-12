from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from sqlalchemy import Column, DateTime
from sqlmodel import Field, Relationship, SQLModel

from .tipos import JSONVariant

from ..tiempo import ahora_utc

if TYPE_CHECKING:
    from .cluster import Cluster
    from .noticia import Noticia


class SintesisNoticia(SQLModel, table=True):
    """
    Qué noticias respaldan cada síntesis.

    Es una relación muchos-a-muchos y no una columna en `Noticia`: una misma
    nota puede sostener varios ángulos (un minuto a minuto cubre el hecho, las
    reacciones y las consecuencias a la vez), y un ángulo se apoya en varias.

    Se modela como tabla y no como una lista de ids en JSON porque de acá sale
    la regla que decide qué se publica: un ángulo necesita `MIN_MEDIOS_CLUSTER`
    medios distintos, y eso es un `count(distinct medio_id)` sobre este join.
    Con ids sueltos en un JSON habría que traer todo a memoria para contarlo, y
    nada garantizaría que las noticias referenciadas existan.
    """

    sintesis_id: Optional[int] = Field(
        default=None, foreign_key="sintesis.id", primary_key=True
    )
    noticia_id: Optional[int] = Field(
        default=None, foreign_key="noticia.id", primary_key=True
    )


class Sintesis(SQLModel, table=True):
    """
    Síntesis neutra de un **ángulo** de un cluster: un resumen objetivo junto
    con la comparativa de enfoques de los medios que lo cubrieron.

    La unidad NO es el cluster sino el ángulo, así que un cluster produce varias
    síntesis. El clustering agrupa el hecho y toda su cobertura buscando no
    perder nada; separar ese material en ángulos distintos (el hecho, sus
    consecuencias, las reacciones) requiere leer los textos, y de eso se encarga
    el modelo. Ver specs/change_logs.md, Fase 4.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    cluster_id: int = Field(foreign_key="cluster.id", index=True)

    # Qué recorte del hecho cubre esta síntesis. Es el título que ve el usuario.
    titulo_angulo: str = Field(index=True)

    resumen_neutro: str
    puntos_clave: List[str] = Field(default_factory=list, sa_column=Column(JSONVariant))
    comparativa_enfoques: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONVariant))

    # De qué tema(s) es y con qué detalle. Valores de las listas cerradas de
    # `services/topicos.py`: es lo que le permite al back-end armar secciones y
    # filtros, y con texto libre esa navegación se rompe sola.
    #
    # Listas y no un principal + secundario: esa pareja mezclaba dos preguntas
    # distintas bajo el mismo campo -- "de qué otra categoría es esto además"
    # (deportes Y espectáculos, pares) y "qué recorte más fino tiene dentro de
    # una categoría" (fútbol, DENTRO de deportes). `topicos` son 1 o 2
    # categorías pares; `subtopicos` son 0 o más recortes finos, cada uno con
    # un padre fijo en `SUBTOPICO_PADRE` que el código garantiza que esté
    # presente en `topicos` -- ver `con_padres_completos`.
    #
    # Se guardan como JSON y no como columnas de Enum para poder agregar una
    # categoría sin migrar la tabla; la garantía de que los valores son válidos
    # la da el `response_schema` del modelo, que sólo admite los de la lista.
    #
    # Vacíos (no nulos) en las síntesis generadas antes de este rediseño y en
    # las anteriores al campo original.
    topicos: List[str] = Field(default_factory=list, sa_column=Column(JSONVariant))
    subtopicos: List[str] = Field(default_factory=list, sa_column=Column(JSONVariant))

    fecha_generacion: datetime = Field(
        default_factory=ahora_utc, sa_column=Column(DateTime, nullable=False)
    )

    # --- Entrega al backend web/mobile ---
    # El motor empuja la síntesis por webhook en vez de exponerla por polling.
    # El estado de entrega vive acá y no en una tabla de log aparte: hoy hay un
    # solo backend destino. Si se agotan los reintentos, la síntesis queda con
    # `enviado_backend=False` y un job periódico barre las no entregadas.
    enviado_backend: bool = Field(default=False, index=True)
    fecha_envio: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    intentos_envio: int = Field(default=0)

    cluster: "Cluster" = Relationship(back_populates="sintesis")
    noticias: List["Noticia"] = Relationship(
        back_populates="sintesis", link_model=SintesisNoticia
    )
