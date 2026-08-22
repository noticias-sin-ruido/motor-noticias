"""
La configuración de logging del motor.

Lo que se prueba acá es incómodo de probar y por eso vale la pena: el logging es
estado global de proceso, así que cada test tiene que dejarlo como lo encontró o
contamina a todos los demás — incluido el `caplog` de pytest, que vive en el
mismo logger raíz.

El test que da sentido a todo el archivo es
`test_un_info_del_motor_llega_a_la_salida`. Esa es exactamente la regresión que
tuvo el motor hasta la 1.0: sin handler en la raíz, cada `logger.info` se
descartaba en silencio y nadie se enteraba, porque **un log que falta no se
parece a un error**.
"""
import inspect
import io
import logging
import sys
import time
from datetime import datetime, timezone

import pytest

from src import database
from src.config import settings
from src.logging_config import (
    LOGGERS_DE_UVICORN,
    LOGGER_SQL,
    NIVELES_DE_TERCEROS,
    _formateador,
    configurar_logging,
)
from src.tiempo import ahora_local

# Los loggers que `configurar_logging` toca, y que por lo tanto hay que
# restaurar. Se arma desde el propio módulo para que un logger nuevo en la lista
# de terceros quede protegido sin tener que acordarse de tocar este archivo.
TOCADOS = (
    tuple(NIVELES_DE_TERCEROS) + LOGGERS_DE_UVICORN + (LOGGER_SQL, "src.logging_config")
)


@pytest.fixture
def raiz_sin_handlers():
    """
    Devuelve una función que deja la raíz como la tiene el motor real —sin un
    solo handler— y restaura todo el estado global al terminar el test.

    **Se la llama desde adentro del test y no acá en el armado**, y el motivo no
    es obvio: pytest vuelve a colgarle su handler de captura a la raíz al empezar
    *cada fase* del test, así que lo que limpie el armado de la fixture ya está
    de vuelta cuando corre el cuerpo. Limpiar en el armado hacía que
    `basicConfig` se creyera pisado por una configuración ajena y no hiciera
    nada — con lo cual los tests probaban el camino equivocado y fallaban todos.

    Limpiar la raíz apaga de paso la captura de `caplog`, así que los tests de
    acá afirman sobre `capsys`, que es donde el handler nuevo termina escribiendo.
    """
    raiz = logging.getLogger()
    previo_raiz = (raiz.handlers[:], raiz.level)
    previos = {
        nombre: (
            logging.getLogger(nombre).handlers[:],
            logging.getLogger(nombre).level,
            logging.getLogger(nombre).propagate,
        )
        for nombre in TOCADOS
    }

    def _limpiar() -> logging.Logger:
        raiz.handlers = []
        raiz.setLevel(logging.WARNING)
        return raiz

    try:
        yield _limpiar
    finally:
        raiz.handlers, nivel = previo_raiz
        raiz.setLevel(nivel)
        for nombre, (handlers, nivel, propaga) in previos.items():
            lg = logging.getLogger(nombre)
            lg.handlers = handlers
            lg.setLevel(nivel)
            lg.propagate = propaga


@pytest.fixture
def nivel(monkeypatch):
    """Fija `LOG_LEVEL` sin depender del `.env` de la máquina que corre el test."""

    def _fijar(valor):
        monkeypatch.setattr(settings, "LOG_LEVEL", valor)

    _fijar("INFO")
    return _fijar


