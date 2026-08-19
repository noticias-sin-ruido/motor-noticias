import logging
import threading
from contextlib import asynccontextmanager
from typing import Callable, Optional

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse
from sqlmodel import Session

from .config import settings
from .database import get_engine, get_session, init_db, verificar_conexion
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
from .services.webhook_delivery import entregar_pendientes
from .tiempo import ahora_local

logger = logging.getLogger(__name__)

# --- Márgenes del scheduler (backlog post-1.0, etapa 4) ---
# A diferencia del intervalo (`settings.INGEST_INTERVAL_MINUTES`, que se calibra
# con datos), estos tres son estructurales: no se tocan por entorno.

# Corridas simultáneas del pipeline. **Uno, y no es negociable.** Es el default
# de APScheduler, pero se declara porque el valor correcto no es obvio y la
# tentación de subirlo "para que se ponga al día" es real: dos pipelines
# concurrentes se pisarían en la asignación de clusters y podrían sintetizar dos
# veces el mismo hecho -- que cuesta plata en Gemini y que, una vez entregado al
# back-end, no se retracta.
SCHEDULER_MAX_INSTANCES = 1

# Si se acumularon varias corridas pendientes, se ejecuta una sola. También es
# el default, y también se declara: lo contrario sería encadenar corridas
# seguidas después de una demora, justo cuando el sistema viene atrasado.
SCHEDULER_COALESCE = True

# Segundos de atraso tolerados entre el momento en que la corrida estaba
# programada y el momento en que se la lanza. El default de APScheduler es **1
# segundo**, que descarta la corrida ante cualquier demora mínima.
#
# 300 s = 5 minutos: una corrida que arranca hasta 5 minutos tarde sigue
# haciendo trabajo útil con 10 de margen antes de la siguiente. Más allá de eso
# la próxima está más cerca que lo que vale la atrasada, y como el pipeline es
# idempotente saltearla no pierde nada. `None` (gracia infinita) sería peor:
# dejaría entrar una corrida absurdamente tarde justo antes de la siguiente.
SCHEDULER_MARGEN_ATRASO_SEGUNDOS = 300

# Fracción del intervalo a partir de la cual una corrida se considera larga y se
# avisa. Es una FRACCIÓN y no un número de segundos a propósito: si el intervalo
# cambia, el umbral lo sigue solo. Al 50% todavía queda margen para reaccionar
# antes de que las corridas empiecen a solaparse.
SCHEDULER_UMBRAL_CORRIDA_LARGA = 0.5

scheduler = AsyncIOScheduler()


