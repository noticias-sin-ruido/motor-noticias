"""
Segunda vía de ingesta: extrae el cuerpo de una nota desde su propia página.

Existe para los medios cuyo RSS trae solo un copete de ~200 caracteres en vez
del artículo completo — Clarín y Perfil, 0 de 438 items con `content:encoded`
verificado el 18/08/2026. Ver specs/change_logs.md, "Backlog punto 1".

El contrato con el resto del motor es deliberadamente angosto: se entrega un
string con el mismo formato que produce `ingestion.limpiar_html`, o `None`.
Eso hace que río abajo —vectorización, clustering, NER, TF-IDF, prompt de
síntesis— **nada pueda distinguir de qué vía vino el texto**, que es lo que
permite sumar esta sin tocar ninguna de esas etapas.
"""
import logging
import re
import time
import urllib.robotparser
from typing import Dict, Optional, Sequence
from urllib.parse import urlparse

import httpx
import trafilatura

from ..config import settings
from . import alerts

logger = logging.getLogger(__name__)

# Nos identificamos igual que el lector de feeds y por el mismo motivo (ver
# `ingestion.USER_AGENT`), pero declarando lo que esto realmente hace: pedir
# páginas de artículo, no feeds. A propósito NO imitamos un navegador.
USER_AGENT = (
    "SinRuido/1.0 (+https://github.com/noticias-sin-ruido/motor-noticias) article-fetcher"
)

# `robots.txt` ya consultados en esta corrida, por dominio. Sin esto se pediría
# el archivo una vez por artículo: con el feed de Clarín son 10 requests extra
# por ciclo para releer siempre lo mismo.
_robots: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}

_ESPACIOS = re.compile(r"\s+")


def limpiar_cache_robots() -> None:
    """Vacía la caché de `robots.txt`. Para los tests y para forzar una relectura."""
    _robots.clear()


def normalizar(texto: str) -> str:
    """
    Colapsa todo espacio en blanco —saltos incluidos— a un solo espacio.

    **No es cosmético.** `trafilatura` devuelve el artículo en párrafos
    separados por saltos de línea, y `limpiar_html` devuelve una sola línea
    (usa `separator=" ", strip=True`). Dejar esa diferencia rompe cosas que no
    avisan:

    - El prompt de síntesis arma bloques `--- NOTA n | medio` y los une con
      saltos de línea. Hoy ese delimitador es inequívoco *solo porque* ningún
      cuerpo contiene `\\n`. Con párrafos adentro deja de serlo, y un cuerpo
      que traiga una línea `--- NOTA` inyecta estructura en el prompt.
    - El embedding se calcula sobre los primeros `EMBEDDING_CHARS_CUERPO`
      caracteres: los saltos gastan lugar de esa ventana.

    Normalizando acá, en el origen, las dos vías quedan intercambiables y no
    hay que tocar `synthesis.py`, `vectorization.py` ni `preprocessing.py`.
    """
    return _ESPACIOS.sub(" ", texto).strip()


