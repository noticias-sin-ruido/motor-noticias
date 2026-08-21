"""
El token de operador de la API.

Lo que se prueba acá no es "el token funciona" sino las tres cosas que pueden
salir mal y no se ven: que algún endpoint quede afuera de la puerta, que la
salud deje de responderle a Docker, y que la comparación filtre información.
"""
import logging

import pytest

from src import auth
from src.config import settings

TOKEN = "un-token-de-prueba-largo-y-aburrido"

# **Todas las rutas de escritura, escritas a mano y no descubiertas del `app`.**
# Sacarlas de `app.routes` haría que el test creciera solo con la aplicación —
# que suena bien pero es justamente lo que lo volvería inútil: un endpoint nuevo
# entraría a la lista y se comprobaría contra sí mismo. Escritas a mano, agregar
# un endpoint sin tocar este archivo deja el hueco a la vista en la revisión.
PROTEGIDAS = [
    ("post", "/ingest"),
    ("post", "/vectorize"),
    ("post", "/cluster"),
    ("post", "/synthesize"),
    ("post", "/deliver"),
    ("get", "/search?q=cualquier+cosa"),
    ("get", "/clusters"),
    ("get", "/modelos"),
    ("post", "/modelos"),
    ("patch", "/modelos/1?activo=false"),
]


@pytest.fixture
def con_token(monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", TOKEN)


@pytest.fixture
def sin_token(monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", None)


class TestConToken:
    @pytest.mark.parametrize("metodo, ruta", PROTEGIDAS)
    def test_sin_credencial_da_401(self, client, session, con_token, metodo, ruta):
        respuesta = getattr(client, metodo)(ruta)
        assert respuesta.status_code == 401, (
            f"{metodo.upper()} {ruta} respondió {respuesta.status_code} sin token: "
            f"quedó fuera de la puerta"
        )

    @pytest.mark.parametrize(
        "metodo, ruta",
        # `/search` queda afuera de **esta** comprobación (no de la de arriba):
        # una vez pasada la puerta llega a la base, y usa el operador `<=>` de
        # pgvector, que SQLite no entiende. Lo que se puede probar acá es que la
        # puerta lo rechaza sin token, y eso sí está cubierto.
        [(m, r) for m, r in PROTEGIDAS if not r.startswith("/search")],
    )
    def test_con_credencial_no_da_401(self, client, session, con_token, metodo, ruta):
        """
        No se afirma un 200: varios de estos hacen trabajo real y pueden dar 404
        o 422 por sus propios motivos. Lo que importa es que **la puerta los
        deje pasar**.
        """
        respuesta = getattr(client, metodo)(
            ruta, headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert respuesta.status_code != 401

    @pytest.mark.parametrize(
        "metodo, ruta, cuerpo",
        [
            ("get", "/search?q=ab", None),          # `q` tiene min_length=3
            ("post", "/modelos", {"nombre": ""}),   # cuerpo que no valida
        ],
    )
    def test_la_puerta_corre_antes_que_la_validacion(
        self, client, session, con_token, metodo, ruta, cuerpo
    ):
        """
        Verificado, y no es obvio: un pedido **mal formado y sin token** da 401,
        no 422.

        Si fuera al revés, un anónimo podría sondear la forma de los parámetros
        —qué campos existen, qué rangos aceptan— sin tener credencial. Es poco,
        pero es información que la puerta existe para no dar, y depende de un
        detalle del orden de resolución de dependencias de FastAPI que podría
        cambiar sin que nada más se entere.
        """
        extra = {"json": cuerpo} if cuerpo is not None else {}
        respuesta = getattr(client, metodo)(ruta, **extra)
        assert respuesta.status_code == 401

    @pytest.mark.parametrize(
        "cabecera",
        [
            "",
            "Bearer",
            "Bearer ",
            f"Bearer {TOKEN}x",
            f"Bearer {TOKEN[:-1]}",
            TOKEN,                      # sin el esquema
            f"bearer {TOKEN}",          # esquema en minúscula
            f"Basic {TOKEN}",
            f"Bearer  {TOKEN}",         # espacio de más
        ],
    )
    def test_las_variantes_casi_correctas_no_entran(
        self, client, session, con_token, cabecera
    ):
        """
        Los adversariales, que son los que discriminan una comparación correcta
        de una rota: un `in`, un `startswith` o un `lower()` de más dejarían
        pasar alguna de éstas.
        """
        respuesta = client.get("/modelos", headers={"Authorization": cabecera})
        assert respuesta.status_code == 401

    def test_la_salud_responde_sin_token(self, client, session, con_token):
        """
        **Lo llama Docker desde adentro del contenedor** (`curl -f
        http://localhost:8000/` en el `HEALTHCHECK`). Si pidiera token, el
        contenedor se reportaría enfermo para siempre — o habría que meter la
        credencial en el `Dockerfile`.
        """
        assert client.get("/").status_code == 200

    @pytest.mark.parametrize("ruta", ["/docs", "/openapi.json"])
    def test_la_documentacion_queda_abierta(self, client, session, con_token, ruta):
        """Expone la forma de la API, no sus datos. Es una decisión, no un olvido."""
        assert client.get(ruta).status_code == 200


class TestSinToken:
    """
    Sin `API_TOKEN` la API queda abierta. **Es el default y es deliberado**: el
    motor lo despliega gente distinta en contextos distintos, y quien lo corre
    en su notebook no debería pelearse con una credencial.
    """

    @pytest.mark.parametrize(
        "metodo, ruta",
        [(m, r) for m, r in PROTEGIDAS if not r.startswith("/search")],
    )
    def test_todo_responde_sin_credencial(self, client, session, sin_token, metodo, ruta):
        assert getattr(client, metodo)(ruta).status_code != 401

    def test_una_cabecera_cualquiera_no_molesta(self, client, session, sin_token):
        """Sin token configurado, lo que venga en `Authorization` se ignora."""
        respuesta = client.get("/modelos", headers={"Authorization": "Bearer lo-que-sea"})
        assert respuesta.status_code == 200

    def test_el_arranque_avisa_de_que_esta_abierta(self, sin_token, caplog):
        """
        Un despliegue sin token es una decisión válida, pero tiene que ser una
        **decisión y no un descuido** — y la diferencia es que alguien lo lea.
        """
        with caplog.at_level(logging.WARNING):
            auth.avisar_si_esta_abierta()

        assert "SIN TOKEN" in caplog.text

    def test_con_token_el_arranque_no_grita(self, con_token, caplog):
        with caplog.at_level(logging.WARNING):
            auth.avisar_si_esta_abierta()

        assert "SIN TOKEN" not in caplog.text


def test_la_comparacion_es_de_tiempo_constante():
    """
    **Guarda sobre el mecanismo, no sobre el resultado.** Un `==` pelado da el
    mismo 401 que `compare_digest`, así que ningún test de comportamiento puede
    distinguirlos: la comparación de strings de Python corta en el primer byte
    distinto y el tiempo de respuesta filtra cuántos caracteres se acertaron.

    Se mira el código fuente porque es lo único que lo delata.
    """
    import inspect

    fuente = inspect.getsource(auth.exigir_token)
    assert "compare_digest" in fuente, (
        "la comparación del token dejó de ser de tiempo constante"
    )
