"""
Pruebas de los endpoints de la API FastAPI.
"""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.config import settings
from src.models import Adaptador, Cluster, ModeloIA


class TestRoot:
    """Pruebas del endpoint raíz."""

    def test_root_status(self, client: TestClient):
        """Verifica que el endpoint raíz retorna status ok."""
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "ok"
        assert "environment" in data

    def test_root_reporta_degradado_si_la_base_no_responde(self, client: TestClient):
        """
        El healthcheck del Dockerfile depende de esto para reiniciar el
        contenedor si Postgres se cae -- antes `GET /` solo confirmaba que
        Uvicorn respondía, no que la base estuviera viva.
        """
        with patch("src.main.verificar_conexion", return_value=False):
            response = client.get("/")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degradado"
        assert data["database"] == "error"


class TestIngestEndpoint:
    """Pruebas del endpoint manual POST /ingest."""

    def test_ingest_corre_el_pipeline_y_devuelve_resultados(self, client: TestClient):
        """Verifica que /ingest invoca el pipeline de ingesta y devuelve sus resultados."""
        resultados_simulados = [
            {
                "medio": "Medio Test",
                "nuevas": 2,
                "duplicadas": 0,
                "en_vivo": 0,
                "sin_contenido": 0,
                "error": None,
            }
        ]

        with patch("src.main.ingerir_todos_los_medios", return_value=resultados_simulados) as mock_ingerir:
            response = client.post("/ingest")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["resultados"] == resultados_simulados
        mock_ingerir.assert_called_once()


class TestVectorizeEndpoint:
    """Pruebas del endpoint manual POST /vectorize."""

    def test_vectorize_devuelve_las_stats(self, client: TestClient):
        """Verifica que /vectorize invoca la vectorización y devuelve sus stats."""
        stats_simuladas = {"pendientes": 3, "vectorizadas": 3}

        with patch("src.main.vectorizar_pendientes", return_value=stats_simuladas) as mock:
            response = client.post("/vectorize")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "pendientes": 3, "vectorizadas": 3}
        mock.assert_called_once()

    def test_vectorize_pasa_el_limite(self, client: TestClient):
        """Verifica que el parámetro `limite` llega al servicio."""
        with patch("src.main.vectorizar_pendientes", return_value={"pendientes": 0, "vectorizadas": 0}) as mock:
            response = client.post("/vectorize?limite=5")

        assert response.status_code == 200
        assert mock.call_args.kwargs["limite"] == 5

    @pytest.mark.parametrize("limite", [-1, 0, -999999])
    def test_un_limite_imposible_es_422_y_no_un_dato_inventado(
        self, client: TestClient, limite
    ):
        """
        Antes de la cota, `?limite=-1` devolvía `{"pendientes": -1}` con un 200:
        no vectorizaba nada —el `while restante > 0` no entraba— pero informaba
        un número imposible como si lo hubiera medido. `/search` y `/clusters`
        ya acotaban su `limite`; a este endpoint se le había pasado.
        """
        with patch("src.main.vectorizar_pendientes") as mock:
            response = client.post(f"/vectorize?limite={limite}")

        assert response.status_code == 422
        # Y ni siquiera se llamó al servicio.
        mock.assert_not_called()


class TestSynthesizeEndpoint:
    """Pruebas del endpoint manual POST /synthesize."""

    def test_synthesize_devuelve_las_estadisticas(self, client: TestClient):
        stats = {
            "pendientes": 3, "sintetizados": 2, "creados": 4,
            "actualizados": 1, "descartados": 1, "bloqueados": 1, "fallidos": 0,
        }

        with patch("src.main.sintetizar_pendientes", return_value=stats) as mock:
            response = client.post("/synthesize")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", **stats}
        mock.assert_called_once()


class TestDeliverEndpoint:
    """Pruebas del endpoint manual POST /deliver."""

    def test_deliver_devuelve_las_estadisticas(self, client: TestClient):
        stats = {
            "estado": "ok", "pendientes": 2, "entregadas": 2,
            "rechazadas": 0, "fallidas": 0, "agotadas": 0,
        }

        with patch("src.main.entregar_pendientes", return_value=stats) as mock:
            response = client.post("/deliver")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", **stats}
        assert mock.call_args.kwargs["forzar"] is False

    def test_deliver_pasa_el_forzado(self, client: TestClient):
        """`forzar` reincluye las síntesis que agotaron los intentos."""
        with patch("src.main.entregar_pendientes", return_value={}) as mock:
            response = client.post("/deliver?forzar=true")

        assert response.status_code == 200
        assert mock.call_args.kwargs["forzar"] is True


class TestPurgeEndpoint:
    """Pruebas del endpoint manual POST /purge (backlog punto 8)."""

    def test_purge_devuelve_las_stats(self, client: TestClient):
        stats = {
            "evaluadas": 4083, "bytes_evaluados": 16246730,
            "purgadas": 4083, "bytes_liberados": 16246730,
        }

        with patch("src.main.purgar_cuerpos_vencidos", return_value=stats) as mock:
            response = client.post("/purge")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", **stats}
        assert mock.call_args.kwargs["solo_contar"] is False

    def test_por_default_no_es_solo_contar(self, client: TestClient):
        with patch("src.main.purgar_cuerpos_vencidos", return_value={}) as mock:
            client.post("/purge")

        assert mock.call_args.kwargs["solo_contar"] is False

    def test_purge_pasa_el_solo_contar(self, client: TestClient):
        """`solo_contar=true` es la corrida en seco: mide sin borrar."""
        with patch("src.main.purgar_cuerpos_vencidos", return_value={}) as mock:
            response = client.post("/purge?solo_contar=true")

        assert response.status_code == 200
        assert mock.call_args.kwargs["solo_contar"] is True


