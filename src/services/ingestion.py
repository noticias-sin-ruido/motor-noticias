"""
Pipeline de ingesta de noticias: descarga los feeds RSS de los medios activos,
limpia el contenido, filtra notas "en vivo", deduplica por guid y persiste
las noticias nuevas. Ver CLAUDE.md, sección Fase 2, para las decisiones de
diseño detrás de cada paso.
"""
import logging
from datetime import datetime
from typing import Optional, Set, Tuple

import feedparser
import httpx
from bs4 import BeautifulSoup
from sqlmodel import Session, select
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import settings
from ..tiempo import ahora_utc
from ..models import Medio, Noticia
from . import alerts

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15

# Nos identificamos de forma explícita ante los medios en vez de mandar el
# User-Agent por defecto de httpx: es la práctica estándar para un lector de
# feeds, y algunos medios (ej. Paparazzi) rechazan con 403 a los clientes que
# no se identifican. A propósito NO imitamos un navegador -- ver specs/.
USER_AGENT = "SinRuido/1.0 (+https://github.com/noticias-sin-ruido/motor-noticias) feed-reader"

# Heurístico genérico para detectar notas "en vivo" / minuto a minuto por el
# título (case-insensitive). Confirmado empíricamente para La Nación ("en
# vivo:") y TN ("vivo" + emoji 🔴); se mantiene como red de seguridad para
# Clarín y El Cronista, donde no se encontraron indicios en la muestra
# revisada -- ver CLAUDE.md, Fase 2, "Noticias en vivo".
EN_VIVO_PALABRAS_CLAVE = ["vivo", "minuto a minuto", "en directo"]
EN_VIVO_EMOJI = "🔴"


def es_en_vivo(titulo: str) -> bool:
    """Heurístico para detectar coberturas en vivo / minuto a minuto por el título."""
    if EN_VIVO_EMOJI in titulo:
        return True
    titulo_lower = titulo.lower()
    return any(palabra in titulo_lower for palabra in EN_VIVO_PALABRAS_CLAVE)


def limpiar_html(html: str) -> str:
    """Convierte el HTML de `content:encoded` a texto plano."""
    return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
