"""
Tests del servicio de ingesta: parseo de feed, limpieza de HTML, filtro de
notas en vivo, deduplicación por guid y manejo de fallos de red.
"""
import logging
import urllib.robotparser
from datetime import datetime
from unittest.mock import patch

import httpx
import pytest
from sqlmodel import Session, select

from src.models import Medio, Noticia
from src.services import extraccion, ingestion
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
        """
        Sin `content:encoded` **y sin `extraer_por_url`**, el item se cuenta y se
        descarta sin romper la corrida (ej. horóscopos).

        La regla de Fase 2 —"el feed debe traer el artículo completo"— sigue
        siendo la predeterminada; la extracción por URL es la excepción que hay
        que declarar medio por medio, y este test es el que la mantiene honesta.
        """
        with patch.object(ingestion, "_descargar_feed", return_value=FEED_XML_SIN_CONTENIDO), \
             patch.object(ingestion, "extraer_varios") as extraer:
            stats = ingestion.ingerir_medio(session, medio)

        assert stats["sin_contenido"] == 1
        assert stats["nuevas"] == 0
        assert stats["errores"] == []
        extraer.assert_not_called()

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
                                "sin_contenido": 0, "url_invalida": 0, "extraidas": 0,
                                "extraccion_fallida": 0, "errores": []}

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


# Largo cómodo, del orden de un artículo real (el más corto de la medición tuvo
# 701 caracteres). Lo que importa acá no es el piso —eso lo valida el extractor,
# y en estos tests está mockeado— sino que el cuerpo llegue entero a la base.
CUERPO_EXTRAIDO = "Cuerpo que vino de la página del artículo y no del feed. " * 12


def _feed_sin_cuerpo(cantidad: int, prefijo: str = "url") -> str:
    """Feed al estilo Clarín/Perfil: items completos pero sin `content:encoded`."""
    items = "".join(
        f"""<item>
  <title>Noticia {prefijo}-{i}</title>
  <link>https://test.com/{prefijo}-{i}</link>
  <guid>guid-{prefijo}-{i}</guid>
  <pubDate>Thu, 06 Aug 2026 10:00:00 GMT</pubDate>
  <description>Solo el copete de unos 200 caracteres.</description>
</item>"""
        for i in range(cantidad)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
        f"<channel><title>Medio</title>{items}</channel>\n</rss>"
    )