class TestPipelineProgramado:
    """
    El job del scheduler aísla los pasos: uno que falla no frena a los que
    siguen, porque todos son idempotentes y la corrida siguiente retoma sola.
    """

    def _mocks(self, **overrides):
        # `PASOS_DEL_PIPELINE` (arriba) es la lista real de pasos: reusarla acá
        # es lo que hace que agregar un paso nuevo al job no pueda dejar este
        # dict desactualizado sin que un test lo note.
        nombres = {paso: {"ok": True} for paso in PASOS_DEL_PIPELINE}
        nombres.update(overrides)
        return nombres

    def test_corre_los_ocho_pasos_en_orden(self):
        """
        Nada más que probara `_job_ingesta_programada` chequeaba que un paso
        estuviera de verdad enganchado al job -- los otros tests de esta clase
        prueban aislamiento entre pasos que YA están, no que la lista completa
        se llame. Un paso agregado y olvidado de conectar habría pasado la
        suite entera igual.

        Además del `call_count`, se registra el ORDEN real de ejecución: el
        comentario de `main.py` y el changelog prometen que la purga corre
        último, a propósito, y un test que solo mira "se llamó" pasaría igual
        si alguien la moviera al principio del job.
        """
        from src import main

        config = self._mocks()
        orden: list = []

        def registrar(nombre, valor):
            def _lado(*args, **kwargs):
                orden.append(nombre)
                return valor
            return _lado

        with ExitStack() as pila:
            mocks = {
                nombre: pila.enter_context(
                    patch.object(main, nombre, side_effect=registrar(nombre, valor))
                )
                for nombre, valor in config.items()
            }
            pila.enter_context(patch.object(main, "enviar_alerta"))
            pila.enter_context(patch.object(main, "get_engine"))
            pila.enter_context(patch.object(main, "Session"))
            main._job_ingesta_programada()

        for nombre, mock in mocks.items():
            assert mock.call_count == 1, f"{nombre} no se llamó una vez"

        assert orden[-1] == "purgar_cuerpos_vencidos", (
            f"la purga tiene que correr última y corrió en la posición "
            f"{orden.index('purgar_cuerpos_vencidos')} de {orden}"
        )

    def test_un_paso_que_falla_no_frena_a_los_siguientes(self):
        from src import main

        config = self._mocks(vectorizar_pendientes=RuntimeError("boom"))

        with ExitStack() as pila:
            for nombre, valor in config.items():
                kwargs = (
                    {"side_effect": valor} if isinstance(valor, Exception)
                    else {"return_value": valor}
                )
                pila.enter_context(patch.object(main, nombre, **kwargs))
            alerta = pila.enter_context(patch.object(main, "enviar_alerta"))
            pila.enter_context(patch.object(main, "get_engine"))
            pila.enter_context(patch.object(main, "Session"))
            main._job_ingesta_programada()

        # Avisó del fallo, pero la síntesis igual corrió.
        assert alerta.call_count == 1
        assert alerta.call_args.kwargs["clave"] == "pipeline:vectorización"

    def test_si_falla_la_fusion_no_se_sintetiza(self):
        """
        Sintetizar sin consolidar publicaría dos veces el mismo hecho, y una
        publicación ya entregada al backend no se retracta.
        """
        from src import main

        with patch.object(main, "ingerir_todos_los_medios", return_value={}), \
             patch.object(main, "vectorizar_pendientes", return_value={}), \
             patch.object(main, "cerrar_clusters_vencidos", return_value={}), \
             patch.object(main, "agrupar_pendientes", return_value={}), \
             patch.object(main, "fusionar_clusters_duplicados",
                          side_effect=RuntimeError("boom")), \
             patch.object(main, "sintetizar_pendientes") as sintesis, \
             patch.object(main, "entregar_pendientes") as entrega, \
             patch.object(main, "purgar_cuerpos_vencidos"), \
             patch.object(main, "enviar_alerta"), \
             patch.object(main, "get_engine"), \
             patch.object(main, "Session"):
            main._job_ingesta_programada()

        sintesis.assert_not_called()

        # La entrega sí corre: es un barrido de todo lo pendiente, y lo que
        # quedó sin entregar de antes no tiene por qué esperar a la fusión.
        entrega.assert_called_once()


class TestClusterEndpoint:
    """Pruebas del endpoint manual POST /cluster."""

    def test_cluster_cierra_agrupa_y_fusiona(self, client: TestClient):
        """Verifica que /cluster corre el cierre, el agrupamiento y la fusión."""
        cierre = {"evaluados": 2, "procesados": 1, "descartados": 1}
        agrupamiento = {"evaluadas": 5, "sumadas_a_cluster": 2, "clusters_creados": 1, "sin_match": 2}
        fusion = {"evaluados": 3, "fusionados": 1}

        with patch("src.main.cerrar_clusters_vencidos", return_value=cierre) as mock_cierre, \
             patch("src.main.agrupar_pendientes", return_value=agrupamiento) as mock_agrupar, \
             patch("src.main.fusionar_clusters_duplicados", return_value=fusion) as mock_fusion:
            response = client.post("/cluster")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "cierre": cierre,
            "agrupamiento": agrupamiento,
            "fusion": fusion,
        }
        mock_cierre.assert_called_once()
        mock_agrupar.assert_called_once()
        mock_fusion.assert_called_once()


class TestSearchEndpoint:
    """
    Pruebas del endpoint GET /search.

    El servicio se mockea porque la búsqueda usa el operador `<=>` de pgvector,
    que no existe en SQLite (los tests corren en memoria). El KNN real se valida
    contra Postgres — ver specs/validacion_manual.md.
    """

    def test_search_devuelve_resultados(self, client: TestClient):
        resultados = [
            {"id": 1, "titulo": "Noticia", "medio": "TN", "similitud": 0.91},
        ]

        with patch("src.main.buscar_noticias_similares", return_value=resultados) as mock:
            response = client.get("/search?q=elecciones")

        assert response.status_code == 200
        data = response.json()
        assert data["consulta"] == "elecciones"
        assert data["cantidad"] == 1
        assert data["resultados"] == resultados
        assert mock.call_args.kwargs["texto"] == "elecciones"

    def test_search_pasa_los_parametros(self, client: TestClient):
        with patch("src.main.buscar_noticias_similares", return_value=[]) as mock:
            response = client.get("/search?q=dolar&limite=5&solo_agrupadas=true")

        assert response.status_code == 200
        assert mock.call_args.kwargs["limite"] == 5
        assert mock.call_args.kwargs["solo_agrupadas"] is True

    def test_search_rechaza_consulta_muy_corta(self, client: TestClient):
        response = client.get("/search?q=ab")
        assert response.status_code == 422

    def test_search_requiere_consulta(self, client: TestClient):
        response = client.get("/search")
        assert response.status_code == 422