def _descargar_feed(feed_url: str) -> str:
    """Descarga el XML del feed, con reintentos y backoff exponencial."""
    response = httpx.get(
        feed_url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.text


def _parsear_entry(entry: feedparser.FeedParserDict) -> Optional[dict]:
    """Extrae los campos necesarios de un item de feedparser. None si falta algo esencial."""
    titulo = entry.get("title")
    link = entry.get("link")
    guid = entry.get("id") or link
    content_list = entry.get("content")

    if not (titulo and link and guid and content_list):
        return None

    contenido_limpio = limpiar_html(content_list[0].get("value", ""))
    if not contenido_limpio:
        return None

    fecha_publicacion = ahora_utc()
    if entry.get("published_parsed"):
        fecha_publicacion = datetime(*entry.published_parsed[:6])

    return {
        "titulo": titulo,
        "url": link,
        "guid": guid,
        "contenido_limpio": contenido_limpio,
        "fecha_publicacion": fecha_publicacion,
    }


def enviar_alerta(medio: Medio, feed_url: str, error: Exception) -> None:
    """Envía un mail de alerta cuando se agotan los reintentos de un feed."""
    alerts.enviar_alerta(
        asunto=f"[Sin Ruido] Fallo de ingesta: {medio.nombre}",
        cuerpo=(
            f"No se pudo ingerir un feed de {medio.nombre} ({feed_url}) "
            f"tras agotar los reintentos.\n\nError: {error}"
        ),
        # Por medio y no por feed: si a La Nación se le cae la infraestructura
        # de RSS, sus 8 secciones fallan juntas y el problema es uno solo.
        clave=f"ingesta:{medio.nombre}",
    )


def _existentes(session: Session, guids: Set[str], urls: Set[str]) -> Tuple[Set[str], Set[str]]:
    """
    Guids y urls de ese conjunto que ya están en la base.

    Una sola consulta para **todo el feed**, no una por item: antes `_ya_esta`
    corría un `SELECT` por cada entry, y con varios feeds por medio (el general
    y el de cada sección) eso eran cientos de queries por corrida, la mayoría
    para descubrir que la nota ya existía.

    Se miran **las dos** claves porque nada garantiza que dos feeds del mismo
    medio le pongan el mismo `guid` a la misma nota -- con solo mirar el guid,
    la segunda copia llegaría al `INSERT` y reventaría contra el índice único
    de `url`, tirando la ingesta entera del medio.

    Como cada feed se commitea antes de procesar el siguiente (`ingerir_feed`),
    esta consulta ya ve lo que trajeron los feeds anteriores del mismo medio
    sin depender de `autoflush`.
    """
    if not guids and not urls:
        return set(), set()

    filas = session.exec(
        select(Noticia.guid, Noticia.url).where(
            Noticia.guid.in_(guids) | Noticia.url.in_(urls)
        )
    ).all()
    return {g for g, _ in filas}, {u for _, u in filas}


def _registrar_fallo(stats: dict, feed_url: str, error: Exception) -> None:
    stats["feeds_fallados"] += 1
    stats["errores"].append(f"{feed_url}: {type(error).__name__}: {error}")


def ingerir_feed(session: Session, medio: Medio, feed_url: str, stats: dict) -> None:
    """
    Procesa un feed, lo commitea y acumula sobre `stats`.

    **Ninguna excepción sale de acá.** Un feed que falla no frena a los demás
    del mismo medio ni a los medios que siguen: son independientes entre sí, y
    que la infraestructura de RSS devuelva 500 en `economia` no es razón para
    perderse `politica`. Antes solo se aislaban los errores de red, y cualquier
    otro —un `IntegrityError` del autoflush por una corrida concurrente, un
    valor no-string en `feeds_rss`— abortaba la ingesta completa del ciclo.

    El commit es **por feed** y no por medio: si algo envenena la sesión, el
    `rollback` se lleva solo lo de este feed y no lo que ya trajeron los
    anteriores. La deduplicación entre feeds sigue funcionando porque lo
    commiteado ya es visible para la consulta del siguiente.
    """
    try:
        feed_xml = _descargar_feed(feed_url)
        _procesar_items(session, medio, feed_url, feed_xml, stats)
        session.commit()
    except httpx.HTTPError as error:
        # El único caso que además avisa por mail: que un medio no responda es
        # información operativa, no un bug nuestro.
        session.rollback()
        logger.error(f"Fallo al descargar {feed_url} tras reintentos: {error}")
        enviar_alerta(medio, feed_url, error)
        _registrar_fallo(stats, feed_url, error)
    except Exception as error:
        session.rollback()
        logger.exception(f"Fallo inesperado procesando {feed_url}")
        _registrar_fallo(stats, feed_url, error)


def _procesar_items(
    session: Session, medio: Medio, feed_url: str, feed_xml: str, stats: dict
) -> None:
    """Parsea el XML y agrega a la sesión las noticias nuevas. No commitea."""
    feed = feedparser.parse(feed_xml)
    if feed.bozo:
        logger.warning(f"Aviso al parsear {feed_url}: {feed.bozo_exception}")

    candidatos = []
    sin_contenido_aca = 0
    for entry in feed.entries:
        datos = _parsear_entry(entry)
        if datos is None:
            # Item sin content:encoded (u otro campo esencial) -- se descarta.
            # Puede pasar con contenido no periodístico servido en el mismo feed
            # (ej. horóscopos, cables de agencia) que no trae cuerpo completo.
            stats["sin_contenido"] += 1
            sin_contenido_aca += 1
            continue

        if es_en_vivo(datos["titulo"]):
            stats["en_vivo"] += 1
            continue

        candidatos.append(datos)

    if feed.entries and sin_contenido_aca == len(feed.entries):
        logger.warning(
            f"{medio.nombre}: ningún item de {feed_url} tenía contenido completo "
            f"({len(feed.entries)} items en la ventana del feed)."
        )

    if not candidatos:
        return

    guids_existentes, urls_existentes = _existentes(
        session,
        {d["guid"] for d in candidatos},
        {d["url"] for d in candidatos},
    )

    # Además de lo que ya está en la base, hay que descartar duplicados DENTRO
    # del propio feed (el mismo artículo listado dos veces): la consulta de
    # arriba es de una sola vez, así que no ve lo que se va agregando en este
    # mismo loop.
    vistos_guid: Set[str] = set()
    vistos_url: Set[str] = set()

    for datos in candidatos:
        ya_existia = (
            datos["guid"] in guids_existentes
            or datos["url"] in urls_existentes
            or datos["guid"] in vistos_guid
            or datos["url"] in vistos_url
        )
        if ya_existia:
            stats["duplicadas"] += 1
            continue

        session.add(Noticia(medio_id=medio.id, **datos))
        vistos_guid.add(datos["guid"])
        vistos_url.add(datos["url"])
        stats["nuevas"] += 1


def ingerir_medio(session: Session, medio: Medio) -> dict:
    """
    Descarga, filtra, deduplica y persiste las noticias nuevas de un medio.

    Recorre **todos** los feeds del medio. El general de un diario grande es una
    selección de portada, no todo lo que publica: ver `models/medio.py`.
    """
    stats = {
        "medio": medio.nombre,
        "feeds": len(medio.feeds_rss),
        "feeds_fallados": 0,
        "nuevas": 0,
        "duplicadas": 0,
        "en_vivo": 0,
        "sin_contenido": 0,
        # Lista y no un solo string: con varios feeds, guardar únicamente el
        # último error hacía que la caída de uno se leyera igual que la del
        # medio entero en la respuesta de `/ingest`.
        "errores": [],
    }

    for feed_url in medio.feeds_rss:
        ingerir_feed(session, medio, feed_url, stats)

    return stats


def ingerir_todos_los_medios(session: Session) -> list[dict]:
    """Corre el pipeline de ingesta para todos los medios activos."""
    medios_activos = session.exec(select(Medio).where(Medio.activo.is_(True))).all()
    return [ingerir_medio(session, medio) for medio in medios_activos]
