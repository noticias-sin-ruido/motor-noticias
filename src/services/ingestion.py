"""
Pipeline de ingesta de noticias: descarga los feeds RSS de los medios activos,
limpia el contenido, filtra notas "en vivo", deduplica por guid y persiste
las noticias nuevas. Ver CLAUDE.md, sección Fase 2, para las decisiones de
diseño detrás de cada paso.
"""
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from typing import Optional

import feedparser
import httpx
from bs4 import BeautifulSoup
from sqlmodel import Session, select
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import settings
from ..models import Medio, Noticia

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

    fecha_publicacion = datetime.utcnow()
    if entry.get("published_parsed"):
        fecha_publicacion = datetime(*entry.published_parsed[:6])

    return {
        "titulo": titulo,
        "url": link,
        "guid": guid,
        "contenido_limpio": contenido_limpio,
        "fecha_publicacion": fecha_publicacion,
    }


def enviar_alerta(medio: Medio, error: Exception) -> None:
    """Envía un mail de alerta cuando se agotan los reintentos de un medio."""
    if not settings.SMTP_HOST or not settings.ALERT_EMAIL_TO:
        logger.error(
            f"No se pudo enviar alerta por mail (SMTP no configurado) -- "
            f"fallo en ingesta de {medio.nombre}: {error}"
        )
        return

    mensaje = EmailMessage()
    mensaje["Subject"] = f"[Sin Ruido] Fallo de ingesta: {medio.nombre}"
    mensaje["From"] = settings.SMTP_USER or settings.ALERT_EMAIL_TO
    mensaje["To"] = settings.ALERT_EMAIL_TO
    mensaje.set_content(
        f"No se pudo ingerir el feed de {medio.nombre} ({medio.feed_rss}) "
        f"tras agotar los reintentos.\n\nError: {error}"
    )

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
            smtp.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(mensaje)
    except Exception as smtp_error:
        logger.error(f"No se pudo enviar el mail de alerta para {medio.nombre}: {smtp_error}")


def ingerir_medio(session: Session, medio: Medio) -> dict:
    """Descarga, filtra, deduplica y persiste las noticias nuevas de un medio."""
    stats = {
        "medio": medio.nombre,
        "nuevas": 0,
        "duplicadas": 0,
        "en_vivo": 0,
        "sin_contenido": 0,
        "error": None,
    }

    try:
        feed_xml = _descargar_feed(medio.feed_rss)
    except httpx.HTTPError as error:
        logger.error(f"Fallo al descargar el feed de {medio.nombre} tras reintentos: {error}")
        enviar_alerta(medio, error)
        stats["error"] = str(error)
        return stats

    feed = feedparser.parse(feed_xml)
    if feed.bozo:
        logger.warning(f"Aviso al parsear el feed de {medio.nombre}: {feed.bozo_exception}")

    for entry in feed.entries:
        datos = _parsear_entry(entry)
        if datos is None:
            # Item sin content:encoded (u otro campo esencial) -- se descarta.
            # Puede pasar con contenido no periodístico servido en el mismo feed
            # (ej. horóscopos, cables de agencia) que no trae cuerpo completo.
            stats["sin_contenido"] += 1
            continue

        if es_en_vivo(datos["titulo"]):
            stats["en_vivo"] += 1
            continue

        ya_existe = session.exec(select(Noticia).where(Noticia.guid == datos["guid"])).first()
        if ya_existe:
            stats["duplicadas"] += 1
            continue

        session.add(Noticia(medio_id=medio.id, **datos))
        stats["nuevas"] += 1

    session.commit()

    if feed.entries and stats["sin_contenido"] == len(feed.entries):
        logger.warning(
            f"{medio.nombre}: ningún item de este ciclo tenía contenido completo "
            f"({len(feed.entries)} items en la ventana del feed)."
        )

    return stats


def ingerir_todos_los_medios(session: Session) -> list[dict]:
    """Corre el pipeline de ingesta para todos los medios activos."""
    medios_activos = session.exec(select(Medio).where(Medio.activo.is_(True))).all()
    return [ingerir_medio(session, medio) for medio in medios_activos]