class TestClustersEndpoint:
    """Pruebas del endpoint GET /clusters."""

    def test_clusters_lista(self, client: TestClient):
        clusters = [
            {"id": 1, "titulo_evento": "Evento", "estado": "abierto", "medios": ["TN", "La Nación"]},
        ]

        with patch("src.main.listar_clusters", return_value=clusters) as mock:
            response = client.get("/clusters")

        assert response.status_code == 200
        data = response.json()
        assert data["cantidad"] == 1
        assert data["clusters"] == clusters
        mock.assert_called_once()

    def test_clusters_filtra_por_estado(self, client: TestClient):
        with patch("src.main.listar_clusters", return_value=[]) as mock:
            response = client.get("/clusters?estado=procesado&limite=3")

        assert response.status_code == 200
        assert mock.call_args.kwargs["estado"] == "procesado"
        assert mock.call_args.kwargs["limite"] == 3

    def test_clusters_rechaza_limite_invalido(self, client: TestClient):
        assert client.get("/clusters?limite=0").status_code == 422
        assert client.get("/clusters?limite=500").status_code == 422


class TestAPIHealth:
    """Pruebas de salud general de la API."""

    def test_api_responds_to_requests(self, client: TestClient):
        """Verifica que la API responde a requests sin crashear."""
        response = client.get("/")
        assert response.status_code == 200

    def test_api_headers(self, client: TestClient):
        """Verifica que la API devuelve headers correctos."""
        response = client.get("/")

        assert response.headers["content-type"] == "application/json"


PASOS_DEL_PIPELINE = [
    "ingerir_todos_los_medios",
    "vectorizar_pendientes",
    "cerrar_clusters_vencidos",
    "agrupar_pendientes",
    "fusionar_clusters_duplicados",
    "sintetizar_pendientes",
    "entregar_pendientes",
    "purgar_cuerpos_vencidos",
]


class TestAvisoDeCorridaPerdida:
    """
    Las tres formas de perder una corrida terminaban en un WARNING de APScheduler
    sobre un stdout que no se persiste. El listener las vuelve audibles.
    """

    def _evento_de_ejecucion(self, code, exception=None):
        from apscheduler.events import JobExecutionEvent

        return JobExecutionEvent(
            code, "ingesta_rss", "default", datetime(2026, 8, 19, 10, 0, 0),
            exception=exception,
        )

    def _disparar(self, evento):
        """
        Devuelve el mock de `threading.Thread` y el de `enviar_alerta`.

        Se parchea el hilo en vez de dejarlo correr: el test no depende de
        sincronización real, y de paso deja a la vista que el aviso **no** se
        manda en el hilo del listener.
        """
        from src import main

        with patch.object(main, "enviar_alerta") as alerta, \
             patch.object(main.threading, "Thread") as hilo:
            main._avisar_corrida_perdida(evento)
        return hilo, alerta

    def test_avisa_cuando_la_corrida_se_solapa(self):
        from apscheduler.events import EVENT_JOB_MAX_INSTANCES, JobSubmissionEvent
        from src import main

        # `EVENT_JOB_MAX_INSTANCES` llega con otra forma: `scheduled_run_times`
        # en plural y sin `exception`. Si el listener asumiera una sola forma,
        # este es el caso que reventaría — y es justo el del overrun.
        evento = JobSubmissionEvent(
            EVENT_JOB_MAX_INSTANCES, "ingesta_rss", "default",
            [datetime(2026, 8, 19, 10, 0, 0)],
        )
        hilo, _ = self._disparar(evento)

        assert hilo.call_args.kwargs["kwargs"]["clave"] == "scheduler:solapada"
        assert "anterior" in hilo.call_args.kwargs["kwargs"]["cuerpo"]
        assert main.SCHEDULER_MAX_INSTANCES == 1

    def test_avisa_cuando_la_corrida_llega_tarde(self):
        from apscheduler.events import EVENT_JOB_MISSED

        hilo, _ = self._disparar(self._evento_de_ejecucion(EVENT_JOB_MISSED))

        assert hilo.call_args.kwargs["kwargs"]["clave"] == "scheduler:atrasada"

    def test_avisa_cuando_el_job_levanta_excepcion(self):
        """
        `_correr_paso` protege cada paso, pero no la apertura de la sesión que
        los envuelve: con la base caída el fallo se escapa por acá.
        """
        from apscheduler.events import EVENT_JOB_ERROR

        evento = self._evento_de_ejecucion(
            EVENT_JOB_ERROR, exception=RuntimeError("la base no responde")
        )
        hilo, _ = self._disparar(evento)

        cuerpo = hilo.call_args.kwargs["kwargs"]["cuerpo"]
        assert hilo.call_args.kwargs["kwargs"]["clave"] == "scheduler:error"
        assert "RuntimeError" in cuerpo
        assert "la base no responde" in cuerpo

    def test_no_manda_el_mail_en_el_hilo_del_listener(self):
        """
        **El invariante que protege el event loop.**

        Este listener corre dentro del loop —`AsyncIOScheduler.wakeup` está
        decorado con `@run_in_event_loop` y `_dispatch_event` invoca los
        listeners sincrónicamente— y `enviar_alerta` abre una conexión SMTP
        bloqueante. Mandarla acá congelaría la API entera mientras dure el
        intercambio, y un servidor SMTP colgado la dejaría sin responder.
        """
        from apscheduler.events import EVENT_JOB_MISSED

        hilo, alerta = self._disparar(self._evento_de_ejecucion(EVENT_JOB_MISSED))

        alerta.assert_not_called()
        assert hilo.call_args.kwargs["target"] is alerta
        assert hilo.call_args.kwargs["daemon"] is True
        hilo.return_value.start.assert_called_once()


