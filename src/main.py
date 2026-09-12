import logging
import threading
from contextlib import asynccontextmanager
from typing import Annotated, Any, Callable, Dict, List, Optional

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Body, Depends, FastAPI, Header, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .auth import (
    RUTAS_ABIERTAS,
    avisar_si_esta_abierta,
    exigir_token,
    exigir_token_estricto,
    hay_token,
)
from .config import VERSION, settings
from .database import get_engine, get_session, init_db, verificar_conexion
from .logging_config import configurar_logging
from .models import (
    Adaptador,
    Cluster,
    ConfiguracionAlertas,
    ConfiguracionEntrega,
    Medio,
    ModeloIA,
)
from .models.alertas import MAX_LARGO_MAIL
from .services.alerts import enviar_alerta
from .services.corridas import (
    cerrar_corrida,
    estado_del_pipeline,
    iniciar_corrida,
)
from .services.alertas import (
    DestinoInvalido as DestinoDeAlertaInvalido,
    configuracion as configuracion_de_alertas,
    guardar_destinos as guardar_destinos_de_alerta,
    marcar_prueba_ok as marcar_prueba_de_alerta_ok,
)
from .services.panel_medios import panel_de_medios
from .services.medios import (
    FeedInservible,
    host_normalizado,
    hosts_ajenos,
    sondear as sondear_medio,
    validar_url_de_logo,
)
from .services.modelos import modelo_activo, sondear
from .services.proveedores import (
    VARIABLE_UNICA,
    ErrorDeProveedor,
    ProveedorNoConfigurado,
    leer_api_key,
    validar_nombre_de_variable,
)
from .services.clustering import (
    agrupar_pendientes,
    alcanza_el_minimo_de_medios,
    cerrar_clusters_vencidos,
    fusionar_clusters_duplicados,
)
from .services.ingestion import ingerir_todos_los_medios
from .services.purga import purgar_cuerpos_vencidos
from .services.search import (
    CursorInvalido,
    buscar_noticias_similares,
    detalle_de_sintesis,
    listar_clusters,
    listar_sintesis,
)
from .services.synthesis import (
    _RESOLVER,
    SintesisBloqueada,
    SintesisFallida,
    SintesisSinConfigurar,
    hay_material_nuevo,
    sintetizar_cluster,
    sintetizar_pendientes,
)
from .services.vectorization import vectorizar_pendientes
from .services.entrega import (
    DestinoInvalido,
    configuracion as configuracion_de_entrega,
    guardar_url,
    validar_url_de_entrega,
)
from .services.webhook_delivery import entregar_pendientes, hay_destino_de_entrega
from .tiempo import a_local, ahora_local

logger = logging.getLogger(__name__)

# --- Márgenes del scheduler (backlog post-1.0, etapa 4) ---
# A diferencia del intervalo (`settings.INGEST_INTERVAL_MINUTES`, que se calibra
# con datos), estos tres son estructurales: no se tocan por entorno.

# Corridas simultáneas del pipeline. **Uno, y no es negociable.** Es el default
# de APScheduler, pero se declara porque el valor correcto no es obvio y la
# tentación de subirlo "para que se ponga al día" es real: dos pipelines
# concurrentes se pisarían en la asignación de clusters y podrían sintetizar dos
# veces el mismo hecho -- que cuesta plata con cualquier proveedor y que, una vez
# entregado al back-end, no se retracta.
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

# --- Cotas de entrada (tanda 3 de la auditoría) ---

# Techo de los `id` que llegan por la ruta.
#
# `medio.id` y `modelo_ia.id` son `integer` en Postgres —32 bits, consultado al
# esquema vivo y no supuesto—, así que un `id` más grande no es "no encontrado"
# sino un valor que la columna no puede ni representar. Sin la cota, `PATCH
# /medios/99999999999999999999999` reventaba con `OverflowError` (medido en
# SQLite, que es donde corre la suite): un 500 por una entrada mala, justo lo
# que el resto de la API no hace. No filtra nada; contradice la regla, y la
# regla es lo que hace que un 500 signifique "se rompió algo nuestro".
#
# `ge=1` del otro lado porque las secuencias arrancan en 1: un `id` negativo es
# un error de quien llama y merece decirlo, no un 404 que sugiere que existía.
MAX_ID = 2**31 - 1


# Largo máximo de cualquier URL que entre por la API y se guarde.
#
# 2048 es el techo de hecho: es el límite histórico de Internet Explorer, y por
# eso es el número bajo el que se quedó todo lo que quiere ser alcanzable. La
# URL más larga del roster medido tiene 62 caracteres
# (`ciudad.com.ar/arc/outboundfeeds/rss/?outputType=xml`), así que sobra por 33
# veces. Sin esta cota se persistían 500 KB en `url_base` — comprobado antes del
# arreglo, se guardaban y `GET /medios` los devolvía.
MAX_LARGO_URL = 2048

# Cuántos feeds distintos se aceptan por medio.
#
# El número lo fija el peor caso de latencia y no el gusto: el sondeo consulta
# cada feed con `TIMEOUT_SONDEO_SEGUNDOS` (10 s), así que veinte feeds que no
# respondan ocupan un worker 200 s. Es acotado y reportable; sin cota no lo era.
#
# Contra la realidad medida sobra: los 7 medios del roster usan **un** feed cada
# uno, y el experimento más grande que se hizo —sumar feeds de sección a La
# Nación y TN— llegó a 8. Se eligió el lado generoso porque desde la tanda 1
# `POST /medios` pide token: el atacante anónimo ya no existe, y lo que esta
# cota frena hoy es sobre todo un error de tipeo como el que destapó el ataque
# (500 copias del mismo feed en un solo POST).
MAX_FEEDS_POR_MEDIO = 20

# Largo máximo del cursor de paginación.
#
# Es un `fecha|id` en base64 -- unos 40 caracteres. 200 deja aire de sobra y
# cierra la puerta a que alguien mande medio megabyte por la query string,
# que es la misma cota que la tanda 3 le puso a todo lo que entra por la API.
MAX_LARGO_CURSOR = 200


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


def _correr_paso(
    session: Session,
    nombre: str,
    funcion: Callable,
    registro: Optional[Dict[str, Any]] = None,
) -> Optional[dict]:
    """
    Corre un paso del pipeline. Si falla, avisa y devuelve None sin cortar.

    El `rollback()` no es opcional: después de una excepción de base la sesión
    queda inutilizable, y sin él los pasos siguientes fallarían en cascada por
    un motivo distinto al original — que es lo peor posible para diagnosticar.

    **`registro` es el dict de la corrida**, y anotar acá y no en cada llamada
    es lo que hace que un paso nuevo no pueda quedar fuera del historial por
    olvido. Un paso que falló queda como `None`, que es distinto de un paso
    ausente: `None` es "se intentó y no salió", ausente es "no llegó a correr".
    """
    try:
        resultado = funcion(session)
        logger.info(f"{nombre}: {resultado}")
        if registro is not None:
            registro[nombre] = resultado
        return resultado
    except Exception as error:
        session.rollback()
        if registro is not None:
            registro[nombre] = None
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
        # La fila se abre ANTES del primer paso, así una corrida que muere a
        # mitad de camino igual deja rastro. Se guarda el id y no el objeto:
        # `_correr_paso` hace `rollback()` cuando un paso falla, y sostener una
        # fila viva a través de eso es la trampa del `expunge` que este repo ya
        # documentó dos veces. Ver `services/corridas.iniciar_corrida`.
        corrida_id = iniciar_corrida(session)
        pasos: Dict[str, Any] = {}

        _correr_paso(session, "ingesta", ingerir_todos_los_medios, pasos)
        _correr_paso(session, "vectorización", vectorizar_pendientes, pasos)

        # El cierre va ANTES del agrupamiento para que los clusters vencidos no
        # sigan capturando noticias nuevas en esta misma corrida.
        _correr_paso(session, "cierre de clusters", cerrar_clusters_vencidos, pasos)
        _correr_paso(session, "agrupamiento", agrupar_pendientes, pasos)

        # La fusión va antes de la síntesis: primero se arma todo y recién ahí
        # se consolidan los clusters que quedaron describiendo el mismo hecho.
        fusion = _correr_paso(
            session, "fusión de clusters", fusionar_clusters_duplicados, pasos
        )

        # Es el único paso que condiciona a otro. Sintetizar sin haber
        # consolidado publicaría dos veces el mismo hecho, y una publicación ya
        # entregada al backend no se retracta.
        if fusion is None:
            logger.error("Se omite la síntesis porque falló la fusión de clusters")
        else:
            _correr_paso(session, "síntesis", sintetizar_pendientes, pasos)

        # La entrega sí corre igual, porque es un barrido de todo lo pendiente y
        # no un envío de lo recién generado: lo que quedó sin entregar de
        # corridas anteriores no tiene por qué esperar a que se arregle la
        # fusión. Por lo mismo tampoco necesita un job de reintento aparte.
        _correr_paso(session, "entrega al backend", entregar_pendientes, pasos)

        # Al final a propósito: nada de lo que hizo esta corrida depende de que
        # la purga haya pasado antes. Idempotente como el resto — una noticia
        # ya purgada no vuelve a tocarse — así que corre todos los ciclos y no
        # necesita su propio disparador.
        _correr_paso(session, "purga de cuerpos", purgar_cuerpos_vencidos, pasos)

    fin = ahora_local()
    duracion = (fin - arranque).total_seconds()
    intervalo = settings.INGEST_INTERVAL_MINUTES * 60
    utilizacion = duracion / intervalo

    # **El registro va acá y no adentro del `with`**: la utilización recién se
    # conoce cuando la sesión de los pasos ya cerró, así que se abre una propia
    # y corta. Que falle no puede tumbar la corrida -- ver `cerrar_corrida`.
    with Session(get_engine()) as session:
        cerrar_corrida(session, corrida_id, pasos, duracion, utilizacion)

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
    #
    # El logging va PRIMERO, antes que cualquier otra cosa del arranque: lo que
    # se emita antes de esta línea no tiene handler donde salir. `init_db` puede
    # fallar por una migración pendiente y el aviso de la API abierta es un
    # WARNING que hay que ver sí o sí, así que los dos van después.
    configurar_logging()
    init_db()
    avisar_si_esta_abierta()
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


