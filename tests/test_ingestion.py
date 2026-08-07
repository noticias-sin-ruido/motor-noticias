"""
Tests del servicio de ingesta: parseo de feed, limpieza de HTML, filtro de
notas en vivo, deduplicación por guid y manejo de fallos de red.
"""
from unittest.mock import patch

import httpx
import pytest
from sqlmodel import Session, select

from src.models import Medio, Noticia
from src.services import ingestion

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


@pytest.fixture
def medio(session: Session) -> Medio:
    m = Medio(nombre="Medio Test", url_base="https://test.com", feed_rss="https://test.com/rss")
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


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
        assert stats["error"] is None

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

        assert stats["error"] is not None
        assert stats["nuevas"] == 0
        mock_alerta.assert_called_once()


class TestIngerirTodosLosMedios:
    def test_solo_procesa_medios_activos(self, session: Session):
        activo = Medio(
            nombre="Activo", url_base="https://a.com", feed_rss="https://a.com/rss", activo=True
        )
        inactivo = Medio(
            nombre="Inactivo",
            url_base="https://i.com",
            feed_rss="https://i.com/rss",
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