class TestMargenesDelScheduler:
    def test_el_job_se_registra_con_los_tres_margenes(self):
        """
        Guarda contra que alguien los saque sin querer: los tres corren con
        defaults de la librería si no se declaran, y el de `misfire_grace_time`
        es **1 segundo**, que descarta la corrida ante cualquier demora mínima.
        """
        from src import main

        async def arrancar():
            async with main.lifespan(None):
                pass

        with patch.object(main, "init_db"), patch.object(main, "scheduler") as sched:
            asyncio.run(arrancar())

        kwargs = sched.add_job.call_args.kwargs
        assert kwargs["max_instances"] == main.SCHEDULER_MAX_INSTANCES == 1
        assert kwargs["coalesce"] == main.SCHEDULER_COALESCE is True
        assert kwargs["misfire_grace_time"] == main.SCHEDULER_MARGEN_ATRASO_SEGUNDOS
        assert kwargs["misfire_grace_time"] > 1

        # Y que el listener quedó enganchado: sin él los tres eventos son mudos.
        sched.add_listener.assert_called_once()
        assert sched.add_listener.call_args.args[0] is main._avisar_corrida_perdida


class TestCanarioDeDuracion:
    """
    Una corrida que se pasa del intervalo hace que la siguiente se saltee. El
    canario avisa bastante antes de llegar ahí.
    """

    def _correr_con_duracion(self, segundos: float):
        from src import main

        arranque = datetime(2026, 8, 19, 10, 0, 0)
        with ExitStack() as pila:
            for nombre in PASOS_DEL_PIPELINE:
                pila.enter_context(patch.object(main, nombre, return_value={}))
            pila.enter_context(patch.object(main, "get_engine"))
            pila.enter_context(patch.object(main, "Session"))
            pila.enter_context(patch.object(
                main, "ahora_local",
                side_effect=[arranque, arranque + timedelta(seconds=segundos)],
            ))
            alerta = pila.enter_context(patch.object(main, "enviar_alerta"))
            main._job_ingesta_programada()
        return alerta

    def _umbral_en_segundos(self) -> float:
        from src import main
        from src.config import settings

        return settings.INGEST_INTERVAL_MINUTES * 60 * main.SCHEDULER_UMBRAL_CORRIDA_LARGA

    def test_avisa_cuando_la_corrida_pasa_el_umbral(self):
        alerta = self._correr_con_duracion(self._umbral_en_segundos() + 10)

        assert alerta.call_count == 1
        assert alerta.call_args.kwargs["clave"] == "scheduler:corrida-larga"

    def test_una_corrida_normal_no_avisa(self):
        alerta = self._correr_con_duracion(self._umbral_en_segundos() - 10)

        alerta.assert_not_called()

    def test_el_umbral_sigue_al_intervalo(self):
        """
        El umbral es una fracción y no un número de segundos a propósito: si el
        intervalo cambia —y es justo lo que hay que calibrar— el canario se
        ajusta solo en vez de quedar desincronizado en silencio.
        """
        from src import main

        with patch.object(settings, "INGEST_INTERVAL_MINUTES", 30):
            # 500 s pasaría el umbral de un ciclo de 15 min (450 s), pero no el
            # de uno de 30 (900 s).
            alerta = self._correr_con_duracion(500)

        assert main.SCHEDULER_UMBRAL_CORRIDA_LARGA == 0.5
        alerta.assert_not_called()


class TestSynthesizeConModelo:
    """
    `POST /synthesize?modelo_id=` — el modo "todas con el modelo que elijo".
    Sin el parámetro tiene que quedar exactamente como estaba, porque es lo que
    llama el scheduler.

    **Estos tests mockean el servicio, así que cubren el cableado y el código de
    estado, no lo que la respuesta dice.** Es la división correcta —acá se prueba
    que el endpoint pase lo que tiene que pasar— pero conviene saber dónde
    termina: lo que sale en el cuerpo lo prueban
    `TestLasRespuestasDeSintesisNoNombranLaVariable` (sin mocks) y
    `test_synthesis.TestLaCadenaConTraduccionRealDeExcepciones`.
    """

    def test_sin_el_parametro_no_le_pasa_ningun_modelo(self, client: TestClient):
        """
        La garantía para el scheduler: sin `modelo_id` el servicio arma la
        cadena por su cuenta, igual que antes de multimodelo.
        """
        with patch("src.main.sintetizar_pendientes", return_value={}) as mock:
            respuesta = client.post("/synthesize")

        assert respuesta.status_code == 200
        assert mock.call_args.kwargs["modelo"] is None

    def test_con_modelo_id_le_pasa_esa_fila(self, client: TestClient, session):
        fila = ModeloIA(
            nombre="elegido", adaptador=Adaptador.GEMINI, modelo="m", activo=False
        )
        session.add(fila)
        session.commit()
        session.refresh(fila)

        with patch("src.main.sintetizar_pendientes", return_value={}) as mock:
            respuesta = client.post(f"/synthesize?modelo_id={fila.id}")

        assert respuesta.status_code == 200
        assert mock.call_args.kwargs["modelo"].nombre == "elegido"

    def test_un_modelo_que_no_existe_es_404_y_no_sintetiza(self, client: TestClient):
        """
        Caer al default con un id equivocado sería la peor respuesta: gastaría
        cuota del proveedor equivocado y lo dejaría escrito en `modelo_usado`.
        """
        with patch("src.main.sintetizar_pendientes") as mock:
            respuesta = client.post("/synthesize?modelo_id=9999")

        assert respuesta.status_code == 404
        mock.assert_not_called()

    @pytest.mark.parametrize("id_malo", ["0", "-1", "99999999999999999999999"])
    def test_un_id_imposible_es_422(self, client: TestClient, id_malo):
        with patch("src.main.sintetizar_pendientes") as mock:
            respuesta = client.post(f"/synthesize?modelo_id={id_malo}")

        assert respuesta.status_code == 422
        mock.assert_not_called()