@pytest.fixture
def medio_extractor(session: Session) -> Medio:
    """Un medio cuyo RSS no trae el artículo, como Clarín y Perfil."""
    m = Medio(
        nombre="Medio Sin Cuerpo",
        url_base="https://test.com",
        feeds_rss=["https://test.com/rss"],
        extraer_por_url=True,
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


class TestExtraccionPorUrl:
    """
    La costura entre la ingesta y `services/extraccion.py`.

    El extractor está mockeado en todos: su comportamiento propio ya lo cubre
    `test_extraccion.py`, y lo que se prueba acá es **cuándo** se lo llama, con
    qué, y qué se hace con lo que devuelve.
    """

    def test_extrae_y_persiste_lo_que_el_feed_no_trajo(
        self, session: Session, medio_extractor: Medio
    ):
        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(2)
        ), patch.object(
            ingestion,
            "extraer_varios",
            return_value={
                "https://test.com/url-0": CUERPO_EXTRAIDO,
                "https://test.com/url-1": CUERPO_EXTRAIDO,
            },
        ):
            stats = ingestion.ingerir_medio(session, medio_extractor)

        assert stats["nuevas"] == 2
        assert stats["extraidas"] == 2
        assert stats["extraccion_fallida"] == 0
        # Los items ya no se cuentan como descartes: son candidatos.
        assert stats["sin_contenido"] == 0

        noticias = session.exec(
            select(Noticia).where(Noticia.medio_id == medio_extractor.id)
        ).all()
        assert len(noticias) == 2
        assert all(n.contenido_limpio == CUERPO_EXTRAIDO for n in noticias)

    def test_si_el_feed_trae_el_cuerpo_no_se_extrae(
        self, session: Session, medio_extractor: Medio
    ):
        """
        `extraer_por_url` es "si falta, buscalo en la URL", no "buscalo
        siempre". Importa para el feed de respaldo de Clarín, que sí trae
        `content:encoded`: ahorra un request y una pausa por artículo.
        """
        with patch.object(ingestion, "_descargar_feed", return_value=FEED_XML), \
             patch.object(ingestion, "extraer_varios") as extraer:
            stats = ingestion.ingerir_medio(session, medio_extractor)

        extraer.assert_not_called()
        assert stats["nuevas"] == 1
        assert stats["extraidas"] == 0

        noticia = session.exec(
            select(Noticia).where(Noticia.medio_id == medio_extractor.id)
        ).one()
        assert noticia.contenido_limpio == "Cuerpo completo de la noticia."

    def test_una_extraccion_fallida_no_arrastra_a_las_demas(
        self, session: Session, medio_extractor: Medio
    ):
        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(3)
        ), patch.object(
            ingestion,
            "extraer_varios",
            return_value={
                "https://test.com/url-0": CUERPO_EXTRAIDO,
                "https://test.com/url-1": None,
                "https://test.com/url-2": CUERPO_EXTRAIDO,
            },
        ):
            stats = ingestion.ingerir_medio(session, medio_extractor)

        assert stats["nuevas"] == 2
        assert stats["extraidas"] == 2
        assert stats["extraccion_fallida"] == 1
        assert stats["errores"] == []

        urls = {
            n.url
            for n in session.exec(
                select(Noticia).where(Noticia.medio_id == medio_extractor.id)
            ).all()
        }
        assert urls == {"https://test.com/url-0", "https://test.com/url-2"}

    def test_solo_se_extraen_las_noticias_nuevas(
        self, session: Session, medio_extractor: Medio
    ):
        """
        El test del riesgo que hace viable toda la vía: la extracción va
        **después** de deduplicar. Si fuera antes, cada ciclo bajaría el feed
        entero —decenas de artículos, cada 15 minutos, para siempre— en vez de
        los pocos realmente nuevos.
        """
        for i in (0, 1):
            session.add(Noticia(
                medio_id=medio_extractor.id,
                titulo=f"Noticia url-{i}",
                url=f"https://test.com/url-{i}",
                guid=f"guid-url-{i}",
                contenido_limpio="Ya estaba en la base.",
                fecha_publicacion=datetime.utcnow(),
            ))
        session.commit()

        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(3)
        ), patch.object(
            ingestion,
            "extraer_varios",
            return_value={"https://test.com/url-2": CUERPO_EXTRAIDO},
        ) as extraer:
            stats = ingestion.ingerir_medio(session, medio_extractor)

        extraer.assert_called_once_with(["https://test.com/url-2"])
        assert stats["duplicadas"] == 2
        assert stats["nuevas"] == 1

    def test_no_hay_transaccion_abierta_durante_la_extraccion(
        self, session: Session, medio_extractor: Medio
    ):
        """
        El invariante que motivó partir `_procesar_items` en tres fases.

        El `SELECT` de deduplicación abre una transacción. Extraer con ella
        abierta la dejaría viva durante toda la descarga de los artículos
        —decenas de segundos en la primera corrida de un medio—, reteniendo una
        conexión del pool y frenando el vacuum de Postgres sobre ese snapshot.
        """
        transaccion_abierta = []

        def espiar(urls):
            transaccion_abierta.append(session.in_transaction())
            return {url: CUERPO_EXTRAIDO for url in urls}

        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(2)
        ), patch.object(ingestion, "extraer_varios", side_effect=espiar):
            ingestion.ingerir_medio(session, medio_extractor)

        assert transaccion_abierta == [False]

    def test_si_fallan_todas_las_extracciones_avisa(
        self, session: Session, medio_extractor: Medio
    ):
        """
        Que fallen todas es la firma de un rediseño del maquetado del medio. Sin
        aviso, eso es perder un medio entero en silencio.
        """
        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(3)
        ), patch.object(
            ingestion,
            "extraer_varios",
            return_value={f"https://test.com/url-{i}": None for i in range(3)},
        ), patch.object(ingestion.alerts, "enviar_alerta") as alerta:
            stats = ingestion.ingerir_medio(session, medio_extractor)

        assert stats["nuevas"] == 0
        assert stats["extraccion_fallida"] == 3
        assert alerta.call_count == 1
        assert alerta.call_args.kwargs["clave"] == "extraccion:Medio Sin Cuerpo"

    def test_una_sola_fallida_no_avisa(self, session: Session, medio_extractor: Medio):
        """Un artículo que se cayó es normal; no es motivo de mail."""
        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(2)
        ), patch.object(
            ingestion,
            "extraer_varios",
            return_value={
                "https://test.com/url-0": CUERPO_EXTRAIDO,
                "https://test.com/url-1": None,
            },
        ), patch.object(ingestion.alerts, "enviar_alerta") as alerta:
            ingestion.ingerir_medio(session, medio_extractor)

        alerta.assert_not_called()

    def test_no_avisa_que_el_feed_no_trae_contenido(
        self, session: Session, medio_extractor: Medio, caplog
    ):
        """
        El warning de "ningún item tenía contenido completo" es correcto para un
        medio que declara traer el artículo en el feed. Para uno con extracción
        es el estado normal y permanente: dejarlo sería ruido cada 15 minutos,
        para siempre — el patrón que el proyecto ya corrigió dos veces.
        """
        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(2)
        ), patch.object(
            ingestion,
            "extraer_varios",
            return_value={f"https://test.com/url-{i}": CUERPO_EXTRAIDO for i in range(2)},
        ), caplog.at_level(logging.WARNING, logger="src.services.ingestion"):
            ingestion.ingerir_medio(session, medio_extractor)

        assert "contenido completo" not in caplog.text

    def test_no_escala_las_queries_con_los_items_del_feed(
        self, session: Session, medio_extractor: Medio
    ):
        """
        El guardia de N+1 de Fase 5 (`TestDeduplicacionNoEscala`) usa la fixture
        `medio`, que no tiene la bandera: cubre solo la vía vieja. Este es su
        equivalente para la vía con extracción.

        Mismo aislamiento que aquel: se precargan las mismas notas que trae el
        feed, así no hay inserts de por medio y la única diferencia entre
        "pocos" y "muchos" es cuántos items hay que chequear.
        """
        for prefijo, cantidad in (("pocos", 3), ("muchos", 40)):
            for i in range(cantidad):
                session.add(Noticia(
                    medio_id=medio_extractor.id,
                    titulo=f"Noticia {prefijo}-{i}",
                    url=f"https://test.com/{prefijo}-{i}",
                    guid=f"guid-{prefijo}-{i}",
                    contenido_limpio="Ya estaba en la base.",
                    fecha_publicacion=datetime.utcnow(),
                ))
        session.commit()

        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(3, "pocos")
        ), patch.object(ingestion, "extraer_varios") as extraer:
            with contar_queries(session) as pocos:
                ingestion.ingerir_medio(session, medio_extractor)

        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(40, "muchos")
        ):
            with contar_queries(session) as muchos:
                stats = ingestion.ingerir_medio(session, medio_extractor)

        assert stats["duplicadas"] == 40
        assert muchos["n"] == pocos["n"]
        # Nada que extraer: ya estaban todas. Es el mismo invariante que
        # `test_solo_se_extraen_las_noticias_nuevas`, visto desde el costo.
        extraer.assert_not_called()

    def test_con_notas_nuevas_solo_crecen_los_inserts(
        self, session: Session, medio_extractor: Medio
    ):
        """
        El caso anterior **no alcanza**: con todo duplicado, `_procesar_items`
        sale por el `return` temprano *antes* del commit intermedio, así que el
        camino nuevo ni se ejercita y el guardia sería falso.

        Acá las notas son todas nuevas, así que corren el commit intermedio y la
        extracción. Las queries tienen que crecer **exactamente** lo que crecen
        los inserts —uno por nota— y ni una más: cualquier exceso sería un
        `SELECT` por artículo, que es el N+1 volviendo por la puerta del commit
        que expira el `Medio`.
        """
        def extraidos(urls):
            return {url: CUERPO_EXTRAIDO for url in urls}

        # Las dos corridas tienen que arrancar del mismo estado. Sin esto, la
        # primera encuentra el `Medio` recién cargado por la fixture y la
        # segunda lo encuentra expirado por los commits de la primera, así que
        # paga un SELECT de refresco que la primera no pagó -- una diferencia
        # de costo fijo, O(1) por corrida, que nada tiene que ver con cuántos
        # artículos trae el feed. Expirarlo en las dos deja a la vista solo lo
        # que crece con los items, que es lo que este test mide.
        session.expire(medio_extractor)
        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(3, "pocas")
        ), patch.object(ingestion, "extraer_varios", side_effect=extraidos):
            with contar_queries(session) as pocos:
                stats_pocos = ingestion.ingerir_medio(session, medio_extractor)

        session.expire(medio_extractor)
        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(40, "muchas")
        ), patch.object(ingestion, "extraer_varios", side_effect=extraidos):
            with contar_queries(session) as muchos:
                stats_muchos = ingestion.ingerir_medio(session, medio_extractor)

        assert stats_pocos["nuevas"] == 3
        assert stats_muchos["nuevas"] == 40

        crecimiento_queries = muchos["n"] - pocos["n"]
        crecimiento_notas = stats_muchos["nuevas"] - stats_pocos["nuevas"]
        assert crecimiento_queries == crecimiento_notas

    def test_un_medio_sin_la_bandera_nunca_extrae(self, session: Session, medio: Medio):
        """
        Guarda de regresión: la vía nueva no puede filtrarse a los medios que no
        la declararon. Para ellos el camino tiene que ser exactamente el de
        antes.
        """
        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(3)
        ), patch.object(ingestion, "extraer_varios") as extraer:
            stats = ingestion.ingerir_medio(session, medio)

        extraer.assert_not_called()
        assert stats["sin_contenido"] == 3
        assert stats["nuevas"] == 0


