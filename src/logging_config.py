"""
Configuración de logging del motor. **Existe porque sin ella el motor era mudo.**

Hasta la 1.0 no había `basicConfig` ni handler en ningún lado de `src/`. Eso no
significa "los logs salen con el formato por defecto": significa que la raíz no
tiene handlers y entonces

- todo `logger.info(...)` del motor **se descarta sin dejar rastro**, y
- todo `logger.warning(...)` para arriba cae en `logging.lastResort`, un handler
  de emergencia de la biblioteca estándar que escribe a stderr **sin fecha, sin
  nivel y sin nombre de logger**.

Medido sobre el proceso real antes de este módulo::

    handlers en la raíz: NINGUNO
    nivel efectivo de src.main: WARNING
    -> un logger.info del motor SE PIERDE

Lo que se perdía no era ruido. Era el resultado de cada paso del pipeline, con
qué modelo se sintetizó y cuántos tokens costó, el aviso de exclusividad al
activar un modelo, y **el porcentaje del ciclo que usó cada corrida** — que es,
según `specs/roadmap.md`, el número con el que se calibra
`INGEST_INTERVAL_MINUTES`. El motor medía su propia utilización y tiraba la
medición a la basura.

Uvicorn no tapa este agujero: su configuración toca solo los loggers `uvicorn*`
y deja la raíz intacta a propósito, para no pisarle la configuración a la
aplicación que hospeda. La aplicación es esta, y no la tenía.

**A stdout y no a un archivo.** El motor corre en un contenedor, y ahí el
archivo es la peor de las dos opciones: lo esconde de `docker logs`, se lo lleva
puesto cada recreación del contenedor, y sin rotación llena el disco del VPS. El
driver `json-file` de Docker ya persiste stdout, y la rotación se declara donde
corresponde — en el `docker-compose.yml`, que la trae configurada.
"""
import logging
import sys
import time
from datetime import datetime

from .config import settings
from .tiempo import ZONA_LOCAL

logger = logging.getLogger(__name__)

# El nombre del logger va incluido a propósito: con 16 módulos emitiendo, saber
# si una línea la escribió `services.synthesis` o `services.webhook_delivery`
# es la mitad del diagnóstico. `-8s` en el nivel alinea la columna del mensaje.
FORMATO = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# **El `-03` va escrito en el formato, no calculado.** `%z` sobre el
# `struct_time` que consume `strftime` daría el offset de la máquina, que es
# justamente el que no queremos. Como `ZONA_LOCAL` es fijo —Argentina no tiene
# horario de verano desde 2009, ver `tiempo.py`— la constante es correcta y no
# arrastra una base de zonas horarias al contenedor.
FECHA = "%Y-%m-%d %H:%M:%S-03"

NIVEL_POR_DEFECTO = logging.INFO

# Librerías que hablan de más en INFO. Sin este techo, subir la raíz a INFO
# —que es todo el punto de este módulo— entierra al motor bajo el ruido de sus
# dependencias, y el resultado neto es peor que no tener logs: hay líneas, pero
# no se encuentra la que importa.
#
# `httpx` es el caso claro: emite una línea por request, y un ciclo con
# extracción por URL hace decenas de requests de artículo. El resto son
# arranques ruidosos (`sentence_transformers` y `transformers` anuncian la carga
# del modelo) o detalle de transporte que solo sirve para depurar la librería.
#
# **`apscheduler` queda deliberadamente afuera**: sus dos líneas por ciclo
# ("Running job" / "executed successfully") son la prueba de vida del scheduler,
# que es justo lo que un operador quiere ver.
NIVELES_DE_TERCEROS = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "urllib3": logging.WARNING,
    "google_genai": logging.WARNING,
    "google.genai": logging.WARNING,
    "sentence_transformers": logging.WARNING,
    "transformers": logging.WARNING,
    "huggingface_hub": logging.WARNING,
    "filelock": logging.WARNING,
    "trafilatura": logging.WARNING,
    "asyncio": logging.WARNING,
}

# El SQL va por acá y NO por `echo=True` de SQLAlchemy. Ver `database.get_engine`
# para el porqué; en una línea, `echo=True` se cuelga su propio handler y cada
# sentencia saldría dos veces con dos formatos distintos.
LOGGER_SQL = "sqlalchemy.engine"

LOGGERS_DE_UVICORN = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _nivel_pedido() -> tuple[int, bool]:
    """
    Traduce `LOG_LEVEL` a un nivel de `logging`, sin poder tumbar el arranque.

    `basicConfig(level="INFOO")` levanta `ValueError`, y como esto corre en el
    `lifespan` un typo en el `.env` dejaría el motor sin arrancar. Cambiar el
    detalle de los logs no es una operación que deba poder hacer eso: se cae a
    INFO y se avisa.

    Devuelve el nivel y **si el valor se entendió**, como bandera aparte. La
    forma tentadora —devolver el valor que falló y usar `None` para "salió
    bien"— no sirve acá: `None` es también uno de los valores que hay que
    rechazar, y el aviso se lo comía justo en ese caso. Lo encontró un test.
    """
    nombre = (settings.LOG_LEVEL or "").strip().upper()
    nivel = getattr(logging, nombre, None)
    if isinstance(nivel, int):
        return nivel, True
    return NIVEL_POR_DEFECTO, False