def _puerta(request: Request, authorization: Optional[str] = Header(default=None)) -> None:
    """
    La comprobación de token que corre antes de cada endpoint.

    Existe para saltear las rutas abiertas —la salud y la documentación— sin
    tener que declarar la dependencia ruta por ruta. Ver `auth.py`.
    """
    if request.url.path in RUTAS_ABIERTAS:
        return
    exigir_token(authorization)


app = FastAPI(
    title="Sin Ruido — API",
    description=(
        "Motor backend para ingesta, vectorización y síntesis neutra de noticias.\n\n"
        "Software libre bajo AGPL-3.0. Código fuente: "
        "https://github.com/noticias-sin-ruido/motor-noticias"
    ),
    # La versión del producto, la misma del tag `v1.1.0` y del User-Agent de la
    # ingesta. NO es `VERSION_PAYLOAD` de `webhook_delivery`: esa versiona el
    # contrato con el back-end y se mueve sola, solo cuando cambia la forma del
    # payload. Subir la app no la toca — de hecho la 1.1.0 no la movió.
    #
    # **1.1.0 y no 2.0.0 fue una decisión, no un descuido.** Lo que esta versión
    # cambió para el consumidor es nada: el payload es idéntico y la API HTTP es
    # retrocompatible (el token de operador es opt-in). Lo que sí rompe es la
    # **configuración** de quien actualiza un despliegue existente: las
    # variables `GEMINI_*` ya no se leen, y la migración deja la fila de
    # `modelo_ia` apagada, así que la síntesis no corre hasta activarla. Eso va
    # avisado en grande en el README en vez de escondido en un número.
    version=VERSION,
    # La sección 13 de la AGPL pide que un programa accesible por red le ofrezca
    # a sus usuarios la forma de conseguir el código. Declararlo acá lo publica
    # en `/docs` y en el esquema OpenAPI, que es la interfaz que el servicio
    # realmente expone.
    license_info={
        "name": "AGPL-3.0-or-later",
        "url": "https://www.gnu.org/licenses/agpl-3.0.html",
    },
    lifespan=lifespan,
    # **La puerta se pone acá y no en cada ruta**, y la diferencia importa: un
    # endpoint que se agregue mañana nace protegido en vez de nacer abierto
    # hasta que alguien se acuerde del decorador. El modo de fallo correcto para
    # una lista de rutas que va a crecer es "protegido salvo que se diga lo
    # contrario", y la excepción está escrita en un solo lugar
    # (`auth.RUTAS_ABIERTAS`).
    dependencies=[Depends(_puerta)],
)


