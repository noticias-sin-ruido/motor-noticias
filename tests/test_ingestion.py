"""
Tests del servicio de ingesta: parseo de feed, limpieza de HTML, filtro de
notas en vivo, deduplicación por guid y manejo de fallos de red.
"""
from datetime import datetime
from unittest.mock import patch

import httpx
import pytest
from sqlmodel import Session, select

from src.models import Medio, Noticia
from src.services import ingestion
from tests.conftest import contar_queries

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
<title>Medio de Prueba</title>
<item>
  <title>Noticia normal de prueba</title>
  <link>https://test.com/noticia-1</link>
  <guid>guid-noticia-1</guid>
  <pubDate>Thu, 06 Aug 2026 10:00:00 GMT</pubDate>
  <content:encoded><![CDATA[<p>Cuerpo <b>completo</b> de la noticia.</p>]]></content:encoded>
</item>
<item>
  <title>EN VIVO: cobertura minuto a minuto</title>
  <link>https://test.com/noticia-2</link>
  <guid>guid-noticia-2</guid>
  <pubDate>Thu, 06 Aug 2026 10:05:00 GMT</pubDate>
  <content:encoded><![CDATA[<p>Contenido en desarrollo.</p>]]></content:encoded>
</item>
</channel>
</rss>
"""

FEED_XML_SIN_CONTENIDO = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
<title>Medio de Prueba</title>
<item>
  <title>Horóscopo de hoy</title>
  <link>https://test.com/horoscopo</link>
  <guid>guid-horoscopo</guid>
  <pubDate>Thu, 06 Aug 2026 10:00:00 GMT</pubDate>
  <description>Solo un resumen corto, sin content:encoded.</description>
</item>
</channel>
</rss>
"""


# La misma nota que FEED_XML pero con otro guid, como la sirve el feed de su
# sección: comparte la `url`, que es lo único que permite reconocerla.
FEED_XML_SECCION = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
<title>Medio de Prueba — Política</title>
<item>
  <title>Noticia normal de prueba</title>
  <link>https://test.com/noticia-1</link>
  <guid>OTRO-guid-para-la-misma-nota</guid>
  <pubDate>Thu, 06 Aug 2026 10:00:00 GMT</pubDate>
  <content:encoded><![CDATA[<p>Cuerpo <b>completo</b> de la noticia.</p>]]></content:encoded>
</item>
<item>
  <title>Nota que solo está en la sección</title>
  <link>https://test.com/noticia-3</link>
  <guid>guid-noticia-3</guid>
  <pubDate>Thu, 06 Aug 2026 11:00:00 GMT</pubDate>
  <content:encoded><![CDATA[<p>Lo que el feed general no trae.</p>]]></content:encoded>
