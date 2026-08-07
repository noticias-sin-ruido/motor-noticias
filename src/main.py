import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI
from sqlmodel import Session

from .config import settings
from .database import get_engine, get_session, init_db
from .models import Medio
from .services.ingestion import ingerir_todos_los_medios

logger = logging.getLogger(__name__)

# Frecuencia del polling de RSS. Ver CLAUDE.md, Fase 2 -- "Scheduler" para el
# razonamiento detrás del intervalo uniforme de 15 minutos.
INGEST_INTERVAL_MINUTES = 15

scheduler = AsyncIOScheduler()


def _job_ingesta_programada() -> None:
    """Job del scheduler: corre el pipeline de ingesta para todos los medios activos."""
    with Session(get_engine()) as session:
        resultados = ingerir_todos_los_medios(session)
        logger.info(f"Ingesta programada completada: {resultados}")


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


@app.get("/test-db")
def test_db(session: Session = Depends(get_session)):
    """
    Endpoint temporal de verificación: inserta un `Medio` de prueba en la base
    de datos y confirma que la conexión y el modelado están funcionando.
    """
    medio_prueba = Medio(
        nombre="Medio de Prueba",
        url_base="https://ejemplo.com",
        feed_rss="https://ejemplo.com/rss",
    )

    session.add(medio_prueba)
    session.commit()
    session.refresh(medio_prueba)

    return {
        "status": "ok",
        "mensaje": "Conexión e inserción de prueba exitosas.",
        "medio_creado": {
            "id": medio_prueba.id,
            "nombre": medio_prueba.nombre,
            "url_base": medio_prueba.url_base,
            "feed_rss": medio_prueba.feed_rss,
            "activo": medio_prueba.activo,
        },
    }


@app.post("/ingest")
def ingest(session: Session = Depends(get_session)):
    """
    Corre el pipeline de ingesta a demanda para todos los medios activos.
    Uso manual durante desarrollo, y como fallback operativo si el scheduler
    se cae -- ver CLAUDE.md, Fase 2, "Endpoint manual POST /ingest".
    """
    resultados = ingerir_todos_los_medios(session)
    return {"status": "ok", "resultados": resultados}