@app.get("/")
def root(session: Session = Depends(get_session)):
    """
    Salud del servicio. Verifica conectividad real a la base -- es lo que usa
    el HEALTHCHECK del Dockerfile para decidir si el contenedor está sano. La
    hora sirve para verificar el reloj del contenedor.

    **`exige_token` y `entrega_configurada` son booleanos, y eso es a propósito.**
    Esta ruta está en `auth.RUTAS_ABIERTAS`: contesta sin credencial. Decir *si*
    la API pide token no agrega nada que un pedido sin token no revele igual, y
    decir *si* hay un destino de entrega tampoco expone nada — pero **la URL del
    destino no sale de acá**, ni siquiera recortada. Quien la quiera leer pasa
    por `GET /entrega`, que exige token siempre.

    Los dos existen porque la cabina no tiene otra forma de saberlo, y sin ellos
    hace dos cosas mal: pide un token que el motor quizás no exige, y marca
    síntesis como "sin entregar" cuando no hay a dónde entregar.

    **`version` también sale en ruta abierta, y no agrega superficie.** Ya se
    publica en `/docs` y en el esquema OpenAPI, así que decirla acá no le cuenta
    a nadie nada que no pudiera leer igual. Está para que la cabina compruebe
    que la ventana y el motor son el mismo par: desde la 1.2.0 se numeran juntos
    y un par desparejo no se rompe, se comporta raro, que es peor.
    """
    db_ok = verificar_conexion(session)
    payload = {
        "status": "ok" if db_ok else "degradado",
        "database": "ok" if db_ok else "error",
        "environment": settings.ENVIRONMENT,
        "hora_local": ahora_local().isoformat(timespec="seconds"),
        # **La version no depende de la base**, asi que se informa igual en el 503
        # de abajo: es una propiedad del codigo que esta corriendo, y es
        # justamente cuando algo anda mal cuando sirve saber cual es.
        "version": VERSION,
        "exige_token": hay_token(),
        # **`entrega_configurada` se consulta sólo si la base contestó**, y el
        # orden es lo único que hace que esta ruta cumpla su contrato. El destino
        # vive en una fila desde el punto 11, así que leerlo es una consulta más:
        # hacerla igual, después de haber comprobado que la base no responde,
        # devolvía **500 en vez de 503**. Verificado parando `sin_ruido_db`
        # contra el motor real: 500 las tres veces.
        #
        # El 503 es contrato y no un detalle: la cabina distingue con él "la base
        # no está" (503 -> migrando) de "el motor no está" (conexión rechazada ->
        # arrancando). Ver `app/src-tauri/src/motor.rs`.
        #
        # Con la base caída el valor **no se sabe**, y `False` es lo que se
        # informa. No es una mentira escondida: la misma respuesta trae
        # `status: degradado` y `database: error`, que es lo que dice que este
        # campo no significa nada en esa respuesta.
        "entrega_configurada": hay_destino_de_entrega(session) if db_ok else False,
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
def vectorize(
    # `ge=1` y no solo un tipo: sin la cota, `?limite=-1` devolvía
    # `{"pendientes": -1}` con 200 — no vectorizaba nada, pero informaba un
    # dato imposible como si fuera medido. Es la misma forma que ya usan
    # `/search` y `/clusters`; a este endpoint se le había pasado. Sin techo
    # a propósito: el backlog real ya lo acota, pedir de más no cuesta nada.
    limite: Optional[int] = Query(None, ge=1),
    session: Session = Depends(get_session),
):
    """
    Vectoriza a demanda las noticias que todavía no tienen embedding.

    Es idempotente (no revectoriza lo ya procesado). `limite` sirve para
    procesar un backlog grande de a tandas en vez de todo de una.
    """
    stats = vectorizar_pendientes(session, limite=limite)
    return {"status": "ok", **stats}


def _modelo_elegido(session: Session, modelo_id: Optional[int]) -> Optional[ModeloIA]:
    """
    El modelo que pidieron por id, o `None` si no pidieron ninguno.

    Levanta `_SinEseModelo` si el id no existe, para que el endpoint conteste
    404 en vez de sintetizar con otro. Que un id equivocado caiga al default
    sería la peor respuesta posible acá: gastaría cuota del proveedor
    equivocado y lo dejaría escrito en `modelo_usado`.
    """
    if modelo_id is None:
        return None
    elegido = session.get(ModeloIA, modelo_id)
    if elegido is None:
        raise _SinEseModelo(modelo_id)
    return elegido


class _SinEseModelo(Exception):
    """El `modelo_id` que pidieron no existe. Ver `_modelo_elegido`."""

    def __init__(self, modelo_id: int):
        self.modelo_id = modelo_id
        super().__init__(f"No existe el modelo {modelo_id}")


@app.post("/synthesize")
def synthesize(
    # **Sin este parámetro es exactamente lo de hoy**, que es lo que llama el
    # scheduler: se arma la cadena y encabeza el modelo activo. Con él se usa
    # ése y solo ése, sin caer a ningún suplente. Ver `sintetizar_pendientes`.
    modelo_id: Optional[int] = Query(None, ge=1, le=MAX_ID),
    session: Session = Depends(get_session),
):
    """
    Genera a demanda las síntesis de los clusters con material nuevo.

    Mismo criterio que `/ingest` y `/cluster`: disparo manual y fallback si el
    scheduler no corrió. Es idempotente — un cluster sin material nuevo desde su
    último intento no se vuelve a sintetizar, así que llamarlo dos veces seguidas
    no duplica publicaciones ni gasta de más.

    `modelo_id` es el modo "todas con el modelo que elijo": sintetiza todo lo
    pendiente con ese proveedor, sin cadena de fallback.
    """
    try:
        elegido = _modelo_elegido(session, modelo_id)
    except _SinEseModelo as error:
        return JSONResponse(
            status_code=404, content={"status": "error", "detalle": str(error)}
        )

    stats = sintetizar_pendientes(session, modelo=elegido)
    return {"status": "ok", **stats}


@app.post("/clusters/{cluster_id}/synthesize")
def synthesize_cluster(
    cluster_id: int = Path(..., ge=1, le=MAX_ID),
    modelo_id: Optional[int] = Query(None, ge=1, le=MAX_ID),
    forzar: bool = False,
    session: Session = Depends(get_session),
):
    """
    Sintetiza **un** cluster puntual, opcionalmente con el modelo que se elija.

    Es el modo "paso a paso": quien mira los clusters decide cuáles valen la
    pena y con qué proveedor sintetizar cada uno. Sirve además para **volver a
    sintetizar con otro modelo** algo que ya salió — las re-síntesis actualizan
    o agregan ángulos, nunca reparten de nuevo, así que el `id` que el back-end
    ya conoce no cambia.

    **No pasa por `clusters_pendientes`**, y es a propósito: ese filtro exige
    además que el material nuevo alcance para un ángulo nuevo, y eso dejaría
    afuera justamente el caso de re-sintetizar un ángulo existente con otro
    modelo. Acá hay alguien eligiendo.

    **Pero sí exige material nuevo, salvo `forzar=true`.** Sin esa guarda, N
    llamadas idénticas eran N llamadas al proveedor: medido, 5 POST seguidos
    daban 5 síntesis reales, contra 1 de `POST /synthesize`. Y como `API_TOKEN`
    es opcional, un doble clic, un reintento por timeout o un script en bucle
    gastan cuota sin que nadie haya decidido gastarla. Repetir con la misma
    entrada tampoco aporta nada: la evidencia y el prompt son los mismos.

    `forzar=true` es la salida deliberada, con el mismo vocabulario que
    `POST /deliver`. En una interfaz, el botón "volver a sintetizar" **es** ese
    acto explícito, así que la fricción no la paga quien usa la app sino quien
    escribe la URL a mano — que es justo donde conviene que se note.

    **Y no sintetiza un cluster que no llega a `MIN_MEDIOS_CLUSTER` medios
    distintos, ni con `forzar`.** Ése no puede producir ningún ángulo
    publicable —`_persistir` los descarta a todos— así que la llamada se
    pagaría para no obtener nada. Es la regla del producto (sin dos voces no
    hay enfoques que comparar) aplicada donde es gratis en vez de después de
    pagar.

    Las dos respuestas que cortan llevan `sintetizado: false` y un `motivo`
    cerrado: `sin_medios_suficientes` o `sin_material_nuevo`.

    Sin `modelo_id` usa el activo. Con él, ése y solo ése: no hay cadena de
    fallback cuando la elección fue explícita.
    """
    cluster = session.get(Cluster, cluster_id)
    if cluster is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "detalle": "No existe ese cluster"},
        )

    # **Incondicional, y no respeta `forzar`.** Un cluster con menos de
    # `MIN_MEDIOS_CLUSTER` medios distintos no puede producir ningún ángulo
    # publicable: `_persistir` los descarta a todos, así que la llamada al
    # proveedor se paga para no obtener nada. Verificado con sonda sobre un
    # cluster `descartado` real: 1 llamada, 0 filas de `Sintesis`.
    #
    # `forzar` significa "re-sintetizá aunque no haya material nuevo", no
    # "gastá en algo imposible", así que acá no aplica: dejarlo pasar solo
    # habilitaría desperdiciar la llamada a mano, sin ningún caso de uso
    # detrás.
    #
    # **Y no se pierde nada al cortar**: un cluster que no llega al mínimo
    # tampoco puede tener una síntesis previa que actualizar —para crearla
    # habría necesitado el mínimo, y a un cluster no se le quitan noticias—,
    # así que la rama de actualización de `_persistir`, que no aplica este
    # filtro, no es una excepción a considerar.
    if not alcanza_el_minimo_de_medios(cluster):
        return {
            "status": "ok",
            "cluster_id": cluster_id,
            "sintetizado": False,
            "motivo": "sin_medios_suficientes",
        }

    # **200 y no 4xx**: no falló nada, el motor decidió no gastar. Un 4xx haría
    # que una interfaz muestre un error ante una condición perfectamente normal.
    # El `motivo` es una categoría cerrada, mismo criterio que `agotados`.
    if not forzar and not hay_material_nuevo(cluster):
        return {
            "status": "ok",
            "cluster_id": cluster_id,
            "sintetizado": False,
            "motivo": "sin_material_nuevo",
        }

    try:
        elegido = _modelo_elegido(session, modelo_id)
    except _SinEseModelo as error:
        return JSONResponse(
            status_code=404, content={"status": "error", "detalle": str(error)}
        )

    try:
        resultado = sintetizar_cluster(session, cluster, elegido or _RESOLVER)
    except SintesisSinConfigurar as error:
        # 422 y no 500: falta configuración, no se rompió nada.
        return JSONResponse(
            status_code=422, content={"status": "error", "detalle": str(error)}
        )
    except SintesisBloqueada as error:
        # También 422, y con su propio mensaje: no es un fallo técnico sino el
        # proveedor rechazando el contenido. No se prueba con otro — ver
        # `_intentar_con_la_cadena`.
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "detalle": f"El proveedor bloqueó el contenido: {error}",
            },
        )
    except SintesisFallida as error:
        # 422 y no 500: un rate limit o un JSON mal armado del proveedor no es
        # que "se rompió algo nuestro" — es la condición más esperable de este
        # endpoint, y antes de esto quedaba indistinguible de un bug real. El
        # mensaje ya viene saneado desde `llamar_modelo`.
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "detalle": f"El proveedor tuvo un problema técnico: {error}",
            },
        )

    # `sintetizado` va en las dos ramas y no solo en la que corta: un campo que
    # aparece a veces obliga a quien consume a escribir `.get(..., True)` y a
    # saber cuál es el default. Con las dos ramas declarándolo, la respuesta se
    # lee sola.
    return {"status": "ok", "cluster_id": cluster_id, "sintetizado": True, **resultado}


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


@app.post("/purge")
def purge(
    solo_contar: bool = Query(
        False,
        description="Mide sin borrar: cuántas noticias y cuántos bytes tocaría.",
    ),
    session: Session = Depends(get_session),
):
    """
    Borra el cuerpo de las noticias huérfanas que ya vencieron su ventana.

    **Irreversible.** El cuerpo no vuelve: la ventana del feed que lo trajo ya
    pasó, así que ni re-ingiriendo se recupera. `solo_contar=true` es la forma
    de comprobar el alcance contra los datos reales antes de tocarlos, y es
    exactamente lo que corre este endpoint sin el flag salvo por el `commit()`
    final — mismo cálculo, misma condición.

    Solo toca noticias sin cluster: una noticia agrupada, por vieja o entregada
    que esté, no se purga acá. Ver `services/purga.py`.
    """
    stats = purgar_cuerpos_vencidos(session, solo_contar=solo_contar)
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


# --- Lectura de las síntesis (backlog punto 14) -----------------------------
#
# Prerrequisito de la app de escritorio: hasta acá el motor no sabía devolver
# lo que produce. Las síntesis salían **solo** empujadas por el webhook, así
# que no había forma de mirar un ángulo sin ir a la base a mano.


@app.get("/sintesis")
def sintesis(
    limite: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(
        None, max_length=MAX_LARGO_CURSOR,
        description="El campo `siguiente` de una respuesta anterior",
    ),
    cluster_id: Optional[int] = Query(None, ge=1, le=MAX_ID),
    entregado: Optional[bool] = Query(
        None, description="Filtra por si ya se entregó al back-end"
    ),
    session: Session = Depends(get_session),
):
    """
    Las síntesis producidas, de la más reciente a la más vieja.

    **Resumidas**: título, tópicos, medios y estado de entrega. El contenido
    —resumen, puntos clave y comparativa— se pide por `GET /sintesis/{id}`,
    porque traer la comparativa completa de veinte ítems para elegir uno es
    pagar la lectura entera para tomar una decisión.

    **Paginación por cursor.** Se toma el campo `siguiente` de una respuesta y
    se manda tal cual en la siguiente llamada; cuando viene `null`, no hay más.
    No es `offset` porque el scheduler inserta síntesis nuevas arriba cada 15
    minutos, y con offset la página 2 repetiría lo que ya se vio.
    """
    try:
        resultado = listar_sintesis(
            session,
            limite=limite,
            cursor=cursor,
            cluster_id=cluster_id,
            entregado=entregado,
        )
    except CursorInvalido as error:
        # 422 y no 500: el cursor es una entrada de quien llama, así que uno
        # mal formado es un pedido inválido y no algo que se rompió acá.
        return JSONResponse(
            status_code=422, content={"status": "error", "detalle": str(error)}
        )

    return {"status": "ok", "cantidad": len(resultado["sintesis"]), **resultado}