</item>
</channel>
</rss>
"""


@pytest.fixture
def medio(session: Session) -> Medio:
    m = Medio(nombre="Medio Test", url_base="https://test.com", feeds_rss=["https://test.com/rss"])
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


@pytest.fixture
def medio_multifeed(session: Session) -> Medio:
    m = Medio(
        nombre="Medio Grande",
        url_base="https://test.com",
        feeds_rss=["https://test.com/rss", "https://test.com/rss/politica"],
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


class TestVariosFeedsPorMedio:
    """
    El feed general de un diario grande es una selección de portada: medido, el
    de La Nación trae el 26% de lo que publica. Recorrer también los de sección
    es lo que recupera el resto, y trae consigo el problema de que la misma nota
    llega dos veces.
    """

    def test_recorre_todos_los_feeds(self, session: Session, medio_multifeed):
        with patch.object(ingestion, "_descargar_feed",
                          side_effect=[FEED_XML, FEED_XML_SECCION]) as descargar:
            stats = ingestion.ingerir_medio(session, medio_multifeed)

        assert descargar.call_count == 2
        assert stats["feeds"] == 2
        # noticia-1 (general) + noticia-3 (solo en la sección). La 2 es en vivo.
        assert stats["nuevas"] == 2

    def test_la_misma_nota_con_distinto_guid_no_se_duplica(
        self, session: Session, medio_multifeed
    ):
        """
        El caso que puede tirar la ingesta entera: nada garantiza que el feed
        general y el de sección le pongan el mismo `guid` a la misma nota. Con
        deduplicar solo por guid, la segunda copia llegaría al INSERT y
        reventaría contra el índice único de `url`.
        """
        with patch.object(ingestion, "_descargar_feed",
                          side_effect=[FEED_XML, FEED_XML_SECCION]):
            stats = ingestion.ingerir_medio(session, medio_multifeed)

        urls = [n.url for n in session.exec(select(Noticia)).all()]
        assert urls.count("https://test.com/noticia-1") == 1
        assert stats["duplicadas"] == 1

    def test_un_feed_caido_no_frena_a_los_demas(self, session: Session, medio_multifeed):
        """
        Que la infraestructura de RSS devuelva 500 en `economia` no es razón
        para perderse `politica`.
        """
        with patch.object(ingestion, "_descargar_feed",
                          side_effect=[httpx.HTTPError("500"), FEED_XML_SECCION]), \
             patch.object(ingestion.alerts, "enviar_alerta"):
            stats = ingestion.ingerir_medio(session, medio_multifeed)

        assert stats["feeds_fallados"] == 1
        assert stats["nuevas"] == 2  # los dos items del feed que sí respondió

    def test_un_error_que_no_es_de_red_tampoco_frena(
        self, session: Session, medio_multifeed
    ):
        """
        Antes solo se aislaban los `httpx.HTTPError`. Cualquier otra cosa —un
        `IntegrityError` del autoflush por una corrida concurrente, un XML que
        rompe el parser— se escapaba de `ingerir_medio` sin commit y abortaba la
        ingesta de todos los medios que faltaban.
        """
        with patch.object(ingestion, "_descargar_feed",
                          side_effect=[RuntimeError("algo raro"), FEED_XML_SECCION]):
            stats = ingestion.ingerir_medio(session, medio_multifeed)

        assert stats["feeds_fallados"] == 1
        assert stats["nuevas"] == 2
        assert "algo raro" in stats["errores"][0]

    def test_los_errores_se_acumulan_por_feed(self, session: Session, medio_multifeed):
        """
        Con un solo campo `error` guardando el último, la caída de un feed se
        leía igual que la del medio entero en la respuesta de `/ingest`.
        """
        with patch.object(ingestion, "_descargar_feed",
                          side_effect=[httpx.HTTPError("uno"), httpx.HTTPError("dos")]), \
             patch.object(ingestion.alerts, "enviar_alerta"):
            stats = ingestion.ingerir_medio(session, medio_multifeed)

        assert len(stats["errores"]) == 2
        assert stats["feeds_fallados"] == 2


class TestHeuristicoEnVivo:
    def test_detecta_palabra_vivo(self):
        assert ingestion.es_en_vivo("EN VIVO: el Senado sesiona")
        assert ingestion.es_en_vivo("Minuto a minuto: así fue la jornada")

    def test_detecta_emoji(self):
        assert ingestion.es_en_vivo("🔴 Últimas noticias del temporal")

    def test_no_detecta_titulo_normal(self):
        assert not ingestion.es_en_vivo("El Senado aprobó la ley de tierras")


class TestLimpiarHtml:
    def test_extrae_texto_plano(self):
        resultado = ingestion.limpiar_html("<p>Hola <b>mundo</b></p>")
        assert resultado == "Hola mundo"


class TestIngerirMedio:
    def test_crea_noticias_nuevas_y_descarta_en_vivo(self, session: Session, medio: Medio):
        with patch.object(ingestion, "_descargar_feed", return_value=FEED_XML):
            stats = ingestion.ingerir_medio(session, medio)

        assert stats["nuevas"] == 1
        assert stats["en_vivo"] == 1
        assert stats["duplicadas"] == 0

        noticias = session.exec(select(Noticia).where(Noticia.medio_id == medio.id)).all()
        assert len(noticias) == 1
        assert noticias[0].guid == "guid-noticia-1"
        assert noticias[0].contenido_limpio == "Cuerpo completo de la noticia."

    def test_descarta_items_sin_content_encoded(self, session: Session, medio: Medio):
        """Items sin content:encoded (ej. horóscopos) se cuentan y descartan, no rompen la corrida."""
        with patch.object(ingestion, "_descargar_feed", return_value=FEED_XML_SIN_CONTENIDO):
            stats = ingestion.ingerir_medio(session, medio)

        assert stats["sin_contenido"] == 1
        assert stats["nuevas"] == 0
        assert stats["errores"] == []

    def test_no_duplica_noticias_ya_ingeridas(self, session: Session, medio: Medio):
        with patch.object(ingestion, "_descargar_feed", return_value=FEED_XML):
            ingestion.ingerir_medio(session, medio)
            stats_segunda_corrida = ingestion.ingerir_medio(session, medio)

        assert stats_segunda_corrida["nuevas"] == 0
        assert stats_segunda_corrida["duplicadas"] == 1

    def test_fallo_de_red_no_rompe_y_envia_alerta(self, session: Session, medio: Medio):
        with patch.object(
            ingestion, "_descargar_feed", side_effect=httpx.ConnectError("fallo simulado")
        ), patch.object(ingestion, "enviar_alerta") as mock_alerta:
            stats = ingestion.ingerir_medio(session, medio)

        assert len(stats["errores"]) == 1
        assert stats["nuevas"] == 0
        mock_alerta.assert_called_once()


class TestIngerirTodosLosMedios:
    def test_solo_procesa_medios_activos(self, session: Session):
        activo = Medio(
            nombre="Activo", url_base="https://a.com", feeds_rss=["https://a.com/rss"], activo=True
        )
        inactivo = Medio(
            nombre="Inactivo",
            url_base="https://i.com",
            feeds_rss=["https://i.com/rss"],
            activo=False,
        )
        session.add(activo)
        session.add(inactivo)
        session.commit()

        with patch.object(ingestion, "_descargar_feed", return_value=FEED_XML):
            resultados = ingestion.ingerir_todos_los_medios(session)

        nombres = [r["medio"] for r in resultados]
        assert "Activo" in nombres
        assert "Inactivo" not in nombres


def _feed_con_items(cantidad: int, prefijo: str) -> str:
    items = "".join(
        f"""<item>
  <title>Noticia {prefijo}-{i}</title>
  <link>https://test.com/{prefijo}-{i}</link>
  <guid>guid-{prefijo}-{i}</guid>
  <pubDate>Thu, 06 Aug 2026 10:00:00 GMT</pubDate>
  <content:encoded><![CDATA[<p>Cuerpo {i}.</p>]]></content:encoded>
