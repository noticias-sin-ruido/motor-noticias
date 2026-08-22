"""
Pipeline de ingesta de noticias: descarga los feeds RSS de los medios activos,
limpia el contenido, filtra notas "en vivo", deduplica por guid y persiste
las noticias nuevas. Ver CLAUDE.md, sección Fase 2, para las decisiones de
diseño detrás de cada paso.
"""
import logging
from datetime import datetime
from typing import Optional, Set, Tuple
from urllib.parse import unquote, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup
from sqlmodel import Session, select
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..tiempo import ahora_utc
from ..models import Medio, Noticia
from . import alerts
from .extraccion import extraer_varios, limpiar_cache_robots

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


def _parsear_entry(
    entry: feedparser.FeedParserDict, permitir_sin_cuerpo: bool = False
) -> Optional[dict]:
    """
    Extrae los campos necesarios de un item de feedparser. None si falta algo esencial.

    Con `permitir_sin_cuerpo` —los medios con `extraer_por_url`— un item sin
    `content:encoded` ya no se descarta acá: vuelve con `contenido_limpio`
    vacío para que `_completar_cuerpos` lo busque en la página del artículo.

    Ese vacío es un **sentinela de vida corta**: se llena o se descarta antes de
    llegar al modelo, donde `contenido_limpio` es `NOT NULL`.
    """
    titulo = entry.get("title")
    link = entry.get("link")
    guid = entry.get("id") or link
    content_list = entry.get("content")

    # Sin título, link o guid no hay nada que hacer ni siquiera con extracción
    # de por medio: son los que permiten deduplicar y saber adónde ir a buscar.
    if not (titulo and link and guid):
        return None

    contenido_limpio = ""
    if content_list:
        contenido_limpio = limpiar_html(content_list[0].get("value", ""))

    if not contenido_limpio and not permitir_sin_cuerpo:
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


def url_utilizable(url: str) -> bool:
    """
    Si la URL sirve para todo lo que el motor va a hacer después con ella.

    Se valida **una sola vez y acá**, porque acá es el único lugar por donde una
    URL entra: `Noticia(...)` se instancia en un solo punto de todo `src/`. Río
    abajo la usan `extraccion.permitido`, `topicos.topico_declarado` y
    `categorias.categoria_no_evento`, ninguno de los cuales la valida ni tiene
    por qué hacerlo, y además **viaja al back-end en el payload**: una URL rota
    es salida rota se extraiga o no. Defender cada consumidor por separado es la
    vigilancia que ya nos falló una vez -- ver change_logs.md, "URLs malformadas".

    El criterio es el mínimo para que la URL sirva de algo. Medido contra las
    4.532 noticias reales de la base: **cero rechazos**, así que no descarta
    nada de lo que hoy funciona.

    Una URL relativa (`/nota/123`, que emiten algunos feeds descuidados) sí se
    descarta, y es lo correcto: no se puede pedir ni entregar. Resolverla contra
    `Medio.url_base` sería una funcionalidad aparte, no un arreglo.
    """
    try:
        partes = urlparse(url)
        # `unquote` antes de parsear es lo que hace `RobotFileParser.can_fetch`,
        # y no es una rareza suya: cualquiera que decodifique la URL pasa por
        # acá. Una URL puede sobrevivir al parseo directo y romperse decodificada
        # -- `https://x.com%5B/nota` es válida hasta que el `%5B` vuelve a ser
        # `[` y el dominio queda con un corchete sin cerrar.
        urlparse(unquote(url))
    except ValueError as error:
        logger.warning(f"URL malformada, se descarta el item: {url!r} ({error})")
        return False

    if not partes.scheme or not partes.netloc:
        logger.warning(f"URL sin esquema o dominio, se descarta el item: {url!r}")
        return False

    return True


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