class TestSynthesizeDeUnCluster:
    """
    `POST /clusters/{id}/synthesize` — el modo "paso a paso": alguien elige qué
    cluster vale la pena y con qué proveedor.

    Mismo alcance que la clase de arriba: **con el servicio mockeado se prueba el
    cableado, no el contenido de la respuesta**. Que esa distinción importa está
    medido: `test_sin_configurar_es_422_y_no_500` pasaba en verde mientras el
    mensaje real filtraba el nombre de una variable de entorno, porque el
    mensaje que asertaba lo ponía el propio mock.
    """

    def _cluster(self, session) -> int:
        """
        Un cluster **publicable**: dos medios distintos con una nota cada uno.

        Antes era un cluster pelado, sin ninguna noticia. Funcionaba para
        probar el cableado porque el servicio estaba mockeado, pero es un
        estado que no puede producir nada — y cuando el endpoint aprendió a no
        gastar una llamada en un cluster que no llega al mínimo de medios,
        estos tests empezaron a cortar antes de llegar al mock. El fixture
        estaba irrealista, no la guarda.
        """
        from datetime import datetime

        from src.models import Medio, Noticia

        cluster = Cluster(titulo_evento="Un evento", estado="abierto")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        for i in range(2):
            medio = Medio(
                nombre=f"MedioCableado{i}",
                url_base=f"https://cableado{i}.test",
                feeds_rss=[f"https://cableado{i}.test/rss"],
            )
            session.add(medio)
            session.commit()
            session.refresh(medio)
            session.add(
                Noticia(
                    medio_id=medio.id,
                    cluster_id=cluster.id,
                    titulo=f"Nota {i}",
                    url=f"https://cableado{i}.test/{i}",
                    guid=f"guid-cableado-{i}",
                    contenido_limpio="Cuerpo suficientemente largo de la nota.",
                    fecha_publicacion=datetime.utcnow(),
                    embedding=[0.1] * 384,
                )
            )
        session.commit()
        return cluster.id

    def test_le_pasa_ese_cluster_y_devuelve_su_resultado(
        self, client: TestClient, session
    ):
        """
        Cableado: que el endpoint le entregue al servicio el cluster que se pidió
        y devuelva lo que el servicio contesta. Que la síntesis funcione es cosa
        de `test_synthesis.py`.
        """
        cid = self._cluster(session)
        resultado = {"creados": 2, "actualizados": 0, "descartados": 1}

        with patch("src.main.sintetizar_cluster", return_value=resultado) as mock:
            respuesta = client.post(f"/clusters/{cid}/synthesize")

        assert respuesta.status_code == 200
        assert respuesta.json() == {
            "status": "ok", "cluster_id": cid, "sintetizado": True, **resultado
        }
        assert mock.call_args[0][1].id == cid

    def test_con_modelo_id_usa_esa_fila(self, client: TestClient, session):
        cid = self._cluster(session)
        fila = ModeloIA(
            nombre="elegido", adaptador=Adaptador.GEMINI, modelo="m", activo=False
        )
        session.add(fila)
        session.commit()
        session.refresh(fila)

        with patch("src.main.sintetizar_cluster", return_value={}) as mock:
            respuesta = client.post(
                f"/clusters/{cid}/synthesize?modelo_id={fila.id}"
            )

        assert respuesta.status_code == 200
        assert mock.call_args[0][2].nombre == "elegido"

    def test_sin_modelo_id_deja_que_lo_resuelva_el_servicio(
        self, client: TestClient, session
    ):
        """
        Se le pasa el centinela `_RESOLVER` y no `None`: `None` significa "no
        hay modelo" y haría fallar la síntesis en vez de usar el activo.
        """
        from src.services.synthesis import _RESOLVER

        cid = self._cluster(session)
        with patch("src.main.sintetizar_cluster", return_value={}) as mock:
            client.post(f"/clusters/{cid}/synthesize")

        assert mock.call_args[0][2] is _RESOLVER

    def test_un_cluster_que_no_existe_es_404(self, client: TestClient):
        with patch("src.main.sintetizar_cluster") as mock:
            respuesta = client.post("/clusters/9999/synthesize")

        assert respuesta.status_code == 404
        mock.assert_not_called()

    def test_sin_configurar_es_422_y_no_500(self, client: TestClient, session):
        """
        Cubre el **código de estado**, y nada más que eso.

        El mensaje que se inyecta acá es inventado y benigno, así que este test
        no dice nada sobre lo que sale de verdad por ese `detalle` — de hecho
        pasaba en verde mientras el mensaje real filtraba el nombre de una
        variable de entorno. Lo que el contenido de la respuesta sí prueba está
        en `TestLasRespuestasDeSintesisNoNombranLaVariable`, sin mocks.
        """
        from src.services.synthesis import SintesisSinConfigurar

        cid = self._cluster(session)
        with patch(
            "src.main.sintetizar_cluster",
            side_effect=SintesisSinConfigurar("no hay modelo activo"),
        ):
            respuesta = client.post(f"/clusters/{cid}/synthesize")

        assert respuesta.status_code == 422

    def test_el_bloqueo_de_contenido_es_422_con_su_propio_mensaje(
        self, client: TestClient, session
    ):
        """
        No es un fallo técnico sino el proveedor rechazando el contenido, y el
        mensaje tiene que decirlo: con otro texto genérico, quien lo lea va a
        buscar el problema en el motor.
        """
        from src.services.synthesis import SintesisBloqueada

        cid = self._cluster(session)
        with patch(
            "src.main.sintetizar_cluster",
            side_effect=SintesisBloqueada("filtro de seguridad"),
        ):
            respuesta = client.post(f"/clusters/{cid}/synthesize")

        assert respuesta.status_code == 422
        assert "bloqueó el contenido" in respuesta.json()["detalle"]

    @pytest.mark.parametrize("id_malo", ["0", "-1", "99999999999999999999999"])
    def test_un_id_de_cluster_imposible_es_422(self, client: TestClient, id_malo):
        with patch("src.main.sintetizar_cluster") as mock:
            respuesta = client.post(f"/clusters/{id_malo}/synthesize")

        assert respuesta.status_code == 422
        mock.assert_not_called()

    def test_fallo_tecnico_del_proveedor_es_422_y_no_500(
        self, client: TestClient, session
    ):
        """
        El hallazgo real: un `ErrorDeProveedor` que agota los 3 reintentos de
        `tenacity` (un 429, un JSON mal armado) llegaba hasta acá como un
        `ValueError` sin atajar en ningún `except` de este endpoint, y salía
        como un 500 sin manejar — justo la condición más esperable de este
        endpoint, indistinguible de un bug del motor.

        **Se mockea `synthesis.llamar_modelo`, no `sintetizar_cluster`.** Un
        mock un nivel más arriba no habría visto si el endpoint atrapa lo que
        la traducción real produce, que es justamente lo que fallaba antes de
        que existiera `SintesisFallida`.
        """
        from datetime import datetime

        from src.models import Medio, Noticia
        from src.services import synthesis
        from src.services.synthesis import SintesisFallida

        session.add(
            ModeloIA(nombre="titular", adaptador=Adaptador.GEMINI, modelo="m", activo=True)
        )
        cluster = Cluster(titulo_evento="Evento", estado="abierto")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        for i, nombre in enumerate(("Uno", "Dos")):
            medio = Medio(
                nombre=nombre,
                url_base=f"https://{nombre}.test",
                feeds_rss=[f"https://{nombre}.test/rss"],
            )
            session.add(medio)
            session.commit()
            session.refresh(medio)
            session.add(
                Noticia(
                    medio_id=medio.id,
                    cluster_id=cluster.id,
                    titulo=f"Titulo {i}",
                    url=f"https://{nombre}.test/{i}",
                    guid=f"guid-{i}",
                    contenido_limpio="Cuerpo suficientemente largo de la nota.",
                    fecha_publicacion=datetime.utcnow(),
                    embedding=[0.1] * 384,
                )
            )
        session.commit()

        with patch.object(
            synthesis, "llamar_modelo",
            side_effect=SintesisFallida("titular: HTTP 429 Too Many Requests"),
        ):
            respuesta = client.post(f"/clusters/{cluster.id}/synthesize")

        assert respuesta.status_code == 422
        assert respuesta.status_code != 500