</item>"""
        for i in range(cantidad)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
        f"<channel><title>Medio</title>{items}</channel>\n</rss>"
    )


class TestDeduplicacionNoEscala:
    """
    `_ya_esta` corría un `SELECT` por cada item del feed. Con varios feeds por
    medio, eso eran cientos de queries por corrida -- la mayoría para
    descubrir que la nota ya existía. Ahora es una sola consulta por feed
    (`guid IN (...) OR url IN (...)`), sin importar cuántos items tenga.
    """

    def _precargar(self, session: Session, medio: Medio, cantidad: int, prefijo: str) -> None:
        """Deja ya en la base las mismas notas que va a traer el feed, todas duplicadas."""
        for i in range(cantidad):
            session.add(Noticia(
                medio_id=medio.id,
                titulo=f"Noticia {prefijo}-{i}",
                url=f"https://test.com/{prefijo}-{i}",
                guid=f"guid-{prefijo}-{i}",
                contenido_limpio=f"Cuerpo {i}.",
                fecha_publicacion=datetime.utcnow(),
            ))
        session.commit()

    def test_no_hace_una_query_por_item_del_feed(self, session: Session, medio: Medio):
        """
        Con inserts de verdad, "muchos" siempre va a hacer más queries que
        "pocos" -- cada nota nueva es un INSERT propio, y eso no es lo que se
        arregló. Lo que se arregló es el `SELECT` de deduplicación, así que acá
        se aísla precargando la base con las mismas notas que trae el feed: sin
        ningún insert de por medio, la única diferencia entre "pocos" y
        "muchos" es cuántos items hay que chequear.
        """
        self._precargar(session, medio, 3, "pocos")
        with patch.object(ingestion, "_descargar_feed", return_value=_feed_con_items(3, "pocos")):
            with contar_queries(session) as pocos:
                stats_pocos = ingestion.ingerir_medio(session, medio)
        assert stats_pocos == {"medio": medio.nombre, "feeds": 1, "feeds_fallados": 0,
                                "nuevas": 0, "duplicadas": 3, "en_vivo": 0,
                                "sin_contenido": 0, "errores": []}

        self._precargar(session, medio, 40, "muchos")
        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_con_items(40, "muchos")
        ):
            with contar_queries(session) as muchos:
                stats_muchos = ingestion.ingerir_medio(session, medio)
        assert stats_muchos["duplicadas"] == 40

        assert muchos["n"] == pocos["n"]

    def test_no_duplica_items_repetidos_dentro_del_mismo_feed(
        self, session: Session, medio: Medio
    ):
        """
        La consulta en lote se hace una sola vez al principio del feed, así que
        no ve lo que se va agregando dentro del propio loop -- eso lo cubren
        los sets `vistos_guid`/`vistos_url` en Python.
        """
        feed_con_repetido = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
            "<channel><title>Medio</title>"
            '<item><title>Repetida</title><link>https://test.com/rep</link>'
            "<guid>guid-rep</guid><pubDate>Thu, 06 Aug 2026 10:00:00 GMT</pubDate>"
            "<content:encoded><![CDATA[<p>Cuerpo.</p>]]></content:encoded></item>"
            '<item><title>Repetida</title><link>https://test.com/rep</link>'
            "<guid>guid-rep</guid><pubDate>Thu, 06 Aug 2026 10:00:00 GMT</pubDate>"
            "<content:encoded><![CDATA[<p>Cuerpo.</p>]]></content:encoded></item>"
            "</channel>\n</rss>"
        )

        with patch.object(ingestion, "_descargar_feed", return_value=feed_con_repetido):
            stats = ingestion.ingerir_medio(session, medio)

        assert stats["nuevas"] == 1
        assert stats["duplicadas"] == 1