@app.get("/sintesis/{sintesis_id}")
def sintesis_detalle(
    sintesis_id: int = Path(..., ge=1, le=MAX_ID),
    session: Session = Depends(get_session),
):
    """
    Una síntesis entera: su comparativa por medio y las notas que la respaldan.

    Es lo único que justifica traer la comparativa completa — acá se está
    leyendo una, no eligiendo entre veinte.
    """
    detalle = detalle_de_sintesis(session, sintesis_id)
    if detalle is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "detalle": "No existe esa síntesis"},
        )
    return {"status": "ok", "sintesis": detalle}


@app.get("/pipeline")
def pipeline(
    historial: int = Query(
        5, ge=1, le=50,
        description="Cuántas corridas devolver en total, la última incluida",
    ),
    session: Session = Depends(get_session),
):
    """
    En qué anda el pipeline: si hay algo corriendo, la última corrida y las previas.

    **El otro faltante que destapó el punto 14.** `GET /` dice si el motor está
    vivo y si la base responde, pero nada del scheduler: ni cuándo corrió por
    última vez, ni qué produjo. Una sala de control que no puede mostrar
    "última corrida hace 12 min, 6 clusters sintetizados" está ciega justo en
    lo que la hace sala de control.

    `corriendo` no sale de preguntar solo si la última quedó sin cerrar: una
    corrida abierta que quedó de un proceso que murió diría "corriendo" para
    siempre. Se exige además que sea reciente, con el ciclo como vara, y la
    otra situación se informa aparte en `huerfana` en vez de esconderla.
    """
    return {"status": "ok", **estado_del_pipeline(session, historial=historial)}


# --- Modelos de IA configurables (backlog punto 2) --------------------------
#
# ⚠️ **Estos tres endpoints no tienen autenticación, como el resto de la API**, y
# son los primeros que aceptan una URL arbitraria para que el motor la llame y
# los primeros que deciden qué credencial se usa. Lo que hay puesto:
#
# - `Adaptador` es un enum cerrado, así que la base no puede elegir qué código
#   se ejecuta.
# - **`api_key_env` no se acepta en el alta.** La credencial vive siempre en la
#   misma variable, así que no hay nada que elegir — y de paso desaparece la
#   primitiva que se armaba con `base_url` propio y una variable ajena.
# - `base_url` se valida: solo http/https, sin credenciales embebidas y sin
#   direcciones link-local.
# - **Ni `api_key_env` ni `base_url` se devuelven en ninguna respuesta.**
# - El cuerpo de la respuesta del proveedor no se refleja en los errores.
#
# **Nada de esto reemplaza a la autenticación.** Quien pueda hacer POST acá
# puede seguir apuntando `base_url` a su propio servidor y quedarse con la key
# de IA del operador. Hasta que exista auth, esto se despliega en una red donde
# solo llega el operador. Ver specs/roadmap.md, punto 9.


class AltaModelo(BaseModel):
    """Lo que hace falta para dar de alta un modelo. Ver `models/modelo_ia.py`."""

    # **Los campos de más se rechazan, no se ignoran.** Es la diferencia entre
    # que alguien mande `api_key_env` y se entere de que ese campo ya no existe,
    # o que se lo descartemos en silencio y se quede creyendo que el motor va a
    # leer la variable que él eligió. En un endpoint donde el campo de más suele
    # ser justamente el que alguien intenta usar para desviar la credencial, el
    # silencio es la peor respuesta.
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=80)
    adaptador: Adaptador
    modelo: str = Field(min_length=1, max_length=200)
    # Acotada por lo mismo que `url_base` en `AltaMedio`: es una URL que entra
    # por la API y se persiste. A dónde puede APUNTAR ya lo decide
    # `MODELO_HOSTS_PERMITIDOS` desde la tanda 2; esto es solo su largo.
    base_url: Optional[str] = Field(default=None, max_length=MAX_LARGO_URL)

    # **El NOMBRE de la variable con la credencial, nunca la credencial.**
    #
    # Hasta multimodelo esto no se aceptaba: con un solo proveedor no hay nada
    # que elegir, y no aceptarlo cerraba de paso la primitiva de exfiltración
    # que la tanda 2 encontró. Ahora hace falta, porque una cadena de fallback
    # solo sirve si el suplente tiene una credencial DISTINTA a la del titular
    # -- si comparten variable, comparten cuota, y caer de uno al otro no
    # resuelve nada. Ver `modelos.cadena_de_modelos`.
    #
    # Sigue sin poder nombrar cualquier variable: el validador de abajo lo acota
    # a `MODELO_API_KEY` o la forma con sufijo.
    api_key_env: str = Field(default=VARIABLE_UNICA, max_length=120)

    # Acotada: sin esto se aceptaba `9999.0` y se lo mandaba tal cual al
    # proveedor. El rango es el que aceptan en común los que nos importan.
    temperatura: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, gt=0)
    prioridad: int = Field(default=100, ge=0)
    # Palancas propias del adaptador —hoy solo el razonamiento de Gemini—. Qué
    # claves se aceptan lo decide cada adaptador y se comprueba al construirlo,
    # o sea antes de guardar nada: ver `proveedores/base.validar_opciones`.
    opciones: Dict[str, Any] = Field(default_factory=dict)
    # No se pide `modo_estructura`: **lo descubre el sondeo**. El operador no
    # tiene por qué saber si su proveedor acepta `response_format` o solo
    # tool-calling, y de hecho la documentación del proveedor puede mentirle.
    activar: bool = False

    @field_validator("api_key_env")
    @classmethod
    def _solo_nuestras_variables(cls, nombre: str) -> str:
        """
        Reusa la misma comprobación que hace la lectura, y falla antes de la red.

        `leer_api_key` ya la corre, y el alta la alcanza igual porque `sondear`
        construye el adaptador -- así que sin este validador la fila tampoco se
        guardaría. Está igual por dos motivos: es un invariante de SEGURIDAD y
        depender de un efecto secundario del sondeo significa que desaparece en
        silencio el día que alguien saltee ese paso; y acá corta antes de
        cualquier pedido al proveedor, en vez de después.
        """
        try:
            return validar_nombre_de_variable(nombre)
        except ProveedorNoConfigurado as error:
            # Pydantic formatea `ValueError`; `ProveedorNoConfigurado` se le
            # escaparía y saldría como un 500.
            raise ValueError(str(error)) from error


def _vista_publica(modelo: ModeloIA) -> dict:
    """
    Lo que se puede devolver de un modelo por una API sin autenticación.

    **Deja afuera `api_key_env` y `base_url`.** No son secretos en sí mismos,
    pero juntos completan una cadena: quien lee el listado sabe qué variable
    nombrar, y con eso más un `base_url` propio consigue que el motor le entregue
    la key del operador en el sondeo. Publicarlos era regalar la mitad del
    trabajo.

    En su lugar va `credencial_configurada`, que es la señal que el operador
    realmente necesita —¿está seteada la variable?— sin decir cuál es.
    """
    datos = modelo.model_dump(exclude={"api_key_env", "base_url"})
    try:
        leer_api_key(modelo)
        datos["credencial_configurada"] = True
    except ProveedorNoConfigurado:
        datos["credencial_configurada"] = False
    return datos


def _nombre_en_uso(session: Session) -> str:
    """
    Con qué se está sintetizando ahora mismo, en una sola representación.

    Antes el GET devolvía `null` y el PATCH un texto para el mismo estado. Dos
    formas de decir lo mismo obligan a quien consume a saber cuál mira.
    """
    activo = modelo_activo(session)
    return activo.nombre if activo else SIN_MODELO


# Qué se responde cuando ninguna fila está activa.
#
# **Es un estado de alarma, no un default.** Hasta la etapa 3 del punto 2 esto
# significaba "corre el camino histórico de Gemini" y el motor sintetizaba
# igual; desde la 4 no hay proveedor de reserva, así que sin fila activa **no se
# sintetiza nada**. El texto lo dice para que nadie lo lea como "anda solo".
SIN_MODELO = "(ninguno activo — la síntesis no corre)"


@app.get("/modelos")
def listar_modelos(session: Session = Depends(get_session)):
    """Los modelos configurados. Ver `_vista_publica` por lo que NO se devuelve."""
    filas = session.exec(select(ModeloIA).order_by(ModeloIA.prioridad, ModeloIA.id)).all()
    return {
        "status": "ok",
        "en_uso": _nombre_en_uso(session),
        "modelos": [_vista_publica(f) for f in filas],
    }