class TestLaGuardaContraLaAmplificacionDeCosto:
    """
    `POST /clusters/{id}/synthesize` no gasta una llamada al proveedor
    repitiendo una síntesis con la misma entrada.

    **El hallazgo, medido**: 5 POST seguidos daban 5 llamadas reales al
    proveedor, contra 1 de `POST /synthesize` —que filtra por
    `clusters_pendientes`—. Con `API_TOKEN` opcional, un doble clic, un
    reintento por timeout o un script en bucle gastaban cuota sin que nadie
    hubiera decidido gastarla.

    **Estos tests NO mockean `sintetizar_cluster`**, y no pueden: lo que se
    prueba es cuántas veces se llega de verdad al proveedor, así que el mock
    va en la frontera de red y el conteo se hace ahí.
    """

    def _motor(self, session, noticias=2):
        """Un modelo activo y un cluster publicable, ambos de verdad."""
        from datetime import datetime

        from src.models import Medio, Noticia

        session.add(
            ModeloIA(nombre="titular", adaptador=Adaptador.GEMINI, modelo="m", activo=True)
        )
        cluster = Cluster(titulo_evento="Un evento", estado="abierto")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        for i in range(noticias):
            medio = Medio(
                nombre=f"Medio{i}",
                url_base=f"https://medio{i}.test",
                feeds_rss=[f"https://medio{i}.test/rss"],
            )
            session.add(medio)
            session.commit()
            session.refresh(medio)
            session.add(
                Noticia(
                    medio_id=medio.id,
                    cluster_id=cluster.id,
                    titulo=f"Titulo {i}",
                    url=f"https://medio{i}.test/{i}",
                    guid=f"guid-{i}",
                    contenido_limpio="Cuerpo suficientemente largo de la nota.",
                    fecha_publicacion=datetime.utcnow(),
                    embedding=[0.1] * 384,
                )
            )
        session.commit()
        return cluster

    def _respuesta(self):
        from src.services.synthesis import (
            AnguloGenerado,
            EnfoqueMedio,
            RespuestaSintesis,
        )
        from src.services.topicos import Topico

        return RespuestaSintesis(
            angulos=[
                AnguloGenerado(
                    id_existente=None,
                    titulo_angulo="Un angulo",
                    resumen_neutro="Resumen neutro con longitud suficiente.",
                    puntos_clave=["Uno", "Dos"],
                    topicos=[Topico.SOCIEDAD],
                    subtopicos=[],
                    comparativa_enfoques=[
                        EnfoqueMedio(medio="Medio0", destaco="A", omitio="B", cita="c0"),
                        EnfoqueMedio(medio="Medio1", destaco="C", omitio="D", cita="c1"),
                    ],
                    notas=[1, 2],
                    relevancia_social=False,
                    resumen_redes=None,
                    hashtags=[],
                )
            ]
        )

    def test_cinco_post_identicos_son_una_sola_llamada_al_proveedor(
        self, client: TestClient, session
    ):
        """El hallazgo, convertido en test: antes eran 5 llamadas."""
        from src.services import synthesis

        cluster = self._motor(session)
        cuenta = {"n": 0}

        def _contar(prompt, modelo):
            cuenta["n"] += 1
            return self._respuesta()

        with patch.object(synthesis, "llamar_modelo", side_effect=_contar):
            respuestas = [
                client.post(f"/clusters/{cluster.id}/synthesize") for _ in range(5)
            ]

        assert all(r.status_code == 200 for r in respuestas)
        assert cuenta["n"] == 1, "solo la primera tenía material nuevo"
        assert respuestas[0].json()["sintetizado"] is not False
        assert respuestas[4].json()["sintetizado"] is False

    def test_el_que_no_sintetiza_dice_por_que_con_una_categoria_cerrada(
        self, client: TestClient, session
    ):
        """
        200 y no 4xx: no falló nada, el motor decidió no gastar. Y el motivo
        es un valor cerrado, no prosa — mismo criterio que `agotados`.
        """
        from src.services import synthesis

        cluster = self._motor(session)

        with patch.object(synthesis, "llamar_modelo", side_effect=lambda p, m: self._respuesta()):
            client.post(f"/clusters/{cluster.id}/synthesize")
            segunda = client.post(f"/clusters/{cluster.id}/synthesize")

        assert segunda.status_code == 200
        assert segunda.json() == {
            "status": "ok",
            "cluster_id": cluster.id,
            "sintetizado": False,
            "motivo": "sin_material_nuevo",
        }

    def test_forzar_si_vuelve_a_sintetizar(self, client: TestClient, session):
        """
        La salida deliberada. Es lo que manda el botón "volver a sintetizar"
        de una interfaz, y lo que hace falta para comparar el mismo cluster
        con otro modelo.
        """
        from src.services import synthesis

        cluster = self._motor(session)
        cuenta = {"n": 0}

        def _contar(prompt, modelo):
            cuenta["n"] += 1
            return self._respuesta()

        with patch.object(synthesis, "llamar_modelo", side_effect=_contar):
            client.post(f"/clusters/{cluster.id}/synthesize")
            forzada = client.post(f"/clusters/{cluster.id}/synthesize?forzar=true")

        assert forzada.status_code == 200
        assert cuenta["n"] == 2
        assert forzada.json()["sintetizado"] is not False

    def test_con_material_nuevo_pasa_sin_pedir_forzar(
        self, client: TestClient, session
    ):
        """
        La guarda no le pide nada a quien tiene material nuevo: ese es el
        caso normal y no tiene que aprender ningún parámetro.
        """
        from datetime import datetime

        from src.models import Noticia
        from src.services import synthesis

        cluster = self._motor(session)
        cuenta = {"n": 0}

        def _contar(prompt, modelo):
            cuenta["n"] += 1
            return self._respuesta()

        with patch.object(synthesis, "llamar_modelo", side_effect=_contar):
            client.post(f"/clusters/{cluster.id}/synthesize")

            # Llega una nota nueva al mismo cluster.
            medio_id = cluster.noticias[0].medio_id
            session.add(
                Noticia(
                    medio_id=medio_id,
                    cluster_id=cluster.id,
                    titulo="Nota nueva",
                    url="https://medio0.test/nueva",
                    guid="guid-nueva",
                    contenido_limpio="Cuerpo de la nota nueva, suficientemente largo.",
                    fecha_publicacion=datetime.utcnow(),
                    embedding=[0.1] * 384,
                )
            )
            session.commit()

            segunda = client.post(f"/clusters/{cluster.id}/synthesize")

        assert segunda.status_code == 200
        assert cuenta["n"] == 2
        assert segunda.json()["sintetizado"] is not False