def _seleccionar_nuevas(
    session: Session,
    medio_nombre: str,
    usa_extraccion: bool,
    feed_url: str,
    feed_xml: str,
    stats: dict,
) -> list:
    """
    Items del feed que todavía no están en la base, filtrados y deduplicados.

    Corre **antes** de cualquier extracción, y eso no es un detalle de orden:
    extraer antes de deduplicar significaría bajar las decenas de artículos del
    feed entero cada 15 minutos para siempre, en vez de los pocos realmente
    nuevos de cada ciclo.
    """
    feed = feedparser.parse(feed_xml)
    if feed.bozo:
        logger.warning(f"Aviso al parsear {feed_url}: {feed.bozo_exception}")

    candidatos = []
    sin_contenido_aca = 0
    for entry in feed.entries:
        datos = _parsear_entry(entry, permitir_sin_cuerpo=usa_extraccion)
        if datos is None:
            # Item sin content:encoded (u otro campo esencial) -- se descarta.
            # Puede pasar con contenido no periodístico servido en el mismo feed
            # (ej. horóscopos, cables de agencia) que no trae cuerpo completo.
            stats["sin_contenido"] += 1
            sin_contenido_aca += 1
            continue

        # Contador propio y no `sin_contenido`: son descartes por motivos
        # distintos, y mezclarlos además falsearía el aviso de más abajo, que
        # compara `sin_contenido_aca` contra el total de items del feed.
        if not url_utilizable(datos["url"]):
            stats["url_invalida"] += 1
            continue

        if es_en_vivo(datos["titulo"]):
            stats["en_vivo"] += 1
            continue

        candidatos.append(datos)

    # Solo tiene sentido avisar para un medio que declara traer el artículo en
    # el feed. Para uno con `extraer_por_url` este es el estado normal y
    # permanente —Clarín y Perfil no traen `content:encoded` en ningún item— y
    # el aviso sería ruido cada 15 minutos, para siempre. Su equivalente allá
    # es que fallen todas las extracciones, y lo cubre `_completar_cuerpos`.
    if not usa_extraccion and feed.entries and sin_contenido_aca == len(feed.entries):
        logger.warning(
            f"{medio_nombre}: ningún item de {feed_url} tenía contenido completo "
            f"({len(feed.entries)} items en la ventana del feed)."
        )

    if not candidatos:
        return []

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
    nuevas = []

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

        nuevas.append(datos)
        vistos_guid.add(datos["guid"])
        vistos_url.add(datos["url"])

    return nuevas


def _completar_cuerpos(nuevas: list, medio_nombre: str, stats: dict) -> list:
    """
    Busca en la página del artículo el cuerpo de las notas que el feed no trajo.

    Se llama **fuera de la transacción** y solo con notas que ya se sabe que son
    nuevas. Una nota que no se pueda extraer **se descarta**: `contenido_limpio`
    es `NOT NULL` y no hay nada que persistir. El feed la vuelve a ofrecer en el
    ciclo siguiente mientras siga en su ventana, así que se reintenta sola.
    """
    faltantes = [d["url"] for d in nuevas if not d["contenido_limpio"]]
    if not faltantes:
        return nuevas

    cuerpos = extraer_varios(faltantes)

    completas = []
    extraidas_aca = 0
    fallidas_aca = 0
    for datos in nuevas:
        if datos["contenido_limpio"]:
            # El feed ya lo traía: `extraer_por_url` es "si falta, buscalo en la
            # URL", no "buscalo siempre". Ahorra un request y una pausa.
            completas.append(datos)
            continue

        cuerpo = cuerpos.get(datos["url"])
        if not cuerpo:
            stats["extraccion_fallida"] += 1
            fallidas_aca += 1
            continue

        datos["contenido_limpio"] = cuerpo
        stats["extraidas"] += 1
        extraidas_aca += 1
        completas.append(datos)

    # Que falle una nota suelta es normal —un timeout, un artículo que se cayó—.
    # Que fallen TODAS es la firma de un rediseño del maquetado del medio, y sin
    # aviso eso es perder la cobertura de un medio entero en silencio hasta que
    # alguien mire los números. Mismo criterio que el `robots.txt` ilegible.
    if fallidas_aca and not extraidas_aca:
        logger.error(
            f"{medio_nombre}: fallaron las {fallidas_aca} extracciones del feed"
        )
        alerts.enviar_alerta(
            asunto=f"[Sin Ruido] No se extrajo ningún artículo de {medio_nombre}",
            cuerpo=(
                f"Fallaron las {fallidas_aca} extracciones de {medio_nombre} en "
                "este feed. Si se repite, lo más probable es que el medio haya "
                "rediseñado su maquetado y `trafilatura` ya no encuentre el "
                "artículo.\n\nMientras tanto sus noticias no entran al pipeline."
            ),
            # Por medio: sus feeds fallan juntos y el problema es uno solo.
            clave=f"extraccion:{medio_nombre}",
        )

    return completas