class TestUrlUtilizable:
    """
    La validación de la URL en el borde.

    `Noticia(...)` se instancia en un solo punto de todo `src/`, así que este es
    el único lugar por el que una URL entra al motor. Validarla acá cubre de una
    a `extraccion.permitido`, `topicos.topico_declarado`,
    `categorias.categoria_no_evento` y el payload que va al back-end.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "https://medio.test/nota",
            "http://medio.test/seccion/nota-con-guiones",
            "https://medio.test/nota?utm_source=rss&x=1",
            "https://medio.test/nota#fragmento",
            # Percent-encoding legítimo en la ruta: no toca el dominio.
            "https://medio.test/nota%20con%20espacios",
        ],
    )
    def test_acepta_las_urls_que_los_medios_emiten(self, url):
        assert ingestion.url_utilizable(url) is True

    @pytest.mark.parametrize(
        "url, motivo",
        [
            ("http://[::1/nota", "IPv6 sin cerrar: urlparse levanta ValueError"),
            # Pasa el primer urlparse (netloc='medio.test%5B') y revienta al
            # decodificarla, que es lo que hace can_fetch puertas adentro.
            ("https://medio.test%5B/nota", "corchete percent-encoded en el dominio"),
            ("/nota/relativa", "relativa: no se puede pedir ni entregar"),
            ("medio.test/nota", "sin esquema"),
            ("", "vacía"),
        ],
    )
    def test_descarta_las_que_no_sirven(self, url, motivo):
        assert ingestion.url_utilizable(url) is False, motivo

    def test_el_item_con_url_invalida_no_llega_a_la_base(
        self, session: Session, medio: Medio
    ):
        """
        Lo que importa río abajo: el item se descarta y **se cuenta aparte**.
        Mezclarlo con `sin_contenido` falsearía el aviso de "ningún item traía
        contenido", que compara ese contador contra el total del feed.
        """
        feed = _feed_con_url_invalida()
        with patch.object(ingestion, "_descargar_feed", return_value=feed):
            stats = ingestion.ingerir_medio(session, medio)

        assert stats["url_invalida"] == 1
        assert stats["sin_contenido"] == 0
        assert stats["nuevas"] == 1

        urls = [
            n.url
            for n in session.exec(
                select(Noticia).where(Noticia.medio_id == medio.id)
            ).all()
        ]
        assert urls == ["https://test.com/nota-buena"]


def _feed_con_url_invalida() -> str:
    """Un feed con una nota sana y otra con el `<link>` malformado."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