class TestLaSalida:
    def test_un_info_del_motor_llega_a_la_salida(
        self, raiz_sin_handlers, nivel, capsys
    ):
        """
        **La regresión que este módulo existe para cerrar.**

        Antes de tenerlo, la raíz no tenía handlers y esta línea no aparecía en
        ningún lado. Con ella se perdía el resultado de cada paso del pipeline y
        el porcentaje del ciclo que usó la corrida — el número con el que se
        calibra `INGEST_INTERVAL_MINUTES`.
        """
        raiz_sin_handlers()
        configurar_logging()

        logging.getLogger("src.services.synthesis").info("sintetizados 21 clusters")

        assert "sintetizados 21 clusters" in capsys.readouterr().out

    def test_la_linea_trae_fecha_nivel_y_modulo(self, raiz_sin_handlers, nivel, capsys):
        """
        Los tres datos que convierten una línea suelta en algo diagnosticable:
        cuándo pasó, qué tan grave es y quién la escribió. `logging.lastResort`
        —lo que había antes— no da ninguno de los tres.
        """
        raiz_sin_handlers()
        configurar_logging()

        logging.getLogger("src.services.ingestion").warning("el feed no contestó")

        salida = capsys.readouterr().out
        assert "WARNING" in salida
        assert "src.services.ingestion" in salida
        assert "el feed no contestó" in salida
        # `%Y-%m-%d %H:%M:%S-03` -- se comprueba la forma, no el valor.
        assert salida[:4].isdigit() and salida[4] == "-"

    def test_la_hora_de_la_linea_es_utc_menos_3(self, monkeypatch):
        """
        `logging` usa `time.localtime`, que en el contenedor es **UTC**
        (`python:3.12-slim` no trae `tzdata`, así que poner `TZ` en el compose
        fallaría de vuelta a UTC en silencio). El prefijo quedaría en UTC
        mientras los mensajes que arma `tiempo.formatear` van en UTC-3. Mezclar
        zonas es lo que `tiempo.py` existe para evitar, y ya costó un bug real
        en la firma del webhook.

        **Se falsea el reloj del sistema, y sin eso el test no probaba nada.**
        La máquina de desarrollo ya está en UTC-3, así que `time.localtime`
        devuelve exactamente lo mismo que la hora argentina: dos mutaciones
        —volver a `time.localtime`, y no engancharle el `converter` al
        formatter— pasaban el test sin despeinarse. Con el reloj apuntando a UTC
        las dos se caen, y el test dice lo que dice su nombre corra donde corra.

        Se parchean los dos caminos porque son distintos: `logging.Formatter`
        guarda una **referencia** a `time.localtime` en un atributo de clase, así
        que pisar `time.localtime` no la alcanza.
        """
        monkeypatch.setattr(time, "localtime", time.gmtime)
        monkeypatch.setattr(logging.Formatter, "converter", time.gmtime)

        registro = logging.LogRecord(
            "src.main", logging.INFO, "", 0, "una linea cualquiera", None, None
        )
        registro.created = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc).timestamp()

        linea = _formateador().format(registro)

        assert linea.startswith("2026-08-21 09:00:00-03 "), linea

    def test_el_handler_usa_ese_formateador(self, raiz_sin_handlers, nivel, capsys):
        """Que la hora sea correcta no sirve si el handler no usa ese formatter."""
        raiz_sin_handlers()
        configurar_logging()

        logging.getLogger("src.main").warning("una linea cualquiera")

        salida = capsys.readouterr().out
        assert salida.startswith(ahora_local().strftime("%Y-%m-%d %H"))
        assert "-03 " in salida


class TestElEncoding:
    """
    Medido en Windows, donde `sys.stdout` sale en cp1252: sin forzar UTF-8, una
    línea con un carácter que ese codec no tiene se pierde **entera** y deja un
    `--- Logging error ---` con traceback en su lugar. Para un motor que ingiere
    noticias internacionales eso es una lotería con la línea más interesante.
    """

    @staticmethod
    def _stdout_cp1252(monkeypatch):
        crudo = io.BytesIO()
        monkeypatch.setattr(
            sys, "stdout", io.TextIOWrapper(crudo, encoding="cp1252")
        )
        return crudo

    def test_una_linea_fuera_de_cp1252_no_se_pierde(
        self, raiz_sin_handlers, nivel, monkeypatch
    ):
        crudo = self._stdout_cp1252(monkeypatch)
        raiz_sin_handlers()
        configurar_logging()

        logging.getLogger("src.services.ingestion").info("titular con 北京 y ≈")
        sys.stdout.flush()

        assert "北京" in crudo.getvalue().decode("utf-8")

    def test_la_salida_queda_en_utf8(self, raiz_sin_handlers, nivel, monkeypatch):
        self._stdout_cp1252(monkeypatch)
        raiz_sin_handlers()
        configurar_logging()

        assert sys.stdout.encoding == "utf-8"

    def test_una_salida_que_no_se_puede_reconfigurar_no_rompe(
        self, raiz_sin_handlers, nivel, monkeypatch
    ):
        """
        Forzar el encoding es una mejora sobre el default de la plataforma, no
        un requisito para arrancar: si el flujo no lo admite, se sigue.
        """

        class SinReconfigure(io.StringIO):
            def reconfigure(self, **kwargs):
                raise OSError("este flujo no se reconfigura")

        falso = SinReconfigure()
        monkeypatch.setattr(sys, "stdout", falso)
        raiz_sin_handlers()
        configurar_logging()

        logging.getLogger("src.main").info("igual sale")
        assert "igual sale" in falso.getvalue()

    def test_no_toca_la_salida_si_la_configuracion_es_ajena(
        self, raiz_sin_handlers, nivel, monkeypatch
    ):
        """Si el handler es de otro, el encoding también es decisión de otro."""
        self._stdout_cp1252(monkeypatch)
        raiz_sin_handlers().addHandler(logging.NullHandler())

        configurar_logging()

        assert sys.stdout.encoding == "cp1252"