def _hora_local(segundos: float) -> time.struct_time:
    """
    La hora de cada línea, en UTC-3 y sin depender del reloj de la máquina.

    `logging` usa `time.localtime` por defecto, que en el contenedor es **UTC**:
    `python:3.12-slim` no trae `tzdata`, así que poner `TZ` en el compose no
    alcanzaría — fallaría de vuelta a UTC en silencio, que es peor que no
    intentarlo.

    El resultado sería un log con el prefijo en UTC y los mensajes en UTC-3,
    porque `tiempo.formatear` —usada para logs y reportes— devuelve hora
    argentina. Mezclar zonas es exactamente lo que `tiempo.py` existe para
    evitar, y ya costó un bug real en la firma del webhook. La regla del
    proyecto es "se guarda en UTC, se muestra en UTC-3", y un log es para
    mostrar.
    """
    return datetime.fromtimestamp(segundos, ZONA_LOCAL).timetuple()


def _formateador() -> logging.Formatter:
    """
    El formatter del handler propio, con su `converter` puesto.

    Se arma acá y no se toca `logging.Formatter.converter` a nivel de clase
    —que es la receta habitual— porque eso es global al proceso: le cambiaría la
    hora también a los formatters de una configuración ajena, que no es nuestra
    para tocar.
    """
    formato = logging.Formatter(FORMATO, FECHA)
    formato.converter = _hora_local
    return formato


def _forzar_utf8(flujo) -> None:
    r"""
    Fuerza UTF-8 en la salida. **No es cosmético: sin esto se pierden líneas.**

    Medido en Windows, donde `sys.stdout` sale en `cp1252`: una línea con un
    carácter que ese codec no tiene —un titular con `北京`, un apellido en
    cirílico, un `≈` en una nota de economía— hace que `StreamHandler.emit`
    levante `UnicodeEncodeError`. Y `logging` no propaga esa excepción: escribe
    un `--- Logging error ---` con traceback y **descarta el mensaje**. O sea
    que la línea rara, la que más ganas hay de leer, es justo la que no está.

    Un motor que ingiere noticias internacionales no puede tener esa lotería.

    `errors="backslashreplace"` va igual, como red: con UTF-8 no debería hacer
    falta nunca, pero si hiciera falta, degradar a `北` es infinitamente
    mejor que perder la línea.
    """
    try:
        flujo.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError, OSError):
        # Flujos que no son un `TextIOWrapper` reconfigurable: un capturador de
        # tests, un pipe ya envuelto. No se insiste — es una mejora sobre el
        # default de la plataforma, no un requisito para arrancar.
        pass


def _unificar_uvicorn() -> None:
    """
    Manda las líneas de uvicorn por el mismo handler que las del motor.

    Uvicorn le pone a sus tres loggers un handler propio y `propagate: False`,
    con un formato que **no lleva fecha**. Sin esto, el log del contenedor
    mezcla dos formatos y las líneas de acceso —las que dicen qué endpoint se
    llamó— son las únicas sin hora, que es justo el dato que se necesita para
    cruzarlas con lo que hizo el pipeline.

    Vaciar `handlers` y devolver la propagación alcanza: el mensaje de acceso se
    arma con `%s - "%s %s HTTP/%s" %d`, así que se renderiza igual con cualquier
    formatter. Lo que aportaba el de uvicorn era el color.

    OJO con el orden: `uvicorn.access` decide si loguear consultando
    `hasHandlers()`, que sube por la jerarquía. Sacarle el handler propio sin que
    la raíz tenga uno apagaría el log de accesos en silencio. Por eso esto se
    llama solo cuando `basicConfig` efectivamente instaló el de la raíz.
    """
    for nombre in LOGGERS_DE_UVICORN:
        uvi = logging.getLogger(nombre)
        uvi.handlers.clear()
        uvi.propagate = True


def configurar_logging() -> None:
    """
    Deja el logging listo. Idempotente, y **no le pisa la configuración a nadie**.

    Si la raíz ya tiene handlers, alguien más ya configuró el logging: uvicorn
    con `--log-config`, un gunicorn por delante, o pytest capturando. En ese caso
    `basicConfig` no hace nada —es su comportamiento documentado, no un
    accidente— y acá se lo respeta: se aplican los techos de terceros, que son
    higiene, pero no se toca el handler ajeno ni los loggers de uvicorn.
    """
    ya_estaba_configurado = bool(logging.getLogger().handlers)
    nivel, se_entendio = _nivel_pedido()

    if not ya_estaba_configurado:
        # Solo si el handler lo ponemos nosotros: si la salida es de otro, el
        # encoding también es decisión de otro.
        _forzar_utf8(sys.stdout)

    # El handler se arma a mano en vez de dejárselo a `basicConfig(format=...)`
    # porque el formatter necesita su `converter` propio (ver `_hora_local`).
    # Si la raíz ya tenía handlers, `basicConfig` sigue siendo un no-op y este
    # se descarta sin haberse enganchado a nada.
    #
    # stdout y no el stderr por defecto: son logs de operación, no errores del
    # proceso.
    manejador = logging.StreamHandler(sys.stdout)
    manejador.setFormatter(_formateador())
    logging.basicConfig(level=nivel, handlers=[manejador])

    for nombre, techo in NIVELES_DE_TERCEROS.items():
        logging.getLogger(nombre).setLevel(techo)

    # `LOG_SQL` es independiente de `LOG_LEVEL` a propósito. El SQL de un ciclo
    # son miles de líneas: si viniera incluido en `DEBUG`, nadie podría poner el
    # motor en DEBUG sin ahogarse.
    logging.getLogger(LOGGER_SQL).setLevel(
        logging.INFO if settings.LOG_SQL else logging.WARNING
    )

    if not ya_estaba_configurado:
        _unificar_uvicorn()

    if not se_entendio:
        logger.warning(
            "LOG_LEVEL=%r no es un nivel conocido; se usa INFO. Valores "
            "válidos: DEBUG, INFO, WARNING, ERROR, CRITICAL.",
            settings.LOG_LEVEL,
        )