<item>
  <title>Nota con URL sana</title>
  <link>https://test.com/nota-buena</link>
  <guid>guid-buena</guid>
  <pubDate>Thu, 06 Aug 2026 10:00:00 GMT</pubDate>
  <content:encoded><![CDATA[<p>Cuerpo de la nota sana.</p>]]></content:encoded>
</item>
<item>
  <title>Nota con URL rota</title>
  <link>http://[::1/nota-rota</link>
  <guid>guid-rota</guid>
  <pubDate>Thu, 06 Aug 2026 11:00:00 GMT</pubDate>
  <content:encoded><![CDATA[<p>Cuerpo de la nota rota.</p>]]></content:encoded>
</item>
</channel>
</rss>"""


def _respuesta_robots(texto: str = "User-agent: *\nAllow: /\n") -> httpx.Response:
    """Lo que devuelve el `httpx.get` del `robots.txt` en `extraccion`."""
    return httpx.Response(
        status_code=200, text=texto, request=httpx.Request("GET", "https://test.com/robots.txt")
    )


class TestVidaUtilDelRobotsTxt:
    """
    Cuánto dura un permiso leído.

    La caché de `robots.txt` es un diccionario de módulo, así que sobrevive a
    la corrida que la llenó: con el scheduler embebido, semanas. Por eso
    `ingerir_todos_los_medios` la vacía al arrancar y la ata a la corrida.

    Se prueba por **comportamiento y no por `assert mock.called`**: se envenena
    la caché igual que lo haría una corrida anterior y se verifica que el
    artículo se extraiga de todas formas. Sacar el vaciado hace fallar los dos.
    """

    def test_una_corrida_no_hereda_el_robots_cacheado_por_la_anterior(
        self, session: Session, medio_extractor: Medio
    ):
        """
        Un `None` cacheado es el rastro de una corrida donde no se pudo leer el
        `robots.txt`. Si sobrevive, el medio deja de extraerse hasta el próximo
        reinicio del proceso **y encima deja de avisar**: la caché corta antes
        del `except` que manda el mail, así que sale un solo aviso y nunca más.
        """
        extraccion._robots["https://test.com"] = None

        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(1)
        ), patch.object(
            extraccion.httpx, "get", return_value=_respuesta_robots()
        ), patch.object(
            extraccion, "_descargar_pagina", return_value="<html></html>"
        ), patch.object(
            extraccion.trafilatura, "extract", return_value=CUERPO_EXTRAIDO
        ):
            ingestion.ingerir_todos_los_medios(session)

        noticia = session.exec(
            select(Noticia).where(Noticia.medio_id == medio_extractor.id)
        ).one()
        # `.strip()` porque el cuerpo pasa por `normalizar` en el camino real.
        assert noticia.contenido_limpio == CUERPO_EXTRAIDO.strip()

    def test_un_disallow_nuevo_se_respeta_en_la_corrida_siguiente(
        self, session: Session, medio_extractor: Medio
    ):
        """
        El lado que importa para el permiso, no para la cobertura: si el medio
        agrega un `Disallow` después de que lo leímos, la caché vieja nos
        dejaría extrayendo con una autorización vencida. `extraer_por_url`
        marca los medios donde vamos a buscar lo que no publicaron en el feed,
        así que releer el permiso es parte del trato.
        """
        permisivo = urllib.robotparser.RobotFileParser()
        permisivo.parse(["User-agent: *", "Allow: /"])
        extraccion._robots["https://test.com"] = permisivo

        with patch.object(
            ingestion, "_descargar_feed", return_value=_feed_sin_cuerpo(1)
        ), patch.object(
            extraccion.httpx,
            "get",
            return_value=_respuesta_robots("User-agent: *\nDisallow: /\n"),
        ), patch.object(
            extraccion, "_descargar_pagina", return_value="<html></html>"
        ), patch.object(
            extraccion.trafilatura, "extract", return_value=CUERPO_EXTRAIDO
        ):
            stats = ingestion.ingerir_todos_los_medios(session)

        assert stats[0]["extraidas"] == 0
        assert stats[0]["nuevas"] == 0
        assert not session.exec(
            select(Noticia).where(Noticia.medio_id == medio_extractor.id)
        ).all()