class TestElNivel:
    def test_warning_apaga_los_info(self, raiz_sin_handlers, nivel, capsys):
        raiz_sin_handlers()
        nivel("WARNING")
        configurar_logging()

        motor = logging.getLogger("src.services.clustering")
        motor.info("esto no debería salir")
        motor.warning("esto sí")

        salida = capsys.readouterr().out
        assert "esto no debería salir" not in salida
        assert "esto sí" in salida

    @pytest.mark.parametrize("valor", ["debug", "Info", "WARNING"])
    def test_no_distingue_mayusculas(self, raiz_sin_handlers, nivel, valor):
        raiz_sin_handlers()
        nivel(valor)
        configurar_logging()

        assert logging.getLogger().level == getattr(logging, valor.upper())

    @pytest.mark.parametrize("valor", ["INFOO", "", "verbose", "12", None])
    def test_un_valor_invalido_no_tumba_el_arranque(
        self, raiz_sin_handlers, nivel, capsys, valor
    ):
        """
        `basicConfig(level="INFOO")` levanta `ValueError`, y esto corre dentro
        del `lifespan`: un typo en el `.env` dejaría el motor sin levantar.
        Cambiar el detalle de los logs no puede tener ese poder.
        """
        raiz_sin_handlers()
        nivel(valor)
        configurar_logging()

        assert logging.getLogger().level == logging.INFO
        # Y el aviso sale, que es la otra mitad: caer a INFO en silencio dejaría
        # a alguien creyendo que su LOG_LEVEL está puesto.
        assert "LOG_LEVEL" in capsys.readouterr().out


class TestElRuidoDeTerceros:
    @pytest.mark.parametrize("libreria", sorted(NIVELES_DE_TERCEROS))
    def test_las_librerias_ruidosas_quedan_en_warning(
        self, raiz_sin_handlers, nivel, libreria
    ):
        """
        Sin este techo, subir la raíz a INFO entierra al motor bajo el ruido de
        sus dependencias — `httpx` sola emite una línea por request, y un ciclo
        con extracción por URL hace decenas de requests de artículo.
        """
        raiz_sin_handlers()
        configurar_logging()

        assert logging.getLogger(libreria).level == logging.WARNING

    def test_apscheduler_queda_en_info(self, raiz_sin_handlers, nivel):
        """
        **Deliberadamente afuera del techo.** Sus dos líneas por ciclo son la
        prueba de vida del scheduler, que es lo primero que un operador quiere
        confirmar. Si alguien lo agrega a la lista, este test lo frena.
        """
        raiz_sin_handlers()
        configurar_logging()

        assert logging.getLogger("apscheduler").getEffectiveLevel() == logging.INFO

    def test_el_sql_esta_apagado_por_defecto(
        self, raiz_sin_handlers, nivel, monkeypatch
    ):
        raiz_sin_handlers()
        monkeypatch.setattr(settings, "LOG_SQL", False)
        configurar_logging()

        assert logging.getLogger(LOGGER_SQL).level == logging.WARNING

    def test_log_sql_lo_enciende(self, raiz_sin_handlers, nivel, monkeypatch):
        raiz_sin_handlers()
        monkeypatch.setattr(settings, "LOG_SQL", True)
        configurar_logging()

        assert logging.getLogger(LOGGER_SQL).level == logging.INFO

    def test_el_sql_no_viene_incluido_en_debug(
        self, raiz_sin_handlers, nivel, monkeypatch
    ):
        """
        `LOG_SQL` es independiente de `LOG_LEVEL` a propósito: el SQL de un ciclo
        son miles de líneas, así que si viniera dentro de DEBUG nadie podría
        poner el motor en DEBUG sin ahogarse.
        """
        raiz_sin_handlers()
        nivel("DEBUG")
        monkeypatch.setattr(settings, "LOG_SQL", False)
        configurar_logging()

        assert logging.getLogger(LOGGER_SQL).level == logging.WARNING