@app.post("/modelos")
def alta_modelo(datos: AltaModelo, session: Session = Depends(get_session)):
    """
    Da de alta un modelo, **después de comprobar que sirve**.

    No es un CRUD: antes de guardar nada se le manda un pedido mínimo y se
    verifica que devuelva la estructura que el motor necesita. El motivo está
    medido — la capa de compatibilidad de Anthropic responde 200 e **ignora el
    esquema en silencio**, así que un alta que solo probara conectividad
    aceptaría un modelo que después rompe la síntesis cada 15 minutos.
    Ver `services/modelos.sondear`.
    """
    duplicado = JSONResponse(
        status_code=409,
        content={"status": "error", "detalle": f"Ya existe un modelo '{datos.nombre}'"},
    )
    if session.exec(select(ModeloIA).where(ModeloIA.nombre == datos.nombre)).first():
        return duplicado

    modelo = ModeloIA(**datos.model_dump(exclude={"activar"}))

    try:
        modo, resumen = sondear(modelo)
    except (ErrorDeProveedor, ProveedorNoConfigurado) as error:
        # 422 y no 500: la configuración que mandaron no sirve. El mensaje del
        # proveedor viaja **saneado** — ver `_mensaje_de_error`, que devuelve
        # solo su campo `error.message` y nunca el cuerpo crudo.
        return JSONResponse(
            status_code=422,
            content={"status": "error", "detalle": str(error)},
        )

    modelo.modo_estructura = modo
    modelo.activo = datos.activar
    if datos.activar:
        # Mismo criterio que el PATCH: como mucho uno prendido. Ver
        # `_apagar_los_demas`. `id_que_queda=None` porque este todavía no tiene
        # id — se apaga todo lo demás y este entra prendido en el mismo commit.
        _apagar_los_demas(session, None)
    session.add(modelo)
    try:
        session.commit()
    except IntegrityError:
        # El chequeo de arriba deja una ventana entre el SELECT y el INSERT.
        # El índice único protege el dato igual; esto protege la respuesta, que
        # si no salía como un 500 por una condición perfectamente normal.
        session.rollback()
        return duplicado
    session.refresh(modelo)

    # **`en_uso` va también acá**, y no es simetría por prolijidad. Era el único
    # de los tres endpoints que no lo devolvía, así que quien daba de alta su
    # modelo con `activar=true` recibía `200` y `"activo": true` sin ninguna
    # forma de enterarse de que el motor estaba sintetizando con otro.
    return {
        "status": "ok",
        "sondeo": resumen,
        "modelo": _vista_publica(modelo),
        "en_uso": _nombre_en_uso(session),
    }


@app.patch("/modelos/{modelo_id}")
def activar_modelo(
    modelo_id: int = Path(..., ge=1, le=MAX_ID),
    activo: bool = Query(...),
    session: Session = Depends(get_session),
):
    """
    Prende o apaga un modelo. **Prender uno apaga a los demás.**

    La exclusividad no es una comodidad: es lo único que hace que la respuesta
    signifique algo. Sin ella, dos filas activas empataban en `prioridad` —el
    default es 100 para todas— y desempataba el `id`, o sea **ganaba la más
    vieja, en silencio**. Quien prendía su modelo recibía `200` y `activo: true`
    mientras el motor seguía sintetizando con otro.

    Y no se pierde nada, porque **la credencial es una sola**: tener dos
    proveedores prendidos a la vez es un estado que no se puede usar. El día que
    exista multimodelo (punto 6-bis del backlog) esto se revisa junto con
    `prioridad`, que es la columna que hoy queda dormida.

    Apagar el último es válido, **pero deja el motor sin sintetizar**: desde la
    etapa 4 no hay proveedor de reserva. Es la marcha atrás si un modelo nuevo
    resulta peor de lo esperado, a condición de prender otro.

    **Activar sondea contra el proveedor, no mira el entorno.** Antes alcanzaba
    con comprobar que la variable existiera, porque cada proveedor tenía la
    suya: si prendías un modelo de Groq sin haber definido su variable, la
    ausencia lo delataba. Con una credencial única eso dejó de ser cierto —
    `MODELO_API_KEY` existe siempre, tenga adentro la key del proveedor que
    tenga—, así que la comprobación pasaba igual cuando el operador se olvidaba
    de cambiar el valor, y el 401 aparecía quince minutos más tarde en el paso
    más caro del pipeline.

    Cuesta una llamada corta (el timeout del sondeo, no el de una síntesis) y a
    cambio el error sale cuando se aprieta el botón, que es cuando hay alguien
    mirando. De paso re-descubre `modo_estructura`: entre el alta y hoy el
    proveedor pudo cambiar de mecanismo, y sale gratis en el mismo viaje.
    """
    modelo = session.get(ModeloIA, modelo_id)
    if modelo is None:
        return JSONResponse(
            status_code=404, content={"status": "error", "detalle": "No existe ese modelo"}
        )

    resumen = None
    if activo:
        try:
            modo, resumen = sondear(modelo)
        except (ErrorDeProveedor, ProveedorNoConfigurado) as error:
            return JSONResponse(
                status_code=422, content={"status": "error", "detalle": str(error)}
            )
        modelo.modo_estructura = modo
        _apagar_los_demas(session, modelo_id)

    modelo.activo = activo
    session.add(modelo)
    session.commit()

    respuesta = {
        "status": "ok",
        "modelo": modelo.nombre,
        "activo": modelo.activo,
        "en_uso": _nombre_en_uso(session),
    }
    if resumen:
        respuesta["sondeo"] = resumen
    return respuesta


def _apagar_los_demas(session: Session, id_que_queda: Optional[int]) -> None:
    """
    Deja como mucho un modelo activo. Ver `activar_modelo`.

    Se hace en la misma transacción que el `activo = True` que lo motiva, así
    que no existe un instante con dos prendidos ni uno con ninguno.
    """
    otros = session.exec(
        select(ModeloIA).where(ModeloIA.activo.is_(True), ModeloIA.id != id_que_queda)
    ).all()
    for otro in otros:
        otro.activo = False
        session.add(otro)
        logger.info(f"Se apaga '{otro.nombre}': solo puede haber un modelo activo")


# ============================================================
# Entrega: el destino lo maneja el operador (backlog punto 11)
# ============================================================
#
# Hasta acá el destino era `WEBHOOK_URL` en el entorno, o sea que cambiarlo era
# editar un archivo y reiniciar un contenedor. Es la misma forma que tenían el
# roster de medios (punto 3) y el modelo de IA (punto 2) antes de sus puntos: una
# decisión que el producto dice que es del operador, implementada como una
# decisión de quien despliega.
#
# **Los dos endpoints exigen token SIEMPRE**, aunque el despliegue haya dejado la
# API abierta, y es la única excepción a la regla de `auth.py`. El motivo está
# escrito en `exigir_token_estricto`: acá se redirige la salida del motor, y las
# síntesis salen **firmadas** — quien reciba una entrega desviada obtiene
# contenido que parece legítimo porque lo es.
#
# **El secreto no se toca desde acá.** `WEBHOOK_SECRET` sigue en el entorno, y de
# él sólo se informa un booleano. Ver `models/entrega.py` por qué la URL puede
# vivir en la base y el secreto no.


class CambioEntrega(BaseModel):
    """El destino nuevo. `null` lo desconfigura y la entrega deja de correr."""

    # Mismo criterio que `AltaModelo` y `AltaMedio`: **los campos de más se
    # rechazan**. Acá importa especialmente, porque el campo de más que alguien
    # va a intentar mandar es `secreto` — y descartárselo en silencio lo dejaría
    # creyendo que el motor lo guardó.
    model_config = ConfigDict(extra="forbid")

    # **Obligatorio y nullable, que no es lo mismo que opcional.** Si tuviera
    # default, un cuerpo `{}` sería indistinguible de "borrá el destino", y el
    # error de tipeo más barato del mundo apagaría la entrega sin decir nada. Así
    # `{}` es un 422 y `{"url": null}` es un borrado explícito.
    url: Optional[str] = Field(..., max_length=MAX_LARGO_URL)


