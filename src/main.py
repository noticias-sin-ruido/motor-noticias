import logging
from contextlib import asynccontextmanager
from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Query
from sqlmodel import Session

from .config import settings
from .database import get_engine, get_session, init_db
from .services.alerts import enviar_alerta
from .services.clustering import (
    agrupar_pendientes,
    cerrar_clusters_vencidos,
    fusionar_clusters_duplicados,
)
from .services.ingestion import ingerir_todos_los_medios
from .services.search import buscar_noticias_similares, listar_clusters
from .services.synthesis import sintetizar_pendientes
from .services.vectorization import vectorizar_pendientes

logger = logging.getLogger(__name__)

# Frecuencia del polling de RSS. Ver specs/change_logs.md, Fase 2 -- "Scheduler"
# para el razonamiento detrás del intervalo uniforme de 15 minutos.
INGEST_INTERVAL_MINUTES = 15

scheduler = AsyncIOScheduler()


def _correr_paso(session: Session, nombre: str, funcion: Callable) -> Optional[dict]:
    """
    Corre un paso del pipeline. Si falla, avisa y devuelve None sin cortar.

    El `rollback()` no es opcional: después de una excepción de base la sesión
    queda inutilizable, y sin él los pasos siguientes fallarían en cascada por
    un motivo distinto al original — que es lo peor posible para diagnosticar.
    """
    try:
        resultado = funcion(session)
        logger.info(f"{nombre}: {resultado}")
        return resultado
    except Exception as error:
        session.rollback()
        logger.exception(f"Falló el paso '{nombre}' del pipeline")
        enviar_alerta(
            asunto=f"[Sin Ruido] Falló el paso '{nombre}' del pipeline",
            cuerpo=f"{type(error).__name__}: {error}",
            clave=f"pipeline:{nombre}",
        )
        return None


def _job_ingesta_programada() -> None:
    """
    Job del scheduler: el pipeline completo, de los feeds a las síntesis.

    Los pasos van encadenados y no en jobs propios porque cada uno depende del
    anterior: sin noticias nuevas no hay nada que vectorizar, sin embeddings no
    hay nada que agrupar.

    **Un paso que falla no frena a los siguientes**, salvo la fusión. Todos son
    idempotentes —la ingesta deduplica por `guid`, la vectorización busca
    `embedding IS NULL`, el agrupamiento reevalúa las sueltas, la fusión itera
    hasta el punto fijo— así que la corrida siguiente retoma sola donde quedó.
    Esa idempotencia es la contingencia real; las alertas son para enterarse.
    """
    with Session(get_engine()) as session:
        _correr_paso(session, "ingesta", ingerir_todos_los_medios)
        _correr_paso(session, "vectorización", vectorizar_pendientes)

        # El cierre va ANTES del agrupamiento para que los clusters vencidos no
        # sigan capturando noticias nuevas en esta misma corrida.
        _correr_paso(session, "cierre de clusters", cerrar_clusters_vencidos)
        _correr_paso(session, "agrupamiento", agrupar_pendientes)

        # La fusión va antes de la síntesis: primero se arma todo y recién ahí
        # se consolidan los clusters que quedaron describiendo el mismo hecho.
        fusion = _correr_paso(session, "fusión de clusters", fusionar_clusters_duplicados)

        # Es el único paso que corta la cadena. Sintetizar sin haber consolidado
        # publicaría dos veces el mismo hecho, y una publicación ya entregada al
        # backend no se retracta.
        if fusion is None:
            logger.error("Se omite la síntesis porque falló la fusión de clusters")
            return

        _correr_paso(session, "síntesis", sintetizar_pendientes)


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


@app.post("/synthesize")
def synthesize(session: Session = Depends(get_session)):
    """
    Genera a demanda las síntesis de los clusters con material nuevo.

    Mismo criterio que `/ingest` y `/cluster`: disparo manual y fallback si el
    scheduler no corrió. Es idempotente — un cluster sin material nuevo desde su
    último intento no se vuelve a sintetizar, así que llamarlo dos veces seguidas
    no duplica publicaciones ni gasta de más.
    """
    stats = sintetizar_pendientes(session)
    return {"status": "ok", **stats}


@app.post("/cluster")
def cluster(session: Session = Depends(get_session)):
    """
    Cierra los clusters vencidos, agrupa las noticias sueltas y fusiona los
    clusters que quedaron describiendo el mismo evento.

    El cierre corre primero para que un cluster ya vencido no capture noticias
    nuevas en la misma pasada; la fusión, al final, sobre lo ya armado.
    """
    cierre = cerrar_clusters_vencidos(session)
    agrupamiento = agrupar_pendientes(session)
    fusion = fusionar_clusters_duplicados(session)
    return {
        "status": "ok",
        "cierre": cierre,
        "agrupamiento": agrupamiento,
        "fusion": fusion,
    }


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