class TestNoGastaEnUnClusterQueNoPuedePublicar:
    """
    Un cluster con menos de `MIN_MEDIOS_CLUSTER` medios distintos no llega al
    proveedor: no puede producir ningún ángulo publicable.

    **La primera versión de este hallazgo estaba mal, y por una sonda mal
    armada.** Decía que un cluster `descartado` podía sintetizarse y llegar al
    back-end contradiciendo la regla de las dos voces. La sonda que lo
    "probaba" construía un descartado **con dos medios**, que es un estado que
    el motor no puede producir: `descartado` se pone solo cuando los medios
    distintos son menos que el mínimo, y el agrupamiento no asigna noticias a
    clusters cerrados, así que uno descartado queda congelado con un medio.

    Con un descartado realista las defensas aguantan —`_persistir` descarta
    todos los ángulos— y no se crea ninguna fila. Lo que quedaba era otra
    cosa, más chica y real: **se pagaba una llamada al proveedor para no
    obtener nada**, y eso se sabe antes de llamar.
    """

    def _cluster_de_un_solo_medio(self, session, estado="descartado"):
        from datetime import datetime

        from src.models import Medio, Noticia

        session.add(
            ModeloIA(nombre="titular", adaptador=Adaptador.GEMINI, modelo="m", activo=True)
        )
        medio = Medio(
            nombre="Unico",
            url_base="https://unico.test",
            feeds_rss=["https://unico.test/rss"],
        )
        session.add(medio)
        session.commit()
        session.refresh(medio)

        cluster = Cluster(titulo_evento="Hecho de un solo medio", estado=estado)
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        for i in range(2):
            session.add(
                Noticia(
                    medio_id=medio.id,
                    cluster_id=cluster.id,
                    titulo=f"Nota {i}",
                    url=f"https://unico.test/{i}",
                    guid=f"guid-unico-{i}",
                    contenido_limpio="Cuerpo suficientemente largo de la nota.",
                    fecha_publicacion=datetime.utcnow(),
                    embedding=[0.1] * 384,
                )
            )
        session.commit()
        return cluster

    def test_un_descartado_no_llega_al_proveedor(self, client: TestClient, session):
        """Dos notas, un solo medio: ni siquiera se intenta."""
        from src.services import synthesis

        cluster = self._cluster_de_un_solo_medio(session)
        cuenta = {"n": 0}

        def _contar(prompt, modelo):
            cuenta["n"] += 1
            raise AssertionError("no tendria que haberse llamado al proveedor")

        with patch.object(synthesis, "llamar_modelo", side_effect=_contar):
            respuesta = client.post(f"/clusters/{cluster.id}/synthesize")

        assert respuesta.status_code == 200
        assert cuenta["n"] == 0
        assert respuesta.json() == {
            "status": "ok",
            "cluster_id": cluster.id,
            "sintetizado": False,
            "motivo": "sin_medios_suficientes",
        }

    def test_forzar_no_lo_saltea(self, client: TestClient, session):
        """
        `forzar` significa "aunque no haya material nuevo", no "gasta en algo
        imposible". Dejarlo pasar solo habilitaria desperdiciar la llamada.
        """
        from src.services import synthesis

        cluster = self._cluster_de_un_solo_medio(session)
        cuenta = {"n": 0}

        def _contar(prompt, modelo):
            cuenta["n"] += 1
            raise AssertionError("no tendria que haberse llamado al proveedor")

        with patch.object(synthesis, "llamar_modelo", side_effect=_contar):
            respuesta = client.post(f"/clusters/{cluster.id}/synthesize?forzar=true")

        assert respuesta.status_code == 200
        assert cuenta["n"] == 0
        assert respuesta.json()["motivo"] == "sin_medios_suficientes"

    def test_tampoco_uno_abierto_de_un_solo_medio(self, client: TestClient, session):
        """
        La guarda mira los medios y no el estado, asi que cubre tambien un
        `abierto` al que alguien le apunte por id -- que el chequeo por
        `estado == descartado` habria dejado pasar.
        """
        from src.services import synthesis

        cluster = self._cluster_de_un_solo_medio(session, estado="abierto")
        cuenta = {"n": 0}

        def _contar(prompt, modelo):
            cuenta["n"] += 1
            raise AssertionError("no tendria que haberse llamado al proveedor")

        with patch.object(synthesis, "llamar_modelo", side_effect=_contar):
            respuesta = client.post(f"/clusters/{cluster.id}/synthesize")

        assert cuenta["n"] == 0
        assert respuesta.json()["motivo"] == "sin_medios_suficientes"


