"""
Pruebas de los endpoints de la API FastAPI.
"""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.config import settings


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


class TestPipelineProgramado:
    """
    El job del scheduler aísla los pasos: uno que falla no frena a los que
    siguen, porque todos son idempotentes y la corrida siguiente retoma sola.
    """

    def _mocks(self, **overrides):
        nombres = {
            "ingerir_todos_los_medios": {"ok": True},
            "vectorizar_pendientes": {"ok": True},
            "cerrar_clusters_vencidos": {"ok": True},
            "agrupar_pendientes": {"ok": True},
            "fusionar_clusters_duplicados": {"ok": True},
            "sintetizar_pendientes": {"ok": True},
            "entregar_pendientes": {"ok": True},
        }
        nombres.update(overrides)
        return nombres

    def test_un_paso_que_falla_no_frena_a_los_siguientes(self):
        from src import main

        config = self._mocks(vectorizar_pendientes=RuntimeError("boom"))
        parches = []
        for nombre, valor in config.items():
            kwargs = (
                {"side_effect": valor} if isinstance(valor, Exception)
                else {"return_value": valor}
            )
            parches.append(patch.object(main, nombre, **kwargs))

        with parches[0], parches[1], parches[2], parches[3], parches[4], parches[5], \
             parches[6], \
             patch.object(main, "enviar_alerta") as alerta, \
             patch.object(main, "get_engine"), \
             patch.object(main, "Session"):
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