def _vista_entrega(fila: ConfiguracionEntrega) -> dict:
    """
    Lo que se devuelve de la configuración de entrega.

    **Acá sí va la URL**, a diferencia de `GET /`: esta ruta exige token siempre,
    y el operador no puede corregir un destino que no ve. Lo que no sale nunca es
    `WEBHOOK_SECRET`, del que se informa sólo si existe — que es el dato que hace
    falta para entender por qué el barrido no corre.

    **`valido` no es lo mismo que `configurado`, y por eso son dos campos.**
    `configurado` dice que hay una URL guardada; `valido` dice que el motor la va
    a aceptar cuando entregue. Pueden diferir: `guardar_url` valida al escribir,
    pero la fila también llega sembrada por la migración desde un `.env` viejo, o
    editada a mano en la base. Sin este campo, el operador veía
    `configurado: true` mientras el barrido descartaba el destino **y sólo lo
    decía en el log del scheduler**, que corre cada 15 minutos y no expone su
    resultado por ninguna ruta.

    **`GET /` no hace esta comprobación, a propósito.** Es el healthcheck: lo
    golpea Docker cada pocos segundos, y validar implica resolver DNS. Ahí
    `entrega_configurada` sigue significando "hay una URL guardada", que es lo
    que la cabina necesita para no marcar síntesis como "sin entregar". Saber si
    esa URL sirve es una pregunta de operador y se paga donde el operador la
    hace.
    """
    momento = a_local(fila.actualizado_en)
    valido, problema = True, None
    if fila.url:
        try:
            validar_url_de_entrega(fila.url)
        except DestinoInvalido as error:
            valido, problema = False, str(error)

    return {
        "url": fila.url,
        "configurado": bool(fila.url),
        "valido": valido,
        # El mensaje del validador tal cual: dice qué revisar. `None` cuando no
        # hay nada que revisar.
        "problema": problema,
        "secreto_configurado": bool(settings.WEBHOOK_SECRET),
        "actualizado_en": momento.isoformat(timespec="seconds") if momento else None,
    }


@app.get("/entrega", dependencies=[Depends(exigir_token_estricto)])
def ver_entrega(session: Session = Depends(get_session)):
    """A dónde se están entregando las síntesis. Ver `_vista_entrega`."""
    return {"status": "ok", "entrega": _vista_entrega(configuracion_de_entrega(session))}


@app.patch("/entrega", dependencies=[Depends(exigir_token_estricto)])
def cambiar_entrega(datos: CambioEntrega, session: Session = Depends(get_session)):
    """
    Cambia el destino de entrega.

    **No reenvía nada.** Cambiar la URL no toca `Sintesis.enviado_backend`, así
    que el destino nuevo recibe desde la próxima síntesis y no el histórico. La
    alternativa se evaluó y se descartó: son cientos de síntesis firmadas
    saliendo de golpe hacia un back-end que quizá recién se levanta, disparadas
    por lo que para quien lo hace es corregir un tipeo. Un reenvío masivo tiene
    que ser una acción con ese nombre, y ya existe: `POST /deliver?forzar=true`.

    La validación del destino está en `services/entrega.validar_url_de_entrega`,
    que **permite la red interna** —un back-end en la misma máquina es el caso
    normal— y bloquea link-local, que es donde viven los metadata de las nubes.
    """
    try:
        fila = guardar_url(session, datos.url)
    except DestinoInvalido as error:
        # 422 y no 400: la forma del cuerpo es correcta, el valor no sirve. El
        # mensaje del validador viaja tal cual porque dice qué revisar.
        return JSONResponse(
            status_code=422, content={"status": "error", "detalle": str(error)}
        )
    return {"status": "ok", "entrega": _vista_entrega(fila)}


class CambioAlertas(BaseModel):
    """
    La lista de destinos de alerta.

    `model_config` con `extra="forbid"` por lo de siempre: un campo de más se
    rechaza en vez de ignorarse en silencio.
    """

    model_config = ConfigDict(extra="forbid")

    # **Obligatorio y sin default**, igual que `CambioEntrega.url`: un cuerpo
    # vacío tiene que dar 422 y no vaciar la lista de destinos. Vaciarla apaga
    # los avisos por mail, así que tiene que costar decirlo.
    destinos: List[
        Annotated[str, StringConstraints(max_length=MAX_LARGO_MAIL)]
    ] = Field(...)


def _vista_alertas(fila: ConfiguracionAlertas) -> dict:
    """
    Lo que se devuelve de la configuración de alertas.

    **`smtp_configurado` es un booleano y nunca el host ni el usuario.** Es la
    misma regla que `secreto_configurado` en la entrega: la cabina necesita
    saber si el canal puede funcionar, y para eso no hace falta el nombre del
    servidor ni la cuenta.

    **Y `ultima_prueba_ok` es el campo que da sentido a todo esto.** Un destino
    configurado no dice nada: la casilla que se usaba para probar la deshabilitó
    su proveedor y el motor siguió intentando meses contra una dirección muerta.
    Esta fecha es la única que distingue "hay un mail puesto" de "el mail sale".
    """
    momento = fila.actualizado_en
    prueba = fila.ultima_prueba_ok
    return {
        "destinos": list(fila.destinos or []),
        "configurado": bool(fila.destinos),
        "smtp_configurado": bool(settings.SMTP_HOST),
        "actualizado_en": momento.isoformat(timespec="seconds") if momento else None,
        "ultima_prueba_ok": prueba.isoformat(timespec="seconds") if prueba else None,
    }


@app.get("/alertas", dependencies=[Depends(exigir_token_estricto)])
def ver_alertas(session: Session = Depends(get_session)):
    """A quién avisa el motor cuando algo se rompe. Ver `_vista_alertas`."""
    return {"status": "ok", "alertas": _vista_alertas(configuracion_de_alertas(session))}


@app.patch("/alertas", dependencies=[Depends(exigir_token_estricto)])
def cambiar_alertas(datos: CambioAlertas, session: Session = Depends(get_session)):
    """
    Cambia a quién se le avisa.

    **Exige token siempre, aun con la API abierta**, y el motivo es más fuerte
    que en `/entrega`: desviar las alertas es **apagarlas** —quien las recibe
    deja de recibirlas y no se entera— y además convierte al motor en un emisor
    de mails con las credenciales SMTP del operador.

    Una lista vacía es válida y significa "no avisar por mail": el motor cae al
    log, que es lo que `alerts.enviar_alerta` ya hace. Es una decisión, no un
    error de configuración.
    """
    try:
        fila = guardar_destinos_de_alerta(session, datos.destinos)
    except DestinoDeAlertaInvalido as error:
        return JSONResponse(
            status_code=422, content={"status": "error", "detalle": str(error)}
        )
    return {"status": "ok", "alertas": _vista_alertas(fila)}


@app.post("/alertas/probar", dependencies=[Depends(exigir_token_estricto)])
def probar_alertas(session: Session = Depends(get_session)):
    """
    Manda un mail de prueba a los destinos configurados.

    **Existe porque configurar un destino no prueba nada.** El motor tiene nueve
    puntos de llamada a `enviar_alerta` y un envío fallido sólo deja un
    `logger.error` que nadie mira: una casilla dada de baja se comporta igual que
    una que anda. Sin esto, la única forma de saber si el canal funciona es
    esperar a que algo se rompa y ver si llega el aviso — o sea, enterarse de
    que las alertas no andan justo cuando hacían falta.

    `ignorar_cooldown` porque una prueba que el cooldown silencia no es una
    prueba: quien la aprieta está mirando y espera una respuesta ahora.

    **Un 200 dice que el servidor de correo aceptó el mensaje, no que alguien lo
    haya recibido.** Que llegue a la bandeja es lo único que no se puede
    comprobar desde acá, y por eso la respuesta lo dice con todas las letras.
    """
    fila = configuracion_de_alertas(session)
    if not fila.destinos:
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "detalle": "No hay ningún destino configurado al que mandar la prueba.",
            },
        )

    salio = enviar_alerta(
        asunto="Sin Ruido: prueba de alertas",
        cuerpo=(
            "Este mensaje lo pediste vos desde la cabina.\n\n"
            "Si lo estás leyendo, el motor puede avisarte cuando un feed deje de "
            "responder, cuando una síntesis venza sin publicarse o cuando la "
            "entrega al back-end se agote.\n\n"
            "Si no lo recibís, el aviso igual queda en el log del motor."
        ),
        clave="alertas:prueba",
        ignorar_cooldown=True,
    )
    if not salio:
        # El motivo concreto quedó en el log del motor, con el nombre del
        # servidor y el error del proveedor. **No viaja acá**: es la misma regla
        # que en el alta de modelos -- un mensaje de error del proveedor puede
        # nombrar la cuenta o la variable de entorno.
        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "detalle": (
                    "El motor no pudo mandar el mail. El motivo quedó en su log; "
                    "suele ser el SMTP mal configurado o la cuenta rechazada."
                ),
            },
        )

    fila = marcar_prueba_de_alerta_ok(session)
    return {"status": "ok", "alertas": _vista_alertas(fila)}


# ============================================================
# Medios: el roster lo maneja el operador (backlog punto 3)
# ============================================================
#
# Hasta la 1.1.0 los siete medios venían hardcodeados en `scripts/seed_medios.py`,
# o sea que **el repo aceptaba sus términos de uso en nombre de quien lo
# desplegara**. Esa decisión es del operador, y estos tres endpoints se la
# devuelven. Ver specs/roadmap.md, punto 3, y `services/medios.py` por qué el
# alta sondea en vez de registrar a ciegas.
#
# **La baja es `activo=False` y no un DELETE**, a propósito y por dos motivos que
# apuntan al mismo lado. El de producto: deshabilitar tiene que ser reversible
# sin perder nada, para poder apagar un medio hoy y volver a prenderlo el mes que
# viene. El de datos: `Noticia.medio_id` es `NOT NULL` con clave foránea a
# `medio.id` y sin cascada, así que borrar un medio que ya ingirió algo violaría
# la restricción — y si se forzara con cascada se llevaría puestas noticias que
# quizá ya formaron clusters, se sintetizaron y se entregaron al back-end.


