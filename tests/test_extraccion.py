"""
Tests de la segunda vía de ingesta (extracción del cuerpo desde la URL).

Se mockea `_descargar_pagina`, que es la frontera con la red — mismo patrón
que `test_ingestion.py` con `_descargar_feed`. Ningún test sale a internet.
"""
from unittest.mock import patch

import httpx
import pytest

from src.config import settings
from src.services import extraccion
from src.services.extraccion import (
    extraer_articulo,
    extraer_varios,
    normalizar,
    permitido,
)

ROBOTS_PERMISIVO = "User-agent: *\nAllow: /\n"
ROBOTS_RESTRICTIVO = "User-agent: *\nDisallow: /privado/\n"

# Largo cómodo por encima de EXTRACCION_MIN_CARACTERES (500).
CUERPO_LARGO = "Una oración de prueba con suficiente texto. " * 20


def _respuesta(texto: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status, text=texto, request=httpx.Request("GET", "https://x.test/")
    )


@pytest.fixture(autouse=True)
def _robots_limpio():
    """
    La caché de robots.txt es de módulo: sin esto, lo que cachea un test se lo
    encuentra el siguiente y los resultados dependen del orden de ejecución.
    """
    extraccion.limpiar_cache_robots()
    yield
    extraccion.limpiar_cache_robots()


@pytest.fixture
def robots_ok():
    """Sirve un robots.txt permisivo para cualquier dominio."""
    with patch.object(extraccion.httpx, "get", return_value=_respuesta(ROBOTS_PERMISIVO)):
        yield


class TestNormalizar:
    """
    El texto extraído tiene que salir con el mismo formato que produce
    `ingestion.limpiar_html`, que devuelve una sola línea. Si no, el
    delimitador `--- NOTA` del prompt de síntesis deja de ser inequívoco.
    """

    def test_colapsa_saltos_de_linea(self):
        assert normalizar("Primer párrafo.\n\nSegundo párrafo.") == (
            "Primer párrafo. Segundo párrafo."
        )

    def test_colapsa_espacios_multiples_y_tabs(self):
        assert normalizar("Hola   mundo\tde\nprueba") == "Hola mundo de prueba"

    def test_recorta_los_extremos(self):
        assert normalizar("\n  Texto.  \n") == "Texto."

    def test_el_resultado_nunca_tiene_saltos(self):
        """La garantía de la que depende el prompt de síntesis."""
        assert "\n" not in normalizar("a\nb\r\nc\n\n\nd")

    def test_coincide_con_limpiar_html(self):
        """
        Las dos vías de ingesta tienen que producir texto indistinguible: es lo
        que permite sumar esta sin tocar synthesis, vectorization ni
        preprocessing.
        """
        from src.services.ingestion import limpiar_html

        html = "<p>Primer párrafo.</p>\n<p>Segundo párrafo.</p>"
        crudo_trafilatura = "Primer párrafo.\nSegundo párrafo."

        assert normalizar(crudo_trafilatura) == limpiar_html(html)


class TestRobots:
    def test_permite_una_ruta_habilitada(self, robots_ok):
        assert permitido("https://medio.test/nota/una-noticia") is True

    def test_respeta_un_disallow(self):
        with patch.object(
            extraccion.httpx, "get", return_value=_respuesta(ROBOTS_RESTRICTIVO)
        ):
            assert permitido("https://medio.test/privado/x") is False
            assert permitido("https://medio.test/publico/x") is True

    def test_se_pide_una_vez_por_dominio(self):
        """
        Sin caché se pediría el robots.txt una vez por artículo: con el feed de
        Clarín son 10 requests extra por ciclo para releer siempre lo mismo.
        """
        with patch.object(
            extraccion.httpx, "get", return_value=_respuesta(ROBOTS_PERMISIVO)
        ) as get:
            for i in range(5):
                permitido(f"https://medio.test/nota-{i}")

        assert get.call_count == 1

    def test_dominios_distintos_se_consultan_por_separado(self):
        with patch.object(
            extraccion.httpx, "get", return_value=_respuesta(ROBOTS_PERMISIVO)
        ) as get:
            permitido("https://uno.test/nota")
            permitido("https://dos.test/nota")

        assert get.call_count == 2

    def test_si_no_se_puede_leer_no_extrae_y_alerta(self):
        """
        Falla cerrado y ruidoso. Cerrado porque no sabemos qué permite el
        medio; ruidoso porque el silencio acá es perder la cobertura de un
        medio entero sin que nadie se entere.
        """
        with patch.object(
            extraccion.httpx, "get", side_effect=httpx.ConnectError("caido")
        ), patch.object(extraccion.alerts, "enviar_alerta") as alerta:
            assert permitido("https://medio.test/nota") is False

        assert alerta.call_count == 1
        assert alerta.call_args.kwargs["clave"] == "robots:https://medio.test"

    def test_una_url_sin_dominio_no_se_pide(self):
        with patch.object(extraccion.httpx, "get") as get:
            assert permitido("no-es-una-url") is False

        get.assert_not_called()