def _parser_robots(base: str) -> Optional[urllib.robotparser.RobotFileParser]:
    """
    `robots.txt` del dominio, cacheado. `None` si no se pudo leer.

    Se baja con httpx y NUESTRO User-Agent, y recién después se parsea el
    texto. `RobotFileParser.read()` haría la descarga con `urllib`, que manda
    `Python-urllib/3.x`: tanto Clarín como Perfil le devuelven **403 al propio
    robots.txt**, y como por spec un 403 significa "disallow all", el parser
    concluye —correctamente, con la información que tiene— que está todo
    prohibido. Es un falso negativo que ya nos pasó durante la medición: dio
    20/20 rutas rechazadas en los dos medios sin haber leído una sola regla.
    """
    if base in _robots:
        return _robots[base]

    parser: Optional[urllib.robotparser.RobotFileParser] = urllib.robotparser.RobotFileParser()
    try:
        respuesta = httpx.get(
            f"{base}/robots.txt",
            timeout=settings.EXTRACCION_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        respuesta.raise_for_status()
        parser.parse(respuesta.text.splitlines())
    except Exception as error:
        # Falla cerrado y RUIDOSO. Cerrado porque no sabemos qué permite el
        # medio; ruidoso porque el silencio acá significa perder la cobertura
        # de un medio entero sin que nadie se entere hasta mirar los números.
        logger.error(f"No se pudo leer {base}/robots.txt: {error}")
        alerts.enviar_alerta(
            asunto=f"[Sin Ruido] No se pudo leer robots.txt de {base}",
            cuerpo=(
                f"No se pudo leer {base}/robots.txt ({type(error).__name__}: {error}).\n\n"
                "Mientras tanto NO se extraen artículos de ese medio: sus noticias "
                "quedan sin cuerpo y no entran al pipeline."
            ),
            # Por dominio: un medio caído no tiene por qué inundar el mail.
            clave=f"robots:{base}",
        )
        parser = None

    _robots[base] = parser
    return parser


def permitido(url: str) -> bool:
    """
    Si el `robots.txt` del medio habilita pedir esa URL.

    **Nunca levanta.** Es una guarda, y una guarda que no puede responder tiene
    que decir que no: quien la llama corre dentro del `try` de `ingerir_feed`,
    donde una excepción cuesta el rollback del feed entero.

    Hay dos formas de romperla, y las dos son con una URL malformada. La
    evidente es `urlparse`, que levanta `ValueError` ante un IPv6 mal cerrado.
    La otra es menos visible: `can_fetch` hace `urlparse(unquote(url))` puertas
    adentro, así que una URL percent-encoded puede pasar el primer parseo y
    reventar en el segundo. La ingesta ya descarta las dos formas en el borde
    (`ingestion.url_utilizable`); esto es la segunda línea, para cuando a esta
    función la llame algo que no venga de un feed.
    """
    try:
        partes = urlparse(url)
        if not partes.scheme or not partes.netloc:
            logger.warning(f"URL sin esquema o dominio, no se extrae: {url!r}")
            return False

        parser = _parser_robots(f"{partes.scheme}://{partes.netloc}")
        if parser is None:
            return False
        return parser.can_fetch(USER_AGENT, url)
    except Exception as error:
        # El mensaje NO afirma que la URL esté malformada, aunque sea la causa
        # conocida: si un refactor mete un TypeError acá, un log que declare la
        # causa equivocada manda a quien depure por el camino de al lado.
        logger.warning(
            f"No se pudo evaluar el permiso de {url!r}, no se extrae "
            f"({type(error).__name__}: {error})"
        )
        return False


def _descargar_pagina(url: str) -> str:
    """
    HTML de la página del artículo.

    Política de red propia y más corta que la de los feeds: `_descargar_feed`
    tolera hasta ~20 s de backoff, que multiplicado por artículo empujaría la
    ingesta contra el ciclo de 15 minutos del scheduler.
    """
    respuesta = httpx.get(
        url,
        timeout=settings.EXTRACCION_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    respuesta.raise_for_status()
    return respuesta.text


def extraer_articulo(url: str) -> Optional[str]:
    """
    Cuerpo del artículo listo para persistir, o `None` si no se pudo.

    **Nunca propaga excepciones.** Es un requisito, no una comodidad: quien la
    llama es `ingestion._procesar_items`, que corre dentro del `try` de
    `ingerir_feed`. Un `httpx.HTTPError` que se escapara de acá caería en ese
    `except`, y el resultado sería `session.rollback()` —perdiendo todas las
    noticias del feed—, un mail de alerta y un `feeds_fallados += 1`. O sea:
    **un artículo caído se contabilizaría y se alertaría como un feed entero
    caído.**

    Devuelve el texto ya normalizado (ver `normalizar`) y NO devuelve la URL
    final tras los redirects: la que se persiste tiene que seguir siendo la del
    feed, porque de ella dependen la deduplicación por `url` —con su índice
    único—, `categoria_no_evento`, `topico_declarado` y el payload al back-end.
    """
    if not permitido(url):
        logger.info(f"robots.txt no habilita la extracción de {url}")
        return None

    html: Optional[str] = None
    for intento in range(settings.EXTRACCION_REINTENTOS + 1):
        try:
            html = _descargar_pagina(url)
            break
        except Exception as error:
            logger.warning(
                f"No se pudo bajar {url} (intento {intento + 1}): "
                f"{type(error).__name__}: {error}"
            )
            if intento < settings.EXTRACCION_REINTENTOS:
                time.sleep(1)

    if html is None:
        return None

    try:
        # Los parámetros van EXPLÍCITOS, incluso los que hoy coinciden con el
        # default. La lección de este bug no fue el valor de uno sino que un
        # default decidiera qué guardamos sin que nadie lo eligiera: `extract`
        # traía `include_comments=True`, así que los comentarios de lectores
        # podían entrar al cuerpo y persistirse como si fueran la nota. Escrito
        # así, una versión nueva de trafilatura no puede cambiarlo en silencio.
        #
        # `include_tables=True` se mantiene a propósito: en una nota de
        # economía o de elecciones la tabla es contenido, no maquetado.
        #
        # `favor_precision` queda en su default (False). Medido sobre 10
        # artículos reales de Perfil: recorta un 2,3% del texto, y como los
        # comentarios no estaban ahí, lo que saca es contenido o boilerplate
        # que no sabemos distinguir. Riesgo sin beneficio demostrado.
        crudo = trafilatura.extract(html, include_comments=False, include_tables=True)
    except Exception as error:
        logger.warning(f"trafilatura falló sobre {url}: {type(error).__name__}: {error}")
        return None

    if not crudo:
        logger.warning(f"trafilatura no encontró artículo en {url}")
        return None

    texto = normalizar(crudo)

    # El piso es la defensa contra el modo de falla propio de esta vía: si el
    # medio rediseña su maquetado, `trafilatura` no tira error -- devuelve un
    # menú, un aviso de cookies o un muro de suscripción que *parece*
    # contenido. Sin este corte eso entraría a la base como si fuera la nota,
    # y contaminaría embeddings, clustering y prompts en silencio.
    if len(texto) < settings.EXTRACCION_MIN_CARACTERES:
        logger.warning(
            f"Lo extraído de {url} tiene {len(texto)} caracteres, por debajo del "
            f"piso de {settings.EXTRACCION_MIN_CARACTERES}: se descarta"
        )
        return None

    return texto


def extraer_varios(urls: Sequence[str]) -> Dict[str, Optional[str]]:
    """
    Extrae varias notas espaciando los pedidos, y devuelve `{url: texto|None}`.

    La pausa vive acá y no en `extraer_articulo` porque es una propiedad del
    ritmo con que se le pide a un medio, no de traer una nota: una llamada
    suelta no tiene a quién esperar. Va **entre** artículos y no después del
    último, para no sumarle un segundo muerto a cada ciclo del scheduler.

    Se devuelven también los `None` en vez de filtrarlos: quien llama necesita
    saber cuántas fallaron para contabilizarlas.

    **Un artículo no puede llevarse el lote.** `extraer_articulo` promete no
    propagar excepciones, pero hasta ahora esa promesa se sostenía por
    vigilancia —cada operación riesgosa con su propio `try`— y ya se comprobó
    que a una se le puede pasar: `permitido` levantaba ante una URL malformada.
    El `try` de acá la vuelve estructural, con el mismo criterio con el que
    `ingerir_feed` aísla un feed de los demás.

    Se usa `logger.exception` y no `warning` a propósito: el riesgo de una red
    así es tapar un bug propio —un `TypeError` de un refactor se leería como
    "no se pudo extraer"—, y el traceback completo en el log es lo que evita
    que sea silencioso.
    """
    resultados: Dict[str, Optional[str]] = {}
    for numero, url in enumerate(urls):
        if numero:
            time.sleep(settings.EXTRACCION_PAUSA_SEGUNDOS)
        try:
            resultados[url] = extraer_articulo(url)
        except Exception as error:
            logger.exception(
                f"Excepción inesperada extrayendo {url}, se cuenta como fallida "
                f"({type(error).__name__}: {error})"
            )
            resultados[url] = None
    return resultados