def _avisar_corrida_perdida(evento) -> None:
    """
    Avisa cuando el scheduler pierde una corrida. Las tres formas son silenciosas.

    - `EVENT_JOB_MAX_INSTANCES`: la corrida anterior seguía viva. Es el síntoma
      de que el pipeline se pasó del intervalo.
    - `EVENT_JOB_MISSED`: el scheduler llegó tarde a lanzarla, más de
      `SCHEDULER_MARGEN_ATRASO_SEGUNDOS`.
    - `EVENT_JOB_ERROR`: el job levantó una excepción. `_correr_paso` protege
      cada paso, pero no la apertura de la sesión que los envuelve: con la base
      caída, el fallo se escapa por acá.

    **El aviso se manda en un hilo aparte, y eso no es opcional.** Este listener
    corre DENTRO del event loop -- `AsyncIOScheduler.wakeup` está decorado con
    `@run_in_event_loop` y `BaseScheduler._dispatch_event` invoca los listeners
    sincrónicamente-- y `enviar_alerta` abre una conexión SMTP bloqueante.
    Llamarla derecho congelaría la API entera mientras dure el intercambio, y un
    servidor SMTP colgado la dejaría sin responder.

    No se usa el executor del loop porque es el mismo que corre el pipeline: con
    una corrida larga en curso, el aviso de que la corrida es larga quedaría
    encolado detrás de ella.
    """
    if evento.code == EVENT_JOB_MAX_INSTANCES:
        motivo = "se salteó porque la anterior todavía estaba corriendo"
        clave = "scheduler:solapada"
    elif evento.code == EVENT_JOB_MISSED:
        motivo = (
            f"se descartó por llegar más de {SCHEDULER_MARGEN_ATRASO_SEGUNDOS} s tarde"
        )
        clave = "scheduler:atrasada"
    else:
        error = getattr(evento, "exception", None)
        motivo = f"terminó con una excepción: {type(error).__name__}: {error}"
        clave = "scheduler:error"

    # `JobSubmissionEvent` trae `scheduled_run_times` (plural) y
    # `JobExecutionEvent` trae `scheduled_run_time`. Se contemplan las dos.
    programada = getattr(evento, "scheduled_run_time", None) or getattr(
        evento, "scheduled_run_times", None
    )

    logger.error(f"Corrida perdida del job '{evento.job_id}': {motivo}")
    hilo = threading.Thread(
        target=enviar_alerta,
        kwargs={
            "asunto": f"[Sin Ruido] Corrida perdida del pipeline ({evento.job_id})",
            "cuerpo": (
                f"Una corrida del pipeline {motivo}.\n\n"
                f"Programada para: {programada}\n\n"
                "El pipeline es idempotente, así que la corrida siguiente retoma "
                "sola. Si esto se repite, el diagnóstico está en cuánto tarda "
                "cada corrida: buscar 'utilización' en los logs."
            ),
            "clave": clave,
        },
        daemon=True,
    )
    hilo.start()


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
    # Hora argentina, que es la que mira quien opera esto. Ver `src/tiempo.py`.
    arranque = ahora_local()
    logger.info(f"=== Pipeline arranca {arranque:%d/%m %H:%M:%S} (UTC-3) ===")

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

        # Es el único paso que condiciona a otro. Sintetizar sin haber
        # consolidado publicaría dos veces el mismo hecho, y una publicación ya
        # entregada al backend no se retracta.
        if fusion is None:
            logger.error("Se omite la síntesis porque falló la fusión de clusters")
        else:
            _correr_paso(session, "síntesis", sintetizar_pendientes)

        # La entrega sí corre igual, porque es un barrido de todo lo pendiente y
        # no un envío de lo recién generado: lo que quedó sin entregar de
        # corridas anteriores no tiene por qué esperar a que se arregle la
        # fusión. Por lo mismo tampoco necesita un job de reintento aparte.
        _correr_paso(session, "entrega al backend", entregar_pendientes)

    fin = ahora_local()
    duracion = (fin - arranque).total_seconds()
    intervalo = settings.INGEST_INTERVAL_MINUTES * 60
    utilizacion = duracion / intervalo

    # La utilización se loguea en cada corrida, no solo cuando molesta: es el
    # dato con el que se calibra el intervalo, y para eso hacen falta las
    # corridas normales tanto como las lentas.
    logger.info(
        f"=== Pipeline termina {fin:%d/%m %H:%M:%S} (UTC-3) — "
        f"{duracion:.1f} s — utilización {utilizacion:.1%} del ciclo ==="
    )

    # El canario del techo. Una corrida que se pasa del intervalo hace que la
    # siguiente se saltee (`max_instances=1`), así que conviene enterarse bien
    # antes de llegar ahí y no cuando ya se están perdiendo ciclos.
    if utilizacion >= SCHEDULER_UMBRAL_CORRIDA_LARGA:
        logger.warning(
            f"La corrida usó el {utilizacion:.1%} del ciclo de "
            f"{settings.INGEST_INTERVAL_MINUTES} min ({duracion:.1f} s)"
        )
        # Acá sí se llama directo, a diferencia del listener: el job corre en el
        # threadpool del executor, no en el event loop, así que bloquear en SMTP
        # no congela la API.
        enviar_alerta(
            asunto="[Sin Ruido] El pipeline se está acercando al techo del ciclo",
            cuerpo=(
                f"La última corrida tardó {duracion:.1f} s, el {utilizacion:.1%} del "
                f"ciclo de {settings.INGEST_INTERVAL_MINUTES} minutos.\n\n"
                "Si llega al 100% la corrida siguiente se saltea. Opciones: subir "
                "INGEST_INTERVAL_MINUTES, o paralelizar la extracción entre medios "
                "(ver specs/change_logs.md, etapa 3 del backlog punto 1)."
            ),
            clave="scheduler:corrida-larga",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: habilita la extensión pgvector, crea las tablas si no existen,
    # y arranca el scheduler embebido (ver CLAUDE.md, Fase 2 -- "Scheduler").
    init_db()
    scheduler.add_job(
        _job_ingesta_programada,
        "interval",
        minutes=settings.INGEST_INTERVAL_MINUTES,
        id="ingesta_rss",
        max_instances=SCHEDULER_MAX_INSTANCES,
        coalesce=SCHEDULER_COALESCE,
        misfire_grace_time=SCHEDULER_MARGEN_ATRASO_SEGUNDOS,
    )
    # Sin esto, las tres formas de perder una corrida terminan en un WARNING de
    # la librería sobre un stdout que no se persiste. Ver `_avisar_corrida_perdida`.
    scheduler.add_listener(
        _avisar_corrida_perdida,
        EVENT_JOB_MAX_INSTANCES | EVENT_JOB_MISSED | EVENT_JOB_ERROR,
    )
    scheduler.start()
    yield
    # Shutdown.
    scheduler.shutdown()


app = FastAPI(
    title="Sin Ruido — API",
    description=(
        "Motor backend para ingesta, vectorización y síntesis neutra de noticias.\n\n"
        "Software libre bajo AGPL-3.0. Código fuente: "
        "https://github.com/noticias-sin-ruido/motor-noticias"
    ),
    # La versión del producto, la misma del tag `v1.0.0` y del User-Agent de la
    # ingesta. NO es `VERSION_PAYLOAD` de `webhook_delivery`: esa versiona el
    # contrato con el back-end y se mueve sola, solo cuando cambia la forma del
    # payload. Subir la app no la toca.
    version="1.0.0",
    # La sección 13 de la AGPL pide que un programa accesible por red le ofrezca
    # a sus usuarios la forma de conseguir el código. Declararlo acá lo publica
    # en `/docs` y en el esquema OpenAPI, que es la interfaz que el servicio
    # realmente expone.
    license_info={
        "name": "AGPL-3.0-or-later",
        "url": "https://www.gnu.org/licenses/agpl-3.0.html",
    },
    lifespan=lifespan,
)


@app.get("/")
def root(session: Session = Depends(get_session)):
    """
    Salud del servicio. Verifica conectividad real a la base -- es lo que usa
    el HEALTHCHECK del Dockerfile para decidir si el contenedor está sano. La
    hora sirve para verificar el reloj del contenedor.
    """
    db_ok = verificar_conexion(session)
    payload = {
        "status": "ok" if db_ok else "degradado",
        "database": "ok" if db_ok else "error",
        "environment": settings.ENVIRONMENT,
        "hora_local": ahora_local().isoformat(timespec="seconds"),
    }
    if not db_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload


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


@app.post("/deliver")
def deliver(forzar: bool = False, session: Session = Depends(get_session)):
    """
    Empuja al back-end las síntesis que quedaron sin entregar.

    Es el mismo barrido que corre el scheduler, expuesto para disparo manual.
    `forzar=true` incluye además las que agotaron `WEBHOOK_MAX_INTENTOS`: es lo
    que se usa cuando el back-end estuvo rechazando por un problema suyo y hay
    que reenviarles lo trabado una vez resuelto.
    """
    stats = entregar_pendientes(session, forzar=forzar)
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