class TestConvivencia:
    def test_llamarlo_dos_veces_no_duplica_la_salida(
        self, raiz_sin_handlers, nivel, capsys
    ):
        raiz_sin_handlers()
        configurar_logging()
        configurar_logging()

        logging.getLogger("src.main").info("una sola vez")

        assert capsys.readouterr().out.count("una sola vez") == 1

    def test_no_pisa_la_configuracion_de_otro(self, raiz_sin_handlers, nivel):
        """
        Si la raíz ya tiene handlers, alguien más configuró el logging: uvicorn
        con `--log-config`, un gunicorn por delante, pytest capturando. Ese
        handler manda, y el nuestro no se agrega.
        """
        raiz = raiz_sin_handlers()
        ajeno = logging.NullHandler()
        raiz.addHandler(ajeno)

        configurar_logging()

        assert raiz.handlers == [ajeno]

    def test_con_configuracion_ajena_no_toca_uvicorn(self, raiz_sin_handlers, nivel):
        """
        Y **esto no es cosmético**: `uvicorn.access` decide si loguear
        consultando `hasHandlers()`. Vaciarle los handlers cuando la raíz tiene
        una configuración ajena que quizá no propaga apagaría el log de accesos
        en silencio.
        """
        raiz_sin_handlers().addHandler(logging.NullHandler())
        acceso = logging.getLogger("uvicorn.access")
        propio = logging.NullHandler()
        acceso.handlers = [propio]
        acceso.propagate = False

        configurar_logging()

        assert acceso.handlers == [propio]
        assert acceso.propagate is False

    @pytest.mark.parametrize("nombre", LOGGERS_DE_UVICORN)
    def test_uvicorn_pasa_a_salir_por_el_handler_del_motor(
        self, raiz_sin_handlers, nivel, nombre
    ):
        """
        Uvicorn le pone a sus loggers un handler propio con un formato **sin
        fecha**, y `propagate: False`. Sin unificarlos, las líneas de acceso —las
        que dicen qué endpoint se llamó— son las únicas del log sin hora, que es
        justo el dato que hace falta para cruzarlas con lo que hizo el pipeline.
        """
        raiz_sin_handlers()
        uvi = logging.getLogger(nombre)
        uvi.handlers = [logging.NullHandler()]
        uvi.propagate = False

        configurar_logging()

        assert uvi.handlers == []
        assert uvi.propagate is True

    def test_una_linea_de_acceso_de_uvicorn_sale_con_fecha(
        self, raiz_sin_handlers, nivel, capsys
    ):
        """
        El formato del mensaje de acceso lo arma uvicorn con `%s - "%s %s
        HTTP/%s" %d` y su formatter propio solo agrega color, así que se
        renderiza igual con el nuestro. Se comprueba de verdad y no de palabra.
        """
        raiz_sin_handlers()
        configurar_logging()

        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d', "127.0.0.1:5321", "POST", "/ingest", "1.1", 200
        )

        salida = capsys.readouterr().out
        assert '127.0.0.1:5321 - "POST /ingest HTTP/1.1" 200' in salida
        assert salida[:4].isdigit()


def test_el_engine_no_usa_echo_verdadero():
    """
    **Guarda sobre el mecanismo, no sobre el resultado.**

    Con `echo=True` SQLAlchemy le cuelga un StreamHandler propio al logger de la
    Engine y no le apaga la propagación, así que cada sentencia saldría dos
    veces y con dos formatos distintos. Con `echo=False` devuelve un Logger
    pelado y el nivel lo decide la jerarquía — que es lo que maneja `LOG_SQL`.

    No hay test de comportamiento que lo distinga sin una base real conectada, y
    `echo=settings.ENVIRONMENT == "development"` es exactamente el tipo de línea
    que alguien "arregla" de vuelta.
    """
    # **Los comentarios se descartan antes de mirar.** El comentario que explica
    # todo esto en `database.py` dice literalmente `echo=False`, así que buscarlo
    # sobre la fuente cruda daba positivo aunque el código dijera otra cosa: la
    # mutación que devuelve el echo condicional pasaba el test sin despeinarse.
    codigo = "\n".join(
        linea.split("#")[0] for linea in inspect.getsource(database.get_engine).splitlines()
    )

    assert "echo=False" in codigo, (
        "el engine volvió a un echo condicional: el SQL saldría duplicado. "
        "El interruptor es LOG_SQL, en logging_config.py"
    )