class TestExtraerArticulo:
    def test_devuelve_el_cuerpo_normalizado(self, robots_ok):
        with patch.object(extraccion, "_descargar_pagina", return_value="<html/>"), \
             patch.object(extraccion.trafilatura, "extract", return_value=CUERPO_LARGO):
            texto = extraer_articulo("https://medio.test/nota")

        assert texto is not None
        assert "\n" not in texto

    def test_no_pide_la_pagina_si_robots_lo_prohibe(self):
        with patch.object(
            extraccion.httpx, "get", return_value=_respuesta(ROBOTS_RESTRICTIVO)
        ), patch.object(extraccion, "_descargar_pagina") as descargar:
            assert extraer_articulo("https://medio.test/privado/nota") is None

        descargar.assert_not_called()

    def test_descarta_lo_que_no_llega_al_piso(self, robots_ok):
        """
        `trafilatura` no falla cuando un medio rediseña su maquetado: devuelve
        un menú o un aviso de cookies que *parece* contenido. Sin este corte
        eso entraría a la base como si fuera la nota.
        """
        menu = "Inicio Secciones Suscribite Contacto"
        with patch.object(extraccion, "_descargar_pagina", return_value="<html/>"), \
             patch.object(extraccion.trafilatura, "extract", return_value=menu):
            assert extraer_articulo("https://medio.test/nota") is None

    def test_justo_por_encima_del_piso_se_acepta(self, robots_ok):
        cuerpo = "x" * settings.EXTRACCION_MIN_CARACTERES
        with patch.object(extraccion, "_descargar_pagina", return_value="<html/>"), \
             patch.object(extraccion.trafilatura, "extract", return_value=cuerpo):
            assert extraer_articulo("https://medio.test/nota") == cuerpo

    def test_si_trafilatura_no_encuentra_nada_devuelve_none(self, robots_ok):
        with patch.object(extraccion, "_descargar_pagina", return_value="<html/>"), \
             patch.object(extraccion.trafilatura, "extract", return_value=None):
            assert extraer_articulo("https://medio.test/nota") is None

    @pytest.mark.parametrize(
        "falla",
        [
            httpx.ConnectError("sin red"),
            httpx.ReadTimeout("tardó"),
            httpx.HTTPStatusError("500", request=None, response=None),
            RuntimeError("cualquier otra cosa"),
        ],
    )
    def test_ninguna_excepcion_escapa(self, robots_ok, falla):
        """
        Es un requisito del contrato, no una comodidad: quien llama corre
        dentro del `try` de `ingerir_feed`, y una excepción que se escape de
        acá haría `rollback` del feed entero, mandaría un mail de alerta y
        contaría un `feeds_fallados`. Un artículo caído no es un feed caído.
        """
        with patch.object(extraccion, "_descargar_pagina", side_effect=falla), \
             patch.object(extraccion.time, "sleep"):
            assert extraer_articulo("https://medio.test/nota") is None

    def test_una_excepcion_de_trafilatura_tampoco_escapa(self, robots_ok):
        with patch.object(extraccion, "_descargar_pagina", return_value="<html/>"), \
             patch.object(
                 extraccion.trafilatura, "extract", side_effect=RuntimeError("boom")
             ):
            assert extraer_articulo("https://medio.test/nota") is None

    def test_reintenta_y_sale_adelante(self, robots_ok):
        with patch.object(
            extraccion,
            "_descargar_pagina",
            side_effect=[httpx.ConnectError("cortado"), "<html/>"],
        ) as descargar, \
             patch.object(extraccion.trafilatura, "extract", return_value=CUERPO_LARGO), \
             patch.object(extraccion.time, "sleep"):
            assert extraer_articulo("https://medio.test/nota") is not None

        assert descargar.call_count == 2

    def test_no_reintenta_mas_de_lo_configurado(self, robots_ok):
        with patch.object(
            extraccion, "_descargar_pagina", side_effect=httpx.ConnectError("cortado")
        ) as descargar, patch.object(extraccion.time, "sleep"):
            extraer_articulo("https://medio.test/nota")

        assert descargar.call_count == settings.EXTRACCION_REINTENTOS + 1

    def test_no_devuelve_la_url_final_tras_redirects(self, robots_ok):
        """
        El extractor entrega texto y nada más. Persistir la URL final en vez de
        la del feed duplicaría el artículo o reventaría contra el índice único
        de `Noticia.url`, tirando la ingesta del medio entero.
        """
        with patch.object(extraccion, "_descargar_pagina", return_value="<html/>"), \
             patch.object(extraccion.trafilatura, "extract", return_value=CUERPO_LARGO):
            resultado = extraer_articulo("https://medio.test/nota")

        assert isinstance(resultado, str)
        assert "http" not in resultado


class TestExtraerVarios:
    def test_devuelve_una_entrada_por_url_incluidos_los_fallos(self, robots_ok):
        urls = ["https://medio.test/a", "https://medio.test/b"]
        with patch.object(
            extraccion, "extraer_articulo", side_effect=[CUERPO_LARGO, None]
        ), patch.object(extraccion.time, "sleep"):
            resultado = extraer_varios(urls)

        assert list(resultado) == urls
        assert resultado["https://medio.test/b"] is None

    def test_pausa_entre_articulos_pero_no_despues_del_ultimo(self, robots_ok):
        """Tres artículos son dos pausas, no tres: la última sería tiempo muerto."""
        with patch.object(extraccion, "extraer_articulo", return_value=CUERPO_LARGO), \
             patch.object(extraccion.time, "sleep") as dormir:
            extraer_varios(["https://m.test/a", "https://m.test/b", "https://m.test/c"])

        assert dormir.call_count == 2
        assert dormir.call_args.args[0] == settings.EXTRACCION_PAUSA_SEGUNDOS

    def test_una_sola_url_no_pausa(self, robots_ok):
        with patch.object(extraccion, "extraer_articulo", return_value=CUERPO_LARGO), \
             patch.object(extraccion.time, "sleep") as dormir:
            extraer_varios(["https://m.test/a"])

        dormir.assert_not_called()
