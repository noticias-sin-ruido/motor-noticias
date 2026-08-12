"""
Tests de la taxonomía de tópicos y subtópicos.

Lo que se prueba acá es la **pista** que entra al prompt y la garantía
mecánica de jerarquía (`con_padres_completos`), no la decisión editorial en sí:
quién elige el tópico y el subtópico es el modelo. Lo importante es que la
pista esté normalizada —que `el-mundo` de La Nación y `internacional` de TN
lleguen como lo mismo—, que cuando no sabe diga que no sabe en vez de inventar,
y que un subtópico nunca quede sin su categoría padre.
"""
import pytest

from src.services.topicos import (
    SECCIONES,
    SUBSECCIONES,
    SUBTOPICO_PADRE,
    Subtopico,
    Topico,
    con_padres_completos,
    subtopico_declarado,
    topico_declarado,
)


class TestTaxonomia:
    def test_todas_las_secciones_apuntan_a_un_topico_valido(self):
        assert all(isinstance(t, Topico) for t in SECCIONES.values())

    def test_todas_las_subsecciones_apuntan_a_un_subtopico_valido(self):
        assert all(isinstance(s, Subtopico) for s in SUBSECCIONES.values())

    def test_no_hay_topico_de_opinion(self):
        """
        Opinión y columnismo son **género**, no tema: una columna sobre
        inflación es economía. Misma distinción que con el horóscopo.
        """
        valores = {t.value for t in Topico}

        assert "opinion" not in valores
        assert "columnistas" not in valores

    def test_todo_subtopico_tiene_un_padre(self):
        """
        Si un subtópico nuevo se agrega al enum sin agregarle padre, el `KeyError`
        de `con_padres_completos` recién aparecería en producción, con Gemini de
        por medio. Este test lo atrapa antes.
        """
        for subtopico in Subtopico:
            assert subtopico in SUBTOPICO_PADRE
            assert isinstance(SUBTOPICO_PADRE[subtopico], Topico)


class TestConPadresCompletos:
    def test_agrega_el_padre_faltante(self):
        """
        El caso que motivó todo el rediseño: el modelo elige un subtópico
        (fútbol) sin haber incluido su categoría (deportes) entre los tópicos.
        """
        resultado = con_padres_completos([Topico.ECONOMIA], [Subtopico.FUTBOL])

        assert Topico.DEPORTES in resultado
        assert Topico.ECONOMIA in resultado

    def test_no_duplica_si_el_padre_ya_estaba(self):
        resultado = con_padres_completos([Topico.DEPORTES], [Subtopico.FUTBOL])

        assert resultado == [Topico.DEPORTES]

    def test_sin_subtopicos_no_toca_los_topicos(self):
        resultado = con_padres_completos([Topico.POLITICA], [])

        assert resultado == [Topico.POLITICA]

    def test_puede_superar_el_tope_de_dos_por_consistencia(self):
        """
        El tope de 2 tópicos es una guía de prompt, no una regla dura: si el
        modelo ya usó las 2 entradas y un subtópico elegido pertenece a una
        tercera categoría, la consistencia (el subtópico nunca queda huérfano)
        pesa más que el tope.
        """
        resultado = con_padres_completos(
            [Topico.DEPORTES, Topico.ECONOMIA], [Subtopico.CHIMENTOS]
        )

        assert set(resultado) == {Topico.DEPORTES, Topico.ECONOMIA, Topico.ESPECTACULOS}

    def test_varios_subtopicos_del_mismo_padre_no_lo_duplican(self):
        resultado = con_padres_completos(
            [], [Subtopico.FUTBOL, Subtopico.RUGBY, Subtopico.TENIS]
        )

        assert resultado == [Topico.DEPORTES]


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


class TestSubtopicoDeclarado:
    @pytest.mark.parametrize(
        "url,esperado",
        [
            # El caso dominante medido: 246 de 283 notas de deportes con
            # segundo segmento están en /deportes/futbol/...
            ("https://tn.com.ar/deportes/futbol/2026/08/09/nota/", Subtopico.FUTBOL),
            ("https://www.lanacion.com.ar/deportes/rugby/nota-nid1/", Subtopico.RUGBY),
            ("https://tn.com.ar/deportes/tenis/nota/", Subtopico.TENIS),
            ("https://www.lanacion.com.ar/espectaculos/musica/nota-nid1/", Subtopico.MUSICA),
            ("https://www.lanacion.com.ar/espectaculos/cine/nota-nid1/", Subtopico.CINE),
            ("https://www.lanacion.com.ar/espectaculos/teatro/nota-nid1/", None),
            ("https://www.lanacion.com.ar/economia/campo/nota-nid1/", Subtopico.CAMPO),
            ("https://www.lanacion.com.ar/economia/negocios/nota-nid1/", Subtopico.NEGOCIOS),
            # También reconoce el primer segmento, por si algún medio lo publica
            # directo ahí (medido: ningún medio activo lo hace hoy, pero
            # `SECCIONES` ya contempla ese caso para el tópico).
            ("https://www.lanacion.com.ar/campo/nota-nid1/", Subtopico.CAMPO),
            ("https://tn.com.ar/economia/nota/", None),
            # Salud y educación: sumadas por decisión editorial, no por volumen
            # medido -- ver el docstring de `Subtopico`.
            ("https://www.lanacion.com.ar/salud/nota-nid1/", Subtopico.SALUD),
            ("https://tn.com.ar/sociedad/educacion/nota/", Subtopico.EDUCACION),
        ],
    )
    def test_reconoce_el_primer_o_segundo_segmento(self, url, esperado):
        assert subtopico_declarado(url) == esperado

    def test_devuelve_none_sin_ruta(self):
        assert subtopico_declarado("https://tn.com.ar") is None

    def test_es_insensible_a_mayusculas(self):
        assert subtopico_declarado("https://tn.com.ar/Deportes/Futbol/nota/") == Subtopico.FUTBOL