class AltaMedio(BaseModel):
    """Lo que hace falta para dar de alta un medio. Ver `models/medio.py`."""

    # Mismo criterio que `AltaModelo`: **los campos de más se rechazan, no se
    # ignoran**. Descartar en silencio un campo que alguien creyó que el motor
    # iba a leer es la peor respuesta posible.
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=120)
    url_base: str = Field(min_length=1, max_length=MAX_LARGO_URL)
    # La cota va en el ELEMENTO y no solo en la lista: sin esto un solo feed
    # de 500 KB pasaba y se persistía. Se comprueba antes que el validador de
    # abajo, así que una URL absurda se rechaza sin llegar a deduplicarse.
    feeds_rss: List[Annotated[str, StringConstraints(max_length=MAX_LARGO_URL)]] = (
        Field(min_length=1)
    )

    # `pattern` y no solo `max_length`: ocho caracteres alcanzan para `<script>`,
    # que es exactamente el largo del campo. Son códigos de idioma y de país, no
    # texto libre, así que la forma se puede exigir entera — BCP-47 corto (`es`,
    # `pt-BR`) e ISO 3166-1 alfa-2 (`AR`). Cerrarlos cuesta una línea y saca dos
    # campos de la superficie que `logo_url` obligó a mirar.
    idioma: str = Field(default="es", max_length=8, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z]{2,4})?$")
    pais: Optional[str] = Field(default=None, max_length=2, pattern=r"^[A-Za-z]{2}$")

    # Acotado acá y **validado en `alta_medio`** con `validar_url_de_logo`: el
    # largo es una regla de forma que Pydantic sabe expresar, y el esquema es una
    # regla del dominio que vive con las otras reglas de URL, en
    # `services/medios.py`.
    logo_url: Optional[str] = Field(default=None, max_length=MAX_LARGO_URL)

    @field_validator("logo_url")
    @classmethod
    def _logo_en_blanco_es_sin_logo(cls, valor: Optional[str]) -> Optional[str]:
        """
        `""` y `"   "` se guardan como `None`.

        Un formulario que deja el logo vacío manda la cadena vacía, no `null`.
        Sin esto se guardaba `""`, que no es una URL y tampoco es "no hay logo":
        es un tercer estado que después alguien tiene que interpretar. Y con la
        validación de esquema puesta pasaría a ser un 422 por dejar un campo
        opcional en blanco, que es peor todavía.
        """
        limpio = (valor or "").strip()
        return limpio or None

    @field_validator("feeds_rss")
    @classmethod
    def _feeds_distintos_y_acotados(cls, feeds: List[str]) -> List[str]:
        """
        Deduplica la lista y le pone techo. **En ese orden, y no al revés.**

        Deduplicar primero es lo que hace que la cota signifique lo que dice:
        veinte *feeds distintos*, que es lo que cuesta veinte pedidos. Si se
        cortara primero por largo, una lista pegada con repetidas se rechazaría
        entera cuando en realidad pedía tres feeds, y el operador tendría que
        limpiarla a mano para descubrir que siempre estuvo dentro del límite.

        La comparación es por string exacto después de `.strip()`. Dos URLs que
        difieren en una barra final apuntan al mismo lado y acá cuentan como
        distintas: normalizar de verdad (caja del host, orden de los parámetros)
        es un problema con esquinas y el techo ya acota lo que se escapa. La
        amplificación que el ataque midió —la misma URL repetida— la cierra este
        `dict.fromkeys`.
        """
        distintos = list(dict.fromkeys(f.strip() for f in feeds))
        if len(distintos) > MAX_FEEDS_POR_MEDIO:
            raise ValueError(
                f"Son {len(distintos)} feeds distintos y el máximo es "
                f"{MAX_FEEDS_POR_MEDIO}. Cada feed es un pedido de red en cada "
                f"ciclo de ingesta, y en el alta uno más que hay que esperar. "
                f"Si el medio de verdad necesita más, entrá los principales y "
                f"medí antes si los que sobran aportan notas nuevas: en La "
                f"Nación y TN los feeds de sección resultaron ser archivo."
            )
        return distintos

    # **La manda el operador y arranca apagada.** El sondeo detecta si el feed
    # trae el cuerpo de las notas y lo informa, pero no prende esta bandera solo:
    # marca los medios donde el motor va a buscar a la página el cuerpo que el
    # medio eligió no publicar en su feed, y cruzar esa línea es una decisión de
    # quien acepta los términos, no del motor. Es la diferencia con
    # `modo_estructura` en `POST /modelos`, que sí se autodescubre — ahí lo que
    # se descubre es un detalle técnico del protocolo, no un permiso.
    extraer_por_url: bool = False


class CambioDeMedio(AltaMedio):
    """
    Lo editable de un medio ya cargado: **el mismo juego que el alta**, más la
    confirmación de la guarda de atribución.

    Hereda de `AltaMedio` a propósito y no repite los campos: si mañana el alta
    suma uno, editar lo sigue solo. Lo que **no** entra es `activo` —tiene su
    propio `PATCH`, con su propia semántica. `extraer_por_url` **sí** entra, por
    herencia y a propósito: es una decisión del operador, y si no se pudiera
    cambiar después, un medio dado de alta sin la bandera quedaría sin ingerir
    para siempre sin forma de arreglarlo.
    """

    # Default `False` y no `None`: no confirmar es el estado normal, y el que
    # tiene que escribir algo es quien va a cambiar de dominio.
    confirmar_dominio_nuevo: bool = False


def _vista_medio(medio: Medio) -> dict:
    """
    Lo que se devuelve de un medio.

    A diferencia de `_vista_publica` para modelos, acá **no hay nada que filtrar**:
    `Medio` no guarda credenciales ni URLs que completen una cadena de ataque,
    solo datos que el medio ya publica de sí mismo. Existe igual como único punto
    de serialización, para que un campo sensible que se agregue mañana tenga un
    solo lugar donde decidirse.
    """
    return medio.model_dump()


@app.get("/medios")
def listar_medios(session: Session = Depends(get_session)):
    """Los medios cargados, activos y deshabilitados."""
    filas = session.exec(select(Medio).order_by(Medio.nombre)).all()
    return {
        "status": "ok",
        "activos": sum(1 for f in filas if f.activo),
        "total": len(filas),
        "medios": [_vista_medio(f) for f in filas],
    }


@app.get("/medios/panel")
def panel_medios(session: Session = Depends(get_session)):
    """
    Con quién se junta cada medio, y en cuántos clusters queda solo.

    **Responde qué medio conviene sumar, que es una pregunta de producto.** Un
    cluster de un solo medio no llega a `MIN_MEDIOS_CLUSTER` y no se sintetiza
    nunca, así que es material que se produce y no se publica. El panel dice
    cuánto de eso aporta cada medio y de qué tema es, y con eso se decide qué
    redacción falta en el roster.

    **Va antes de `/medios/{medio_id}` en el archivo** aunque hoy no haya un GET
    con ese path: el día que lo haya, `panel` sería un `medio_id` inválido y el
    orden de declaración es lo único que decide cuál gana.

    Es un informe y no la lista, y por eso es una ruta aparte en vez de campos
    de más en `GET /medios`: recorre todas las noticias agrupadas, así que tiene
    otro costo y otra frecuencia de uso. Medido contra la base real —5.390
    noticias, 688 clusters— el recorrido es una sola consulta.
    """
    return {"status": "ok", **panel_de_medios(session)}


