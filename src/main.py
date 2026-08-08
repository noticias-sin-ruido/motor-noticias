import logging
from contextlib import asynccontextmanager
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Query
from sqlmodel import Session

from .config import settings
from .database import get_engine, get_session, init_db
from .services.clustering import agrupar_pendientes, cerrar_clusters_vencidos
from .services.ingestion import ingerir_todos_los_medios
from .services.search import buscar_noticias_similares, listar_clusters
from .services.vectorization import vectorizar_pendientes

logger = logging.getLogger(__name__)

# Frecuencia del polling de RSS. Ver specs/change_logs.md, Fase 2 -- "Scheduler"
# para el razonamiento detrás del intervalo uniforme de 15 minutos.
INGEST_INTERVAL_MINUTES = 15

scheduler = AsyncIOScheduler()


def _job_ingesta_programada() -> None:
    """
    Job del scheduler: ingiere los feeds y vectoriza lo que haya entrado.

    La vectorización va encadenada a la ingesta y no en un job propio porque
    depende de ella: sin noticias nuevas no hay nada que vectorizar. Es
    idempotente, así que si una corrida falla a mitad de camino, la siguiente
    retoma las noticias que quedaron sin embedding.
    """
    with Session(get_engine()) as session:
        resultados = ingerir_todos_los_medios(session)
        logger.info(f"Ingesta programada completada: {resultados}")

        stats = vectorizar_pendientes(session)
        logger.info(f"Vectorización completada: {stats}")

        # El cierre va ANTES del agrupamiento para que los clusters vencidos no
        # sigan capturando noticias nuevas en esta misma corrida.
        cierre = cerrar_clusters_vencidos(session)
        logger.info(f"Cierre de clusters completado: {cierre}")

        agrupamiento = agrupar_pendientes(session)
        logger.info(f"Agrupamiento completado: {agrupamiento}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: habilita la extensión pgvector, crea las tablas si no existen,
    # y arranca el scheduler embebido (ver CLAUDE.md, Fase 2 -- "Scheduler").
    init_db()
    scheduler.add_job(
        _job_ingesta_programada,
        "interval",
        minutes=INGEST_INTERVAL_MINUTES,
        id="ingesta_rss",
    )
    scheduler.start()
    yield
    # Shutdown.
    scheduler.shutdown()


app = FastAPI(
    title="Sin Ruido — API",
    description="Motor backend para ingesta, vectorización y síntesis neutra de noticias.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
def root():
    return {"status": "ok", "environment": settings.ENVIRONMENT}


@app.post("/ingest")
def ingest(session: Session = Depends(get_session)):
    """
    Corre el pipeline de ingesta a demanda para todos los medios activos.
    Uso manual durante desarrollo, y como fallback operativo si el scheduler
    se cae -- ver CLAUDE.md, Fase 2, "Endpoint manual POST /ingest".
    """
    resultados = ingerir_todos_los_medios(session)
    return {"status": "ok", "resultados": resultados}


@app.post("/vectorize")
def vectorize(limite: Optional[int] = None, session: Session = Depends(get_session)):
    """
    Vectoriza a demanda las noticias que todavía no tienen embedding.

    Es idempotente (no revectoriza lo ya procesado). `limite` sirve para
    procesar un backlog grande de a tandas en vez de todo de una.
    """
    stats = vectorizar_pendientes(session, limite=limite)
    return {"status": "ok", **stats}


@app.post("/cluster")
def cluster(session: Session = Depends(get_session)):
    """
    Cierra los clusters vencidos y agrupa las noticias vectorizadas sueltas.

    El cierre corre primero para que un cluster ya vencido no capture noticias
    nuevas en la misma pasada.
    """
    cierre = cerrar_clusters_vencidos(session)
    agrupamiento = agrupar_pendientes(session)
    return {"status": "ok", "cierre": cierre, "agrupamiento": agrupamiento}


@app.get("/search")
def search(
    q: str = Query(..., min_length=3, description="Texto a buscar"),
    limite: int = Query(10, ge=1, le=50),
    solo_agrupadas: bool = False,
    session: Session = Depends(get_session),
):
    """
    Búsqueda semántica de noticias: devuelve las más parecidas al texto `q`,
    ordenadas por similitud. No busca por palabras exactas sino por significado.
    """
    resultados = buscar_noticias_similares(
        session, texto=q, limite=limite, solo_agrupadas=solo_agrupadas
    )
    return {"status": "ok", "consulta": q, "cantidad": len(resultados), "resultados": resultados}


@app.get("/clusters")
def clusters(
    estado: Optional[str] = Query(None, description="abierto | procesado | descartado"),
    limite: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
):
    """Lista los clusters (eventos) con sus noticias y los medios que los cubrieron."""
    resultados = listar_clusters(session, estado=estado, limite=limite)
    return {"status": "ok", "cantidad": len(resultados), "clusters": resultados}
