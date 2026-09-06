from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from .tipos import JSONVariant

from ..tiempo import ahora_utc


class Corrida(SQLModel, table=True):
    """
    Una corrida del pipeline: cuándo arrancó, cuánto tardó y qué hizo cada paso.

    **Existe porque el número ya se calculaba y se tiraba.**
    `_job_ingesta_programada` mide la duración y la utilización del ciclo, las
    loguea —"utilización 26,7% del ciclo"— y ahí mueren. La decisión que ese
    número justifica (¿subir `INGEST_INTERVAL_MINUTES`?, ¿ya hacen falta los
    hilos por modelo?) depende hoy de que alguien lea logs viejos. Ver el punto
    14 de `roadmap.md` y `medir-antes-de-resolver`: un número medido tiene que
    quedar donde justifica la decisión.

    **Los pasos van en una sola columna JSON y no en una tabla aparte.** Las
    preguntas que existen son "la última" y "las últimas N", no "todas las veces
    que falló la vectorización"; normalizar sería resolver preguntas que nadie
    tiene todavía. Y `JSONVariant` ya es el idioma de la casa: lo usan
    `puntos_clave`, `comparativa_enfoques` y `topicos`.
    """

    id: Optional[int] = Field(default=None, primary_key=True)

    # UTC como todo lo que se persiste; a hora argentina se pasa al salir por la
    # API (`iso_local`). Indexado porque **toda** consulta ordena por él: la
    # última corrida y las últimas N son las dos únicas preguntas que se hacen.
    inicio: datetime = Field(
        default_factory=ahora_utc,
        sa_column=Column(DateTime, nullable=False, index=True),
    )

    # **`None` significa "arrancó y no terminó".** La fila se escribe al empezar
    # y se completa al final, y esa es justamente la forma de saber si hay una
    # corrida en curso sin depender del estado en memoria de APScheduler, que se
    # pierde con cada reinicio.
    #
    # El costo honesto: si el proceso muere a mitad de una corrida, la fila
    # queda con `fin` en `None` para siempre y parece estar corriendo. Por eso
    # `estado_del_pipeline` no pregunta solo por `fin IS NULL` sino también por
    # la antigüedad — una corrida sin cerrar más vieja que el ciclo está
    # huérfana, no viva.
    fin: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )

    duracion_segundos: Optional[float] = Field(default=None)

    # Fracción del ciclo que consumió la corrida. Es el canario del techo: si se
    # acerca a 1, la corrida siguiente se saltea (`SCHEDULER_MAX_INSTANCES = 1`).
    utilizacion: Optional[float] = Field(default=None)

    # Nombre del paso -> sus stats, o `null` si ese paso falló. El `null` no es
    # un hueco: es el dato de que ese paso se intentó y no salió, que es
    # distinto de un paso que no llegó a correrse y por eso no está en el dict.
    pasos: Dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONVariant, nullable=False)
    )
