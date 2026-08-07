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


class TestTestDB:
    """Pruebas del endpoint /test-db."""

    def test_test_db_success(self, client: TestClient):
        """Verifica que /test-db crea un Medio de prueba y retorna 200."""
        response = client.get("/test-db")

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "ok"
        assert "mensaje" in data
        assert "medio_creado" in data

        medio = data["medio_creado"]
        assert medio["id"] is not None
        assert medio["nombre"] == "Medio de Prueba"
        assert medio["url_base"] == "https://ejemplo.com"
        assert medio["feed_rss"] == "https://ejemplo.com/rss"
        assert medio["activo"] is True

    def test_test_db_multiple_calls(self, client: TestClient):
        """Verifica que se pueden hacer múltiples llamadas a /test-db."""
        response1 = client.get("/test-db")
        response2 = client.get("/test-db")

        assert response1.status_code == 200
        assert response2.status_code == 200

        # Ambas llamadas deberían tener IDs diferentes
        id1 = response1.json()["medio_creado"]["id"]
        id2 = response2.json()["medio_creado"]["id"]

        assert id1 != id2


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