class TestLaSuiteNoPuedeGastarCuota:
    """
    La red de seguridad de `conftest.sin_credencial_de_ia`, que no la cubre
    ningún otro test porque solo actúa cuando un parche está mal puesto.

    Existe por un caso real: un parche equivocado dejó a `sintetizar_cluster`
    resolviendo el modelo activo de la base, el adaptador leyó la credencial del
    `.env` del desarrollador y le pegó a Gemini de verdad. En un proyecto con
    límite de costos duro eso no puede depender de que ningún parche se
    equivoque.
    """

    def test_ninguna_credencial_de_ia_queda_visible(self):
        import os

        assert [v for v in os.environ if v.startswith("MODELO_API_KEY")] == []

    def test_tampoco_se_ve_el_env_del_desarrollador(self):
        """
        `_del_entorno` mira las dos puertas: el entorno del proceso y el `.env`
        del directorio actual. La fixture cierra las dos.
        """
        from pathlib import Path

        assert not Path(".env").exists()


class TestLasRespuestasDeSintesisNoNombranLaVariable:
    """
    El invariante de la tanda 2, extendido a los endpoints de síntesis.

    Estaba testeado solo para `GET /modelos` y `POST /modelos`, y por esa grieta
    se coló: el campo `agotados` publicaba el mensaje entero de
    `ProveedorNoConfigurado` —que nombra la variable de entorno— **en un 200**,
    sin necesidad siquiera de provocar un error.

    Por qué importa: quien sabe qué variable nombrar puede dar de alta un modelo
    con `base_url` propio y `api_key_env` apuntando ahí, y el motor le entrega
    la credencial del operador durante el sondeo. Con `API_TOKEN` sin definir
    —configuración soportada— lo lee cualquiera que alcance el puerto.

    **Estos tests NO mockean el servicio.** Los del endpoint que ya existían sí,
    y por eso no vieron nada: `test_sin_configurar_es_422_y_no_500` inyecta un
    mensaje inventado y benigno, así que verificaba el código de estado y
    aparentaba verificar el contenido. Acá el mensaje tiene que nacer del camino
    real.
    """

    @pytest.fixture
    def motor_sin_credencial(self, session):
        """
        Un modelo activo cuya variable de entorno no existe, y un cluster
        publicable de verdad. `conftest.sin_credencial_de_ia` ya garantiza que
        la variable no esté ni en el entorno ni en un `.env` alcanzable.
        """
        from datetime import datetime

        from src.models import Medio, Noticia

        session.add(
            ModeloIA(
                nombre="titular",
                adaptador=Adaptador.GEMINI,
                modelo="un-modelo",
                activo=True,
                api_key_env="MODELO_API_KEY_INEXISTENTE",
            )
        )
        cluster = Cluster(titulo_evento="Evento", estado="abierto")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        for i, nombre in enumerate(("Uno", "Dos")):
            medio = Medio(
                nombre=nombre,
                url_base=f"https://{nombre}.test",
                feeds_rss=[f"https://{nombre}.test/rss"],
            )
            session.add(medio)
            session.commit()
            session.refresh(medio)
            session.add(
                Noticia(
                    medio_id=medio.id,
                    cluster_id=cluster.id,
                    titulo=f"Titulo {i}",
                    url=f"https://{nombre}.test/{i}",
                    guid=f"guid-{i}",
                    contenido_limpio="Cuerpo suficientemente largo de la nota.",
                    fecha_publicacion=datetime.utcnow(),
                    embedding=[0.1] * 384,
                )
            )
        session.commit()
        return cluster

    def test_synthesize_no_publica_la_variable_en_su_200(
        self, client: TestClient, motor_sin_credencial
    ):
        """El caso que se escapó: un 200, sin error de por medio."""
        respuesta = client.post("/synthesize")

        assert respuesta.status_code == 200
        assert "MODELO_API_KEY_INEXISTENTE" not in respuesta.text
        # Y el motivo igual se informa, en forma de categoría cerrada.
        assert respuesta.json()["agotados"] == {"titular": "sin_configurar"}

    def test_el_endpoint_por_cluster_no_publica_la_variable_en_su_422(
        self, client: TestClient, motor_sin_credencial
    ):
        respuesta = client.post(f"/clusters/{motor_sin_credencial.id}/synthesize")

        assert respuesta.status_code == 422
        assert "MODELO_API_KEY_INEXISTENTE" not in respuesta.text
        # Dice qué modelo y qué le pasa, sin decir dónde mirar la credencial.
        assert "titular" in respuesta.json()["detalle"]

    def test_el_detalle_completo_queda_en_el_log(
        self, client: TestClient, motor_sin_credencial, caplog
    ):
        """
        La otra mitad: sacarlo de la respuesta no puede costar el diagnóstico.
        Quien opera el motor necesita saber qué variable falta; lo lee en el log.
        """
        import logging

        with caplog.at_level(logging.ERROR, logger="src.services.synthesis"):
            client.post("/synthesize")

        assert "MODELO_API_KEY_INEXISTENTE" in caplog.text

    def test_agotados_solo_lleva_categorias_cerradas(
        self, client: TestClient, motor_sin_credencial
    ):
        """
        `agotados` viaja en un 200, así que todo lo que entre ahí es público.
        Un valor cerrado no puede filtrar por descuido; texto libre sí, y ya lo
        hizo una vez.
        """
        from src.services.synthesis import (
            AGOTADO_FALLOS_SEGUIDOS,
            AGOTADO_SIN_CONFIGURAR,
        )

        agotados = client.post("/synthesize").json()["agotados"]

        assert set(agotados.values()) <= {
            AGOTADO_SIN_CONFIGURAR,
            AGOTADO_FALLOS_SEGUIDOS,
        }