@app.post("/medios")
def alta_medio(datos: AltaMedio, session: Session = Depends(get_session)):
    """
    Da de alta un medio, **después de sondear sus feeds**, y lo deja habilitado.

    No es un CRUD: antes de guardar nada se leen los feeds y se informa qué hay
    del otro lado — cuántos items traen, si traen el cuerpo, qué ventana cubren y
    qué dice el `robots.txt`. Así el alta pasa de *"registrá esto"* a *"esto es
    lo que encontramos, decidí vos"*.

    **Qué frena y qué no.** Un feed que no responde, no parsea o no trae un solo
    item utilizable devuelve 422 y no guarda nada: ahí no hay criterio que
    aplicar, está roto. Lo que sí es criterio del operador —que el medio no
    publique el cuerpo, que su `robots.txt` sea restrictivo, que la ventana
    parezca archivo— viaja en `avisos` y **no impide el alta**.

    **Nace activo**, y es la diferencia deliberada con `POST /modelos`, donde
    `activar` es `False` por default: allá prender un modelo apaga a los demás,
    así que activar es un interruptor y encenderlo solo sería tomar una decisión
    ajena. Acá los medios conviven —cuantos más, mejor funciona el clustering— y
    sumar uno es aditivo. Dar de alta un medio para después tener que acordarse
    de prenderlo sería una ceremonia sin contenido.
    """
    duplicado = JSONResponse(
        status_code=409,
        content={"status": "error", "detalle": f"Ya existe un medio '{datos.nombre}'"},
    )
    if session.exec(select(Medio).where(Medio.nombre == datos.nombre)).first():
        return duplicado

    try:
        # El logo primero, porque no toca la red: un `javascript:` se rechaza sin
        # gastar los pedidos del sondeo. Comparte el `except` porque para quien
        # llama es el mismo error —"lo que mandaste no sirve, y acá está por qué".
        if datos.logo_url:
            datos.logo_url = validar_url_de_logo(datos.logo_url)
        informe, avisos = sondear_medio(datos.url_base, datos.feeds_rss)
    except FeedInservible as error:
        # 422 y no 500: lo que mandaron no sirve, y el mensaje dice cuál de los
        # feeds falló y por qué.
        return JSONResponse(
            status_code=422, content={"status": "error", "detalle": str(error)}
        )

    medio = Medio(**datos.model_dump(), activo=True)
    session.add(medio)
    try:
        session.commit()
    except IntegrityError:
        # El SELECT de arriba deja una ventana entre la comprobación y el INSERT.
        # El índice único de `Medio.nombre` protege el dato; esto protege la
        # respuesta, que si no salía como un 500 por una carrera perfectamente
        # normal. Mismo patrón que `alta_modelo`.
        session.rollback()
        return duplicado
    session.refresh(medio)

    logger.info(
        f"Alta de medio '{medio.nombre}' (id={medio.id}): "
        f"{informe['items_totales']} items, {informe['items_con_cuerpo']} con cuerpo, "
        f"extraer_por_url={medio.extraer_por_url}, {len(avisos)} avisos"
    )
    return {
        "status": "ok",
        "medio": _vista_medio(medio),
        "sondeo": informe,
        "avisos": avisos,
    }


@app.put("/medios/{medio_id}")
def editar_medio(
    medio_id: int = Path(..., ge=1, le=MAX_ID),
    datos: CambioDeMedio = Body(...),
    session: Session = Depends(get_session),
):
    """
    Cambia los datos de un medio ya cargado. Existe porque **los medios mueven
    sus feeds** y hasta hoy eso obligaba a darlo de baja y de alta de nuevo,
    perdiendo su historia.

    **Es `PUT` y no otro `PATCH`, y es una decisión.** El `PATCH` de abajo es un
    interruptor con una propiedad que vale la pena no tocar: apagar un medio no
    consulta la red, así que funciona con el servidor del medio muerto. Meterle
    un cuerpo encima habría mezclado esa garantía con una operación que sí sale
    a la red y que necesita una confirmación. Son dos cosas distintas sobre el
    mismo recurso, y separarlas deja a cada una con su propia forma de fallar.

    **La guarda que este endpoint tiene y el alta no.** Dar de alta un medio
    nuevo no puede mentir sobre quién es: nace vacío. Cambiarle la URL a uno que
    ya tiene historia, sí — si a "La Nación" se le apunta el feed a otra
    redacción, las síntesis dicen que La Nación publicó algo que publicó otro,
    **firmado**, y el back-end lo recibe como legítimo. Por eso un host que el
    medio no tenía **exige `confirmar_dominio_nuevo`**, y la respuesta que lo
    rechaza nombra los hosts para que la confirmación se dé mirando el dato y no
    apretando el botón de siempre.

    **Sondea sólo si cambiaron las URLs.** Corregir un nombre mal escrito no
    tiene por qué fallar porque el servidor del medio esté caído — es el mismo
    criterio que hace que apagar no consulte la red.
    """
    medio = session.get(Medio, medio_id)
    if medio is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "detalle": "No existe ese medio"},
        )

    # El nombre es único en la base. Se comprueba contra los OTROS medios, así
    # que guardar sin cambiarlo no choca consigo mismo.
    choca = session.exec(
        select(Medio).where(Medio.nombre == datos.nombre, Medio.id != medio_id)
    ).first()
    if choca:
        return JSONResponse(
            status_code=409,
            content={"status": "error", "detalle": f"Ya existe un medio '{datos.nombre}'"},
        )

    # Lo que no toca la red, primero: un `javascript:` en el logo o un dominio
    # ajeno se rechazan sin gastar los pedidos del sondeo.
    try:
        if datos.logo_url:
            datos.logo_url = validar_url_de_logo(datos.logo_url)
    except FeedInservible as error:
        return JSONResponse(
            status_code=422, content={"status": "error", "detalle": str(error)}
        )

    conocidas = [medio.url_base, *medio.feeds_rss]
    propuestas = [datos.url_base, *datos.feeds_rss]
    ajenos = hosts_ajenos(propuestas, conocidas)
    if ajenos and not datos.confirmar_dominio_nuevo:
        return JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "detalle": (
                    f"'{medio.nombre}' hoy publica desde "
                    f"{', '.join(sorted({host_normalizado(u) for u in conocidas}))}. "
                    f"Lo que mandaste apunta además a {', '.join(ajenos)}. "
                    "Si es el mismo medio que cambió de dominio, confirmalo; si no, "
                    "es otro medio y va como alta nueva."
                ),
                # La app ramifica con esto y no parseando el texto de arriba.
                "requiere_confirmacion": True,
                "hosts_nuevos": ajenos,
            },
        )

    urls_cambiaron = (
        datos.url_base != medio.url_base or list(datos.feeds_rss) != list(medio.feeds_rss)
    )
    informe = None
    avisos: List[str] = []
    if urls_cambiaron:
        try:
            informe, avisos = sondear_medio(datos.url_base, datos.feeds_rss)
        except FeedInservible as error:
            return JSONResponse(
                status_code=422, content={"status": "error", "detalle": str(error)}
            )

    cambios = datos.model_dump(exclude={"confirmar_dominio_nuevo"})
    for campo, valor in cambios.items():
        setattr(medio, campo, valor)
    session.add(medio)
    try:
        session.commit()
    except IntegrityError:
        # Misma ventana que en el alta entre el SELECT y el UPDATE, y el mismo
        # motivo para atajarla: el índice único protege el dato, esto protege la
        # respuesta de salir como un 500 por una carrera normal.
        session.rollback()
        return JSONResponse(
            status_code=409,
            content={"status": "error", "detalle": f"Ya existe un medio '{datos.nombre}'"},
        )
    session.refresh(medio)

    logger.info(
        f"Medio '{medio.nombre}' (id={medio.id}) editado: "
        f"urls_cambiaron={urls_cambiaron}, hosts_nuevos={ajenos}"
    )
    return {
        "status": "ok",
        "medio": _vista_medio(medio),
        "sondeo": informe,
        "avisos": avisos,
    }


@app.patch("/medios/{medio_id}")
def habilitar_medio(
    medio_id: int = Path(..., ge=1, le=MAX_ID),
    activo: bool = Query(...),
    session: Session = Depends(get_session),
):
    """
    Habilita o deshabilita un medio. **Deshabilitar no es borrar.**

    Un medio deshabilitado conserva todo —sus noticias, sus clusters, sus
    síntesis ya entregadas— y lo único que cambia es que
    `ingerir_todos_los_medios` deja de traer sus feeds (`services/ingestion.py`,
    que ya filtraba por `Medio.activo` desde la Fase 2). Se puede volver a
    habilitar cuando sea.

    **No hay exclusividad**, a diferencia de `PATCH /modelos/{id}`: ahí prender
    uno apaga a los demás porque la credencial es una sola y dos proveedores
    prendidos son un estado que no se puede usar. Acá los medios conviven, y de
    hecho el clustering necesita varios para encontrar el mismo hecho contado por
    distintas redacciones.

    **Habilitar sondea; deshabilitar no.** Al prender se re-verifican los feeds
    con la misma regla que el alta, porque entre aquel día y hoy el medio pudo
    cambiar de URL o dar de baja el feed — y el error conviene verlo al apretar
    el botón, no quince minutos más tarde en un mail de alerta.

    Apagar, en cambio, **no consulta la red y no puede fallar por ella**. Es la
    válvula de escape: si un medio está devolviendo basura o golpeando de más,
    hay que poder apagarlo con la conexión caída y con su servidor muerto. Un
    apagado que dependa de que el feed responda es un apagado que falla justo
    cuando se lo necesita.
    """
    medio = session.get(Medio, medio_id)
    if medio is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "detalle": "No existe ese medio"},
        )

    informe = None
    avisos: List[str] = []
    if activo:
        try:
            informe, avisos = sondear_medio(medio.url_base, medio.feeds_rss)
        except FeedInservible as error:
            return JSONResponse(
                status_code=422, content={"status": "error", "detalle": str(error)}
            )

    medio.activo = activo
    session.add(medio)
    session.commit()
    session.refresh(medio)

    logger.info(
        f"Medio '{medio.nombre}' (id={medio.id}) "
        f"{'habilitado' if activo else 'deshabilitado'}"
    )
    respuesta = {
        "status": "ok",
        "medio": _vista_medio(medio),
        "avisos": avisos,
    }
    if informe is not None:
        respuesta["sondeo"] = informe
    return respuesta
