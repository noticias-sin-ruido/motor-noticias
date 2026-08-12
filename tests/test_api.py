"""
Pruebas de los endpoints de la API FastAPI.
"""
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.models import Medio


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
    contra Postgres — ver VALIDACION_FASE2.md.
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