def _procesar_items(
    session: Session, medio: Medio, feed_url: str, feed_xml: str, stats: dict
) -> None:
    """
    Resuelve qué es nuevo, completa los cuerpos que falten y agrega a la sesión.

    No commitea el resultado —eso es de `ingerir_feed`— pero para los medios con
    extracción **sí cierra la transacción de lectura en el medio**, a propósito.
    """
    # Se leen los atributos del medio ANTES del commit intermedio: después
    # quedan expirados y cada acceso dispararía un SELECT de refresco.
    medio_id, medio_nombre = medio.id, medio.nombre
    usa_extraccion = medio.extraer_por_url

    a_insertar = _seleccionar_nuevas(
        session, medio_nombre, usa_extraccion, feed_url, feed_xml, stats
    )
    if not a_insertar:
        return

    if usa_extraccion:
        # El SELECT de `_existentes` abrió una transacción. Extraer con ella
        # abierta la dejaría viva durante toda la descarga de los artículos
        # —~40 s en la primera corrida de un medio—, reteniendo una conexión
        # del pool y frenando el vacuum de Postgres sobre ese snapshot. El
        # commit acá es de solo lectura: no hay nada que perder si algo falla
        # después.
        session.commit()
        a_insertar = _completar_cuerpos(a_insertar, medio_nombre, stats)

    for datos in a_insertar:
        # Guarda del invariante, no decoración: `contenido_limpio` es NOT NULL,
        # y un vacío que se escape hasta acá revienta en el INSERT -- y con él,
        # por el rollback de `ingerir_feed`, todo lo que el feed ya había traído.
        if not datos["contenido_limpio"]:
            logger.error(f"Se descarta {datos['url']}: quedó sin cuerpo")
            continue

        session.add(Noticia(medio_id=medio_id, **datos))
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
        # Items cuyo `<link>` no sirve para nada de lo que viene después. Es
        # su propio contador porque un feed que empiece a emitirlos es un
        # problema del feed, no falta de contenido -- ver `url_utilizable`.
        "url_invalida": 0,
        # Solo se mueven en los medios con `extraer_por_url`. Ahí `sin_contenido`
        # deja de contar los items sin `content:encoded` —son candidatos, no
        # descartes— y su señal pasa a vivir en estas dos.
        "extraidas": 0,
        "extraccion_fallida": 0,
        # Lista y no un solo string: con varios feeds, guardar únicamente el
        # último error hacía que la caída de uno se leyera igual que la del
        # medio entero en la respuesta de `/ingest`.
        "errores": [],
    }

    for feed_url in medio.feeds_rss:
        ingerir_feed(session, medio, feed_url, stats)

    return stats


def ingerir_todos_los_medios(session: Session) -> list[dict]:
    """
    Corre el pipeline de ingesta para todos los medios activos.

    **Arranca vaciando la caché de `robots.txt`**, y eso no es limpieza: es lo
    que le fija la vida útil a un permiso leído. La caché existe para no pedir
    el archivo una vez por artículo, pero es un diccionario de módulo, así que
    sin este vaciado dura lo que dure el proceso —con el scheduler embebido,
    semanas—. Dos consecuencias, las dos malas:

    - Un fallo de red puntual queda cacheado como `None` para siempre y el
      medio deja de extraerse hasta el próximo reinicio, en silencio: el
      `except` que avisa ni se vuelve a ejecutar, porque la caché corta antes.
    - Si el medio agrega un `Disallow` mañana, no nos enteramos nunca. Seguir
      extrayendo con un permiso leído hace semanas es justo lo que este motor
      no puede hacer, porque `extraer_por_url` marca los medios donde vamos a
      buscar lo que el medio no publicó en su feed.

    Vaciarla acá ata el permiso a la corrida: se relee una vez por medio y por
    ciclo. **Ese es el costo por medio que hay que tener presente al sumar
    medios** — ver specs/tech_stack.md, "Puntos de quiebre".
    """
    limpiar_cache_robots()

    medios_activos = session.exec(select(Medio).where(Medio.activo.is_(True))).all()
    return [ingerir_medio(session, medio) for medio in medios_activos]
