"""
Tests de la taxonomía de tópicos.

Lo que se prueba acá es la **pista** que entra al prompt, no la decisión: quién
elige el tópico es el modelo. Lo importante es que la pista esté normalizada
—que `el-mundo` de La Nación y `internacional` de TN lleguen como lo mismo— y
que cuando no sabe, diga que no sabe en vez de inventar.
"""
import pytest

from src.services.topicos import (
    NINGUNO,
    SECCIONES,
    Topico,
    TopicoSecundario,
    topico_declarado,
)


class TestTaxonomia:
    def test_el_secundario_tiene_los_mismos_valores_mas_ninguno(self):
        """
        Se deriva de `Topico` justamente para que no se desincronicen al agregar
        una categoría. Este test es el que lo verifica.
        """
        principales = {t.value for t in Topico}
        secundarios = {t.value for t in TopicoSecundario}

        assert secundarios == principales | {NINGUNO}

    def test_todas_las_secciones_apuntan_a_un_topico_valido(self):
        assert all(isinstance(t, Topico) for t in SECCIONES.values())

    def test_no_hay_topico_de_opinion(self):
        """
        Opinión y columnismo son **género**, no tema: una columna sobre
        inflación es economía. Misma distinción que con el horóscopo.
        """
        valores = {t.value for t in Topico}

        assert "opinion" not in valores
        assert "columnistas" not in valores


class TestTopicoDeclarado:
    @pytest.mark.parametrize(
        "url,esperado",
        [
            # Los seis medios nombran lo mismo distinto; por eso hace falta
            # normalizar antes de que la pista sirva de algo.
            ("https://tn.com.ar/internacional/2026/08/09/nota/", Topico.INTERNACIONAL),
            ("https://www.lanacion.com.ar/el-mundo/nota-nid09082026/", Topico.INTERNACIONAL),
            ("https://www.lanacion.com.ar/estados-unidos/nota-nid09082026/", Topico.INTERNACIONAL),
            ("https://www.cronista.com/economia-politica/nota/", Topico.ECONOMIA),
            ("https://www.cronista.com/negocios/nota/", Topico.ECONOMIA),
            ("https://tn.com.ar/economia/nota/", Topico.ECONOMIA),
            ("https://tn.com.ar/show/nota/", Topico.ESPECTACULOS),
            ("https://www.paparazzi.com.ar/teve/nota/", Topico.ESPECTACULOS),
            ("https://www.revistagente.com/entretenimiento/nota/", Topico.ESPECTACULOS),
            ("https://www.ciudad.com.ar/espectaculos/nota/", Topico.ESPECTACULOS),
            ("https://www.lanacion.com.ar/seguridad/nota/", Topico.POLICIALES),
            ("https://tn.com.ar/policiales/nota/", Topico.POLICIALES),
            ("https://tn.com.ar/deportes/futbol/2026/08/09/nota/", Topico.DEPORTES),
        ],
    )
    def test_normaliza_la_seccion_de_cada_medio(self, url, esperado):
        assert topico_declarado(url) == esperado

    @pytest.mark.parametrize(
        "url",
        [
            # Cajón de sastre de las revistas: agrupa cosas de temas distintos.
            "https://www.revistagente.com/actualidad/nota/",
            # Género, no tema.
            "https://www.cronista.com/columnistas/nota/",
            "https://www.lanacion.com.ar/opinion/nota/",
            # Sección que no existe en el mapa.
            "https://tn.com.ar/seccion-inventada/nota/",
            # Sin ruta.
            "https://tn.com.ar",
        ],
    )
    def test_devuelve_none_cuando_no_sabe(self, url):
        """
        Que el modelo decida sin pista es mejor que darle una equivocada. Por
        eso `None` es una respuesta válida y no un fallo.
        """
        assert topico_declarado(url) is None

    def test_no_le_importa_el_resto_de_la_url(self):
        """
        Mira solo el primer segmento, a diferencia de
        `categorias.categoria_no_evento`, que busca en la URL entera. Son dos
        preguntas distintas: el género puede aparecer en cualquier sección, el
        tema es justo lo que la sección declara.
        """
        assert topico_declarado("https://tn.com.ar/deportes/economia/nota/") == Topico.DEPORTES

    def test_es_insensible_a_mayusculas(self):
        assert topico_declarado("https://tn.com.ar/Deportes/nota/") == Topico.DEPORTES
