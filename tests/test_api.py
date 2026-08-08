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
        assert "environment" in data


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


class TestClusterEndpoint:
    """Pruebas del endpoint manual POST /cluster."""

    def test_cluster_cierra_y_agrupa(self, client: TestClient):
        """Verifica que /cluster corre el cierre y el agrupamiento, en ese orden."""
        cierre = {"evaluados": 2, "procesados": 1, "descartados": 1}
        agrupamiento = {"evaluadas": 5, "sumadas_a_cluster": 2, "clusters_creados": 1, "sin_match": 2}

        with patch("src.main.cerrar_clusters_vencidos", return_value=cierre) as mock_cierre, \
             patch("src.main.agrupar_pendientes", return_value=agrupamiento) as mock_agrupar:
            response = client.post("/cluster")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "cierre": cierre, "agrupamiento": agrupamiento}
        mock_cierre.assert_called_once()
        mock_agrupar.assert_called_once()


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
