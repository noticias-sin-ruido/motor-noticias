"""
Dar de alta un medio, y comprobar su feed **antes** de aceptarlo.

Ver specs/roadmap.md, backlog punto 3, y `models/medio.py`.

**El alta no es un CRUD, y ese es el punto entero.** Hasta la 1.1.0 el roster
venía hardcodeado en `scripts/seed_medios.py`, o sea que el repo aceptaba los
términos de uso de siete medios argentinos en nombre de quien lo desplegara. Esa
decisión es del operador, y para tomarla necesita saber qué hay del otro lado:
la revisión del 19-20/08 mostró que los términos varían muchísimo entre medios
—Clarín licencia solo títulos y links, Perfil pide links de vuelta, La Izquierda
Diario reserva TDM en su `robots.txt`— y que cuál es aceptable depende del uso.

Por eso el sondeo convierte el alta de *"registrá esto"* en *"esto es lo que
encontramos, decidí vos"*.

**Qué bloquea y qué informa.** No es lo mismo un feed que no sirve que un feed
que no te gusta:

- **Bloquea** lo que no admite discusión: la URL no es alcanzable, el feed no
  responde, no parsea, o no trae un solo item utilizable. El
  `/feed/internacionales` de Perfil da 404 aunque Perfil lo publique en su
  propia página de RSS — eso no es una decisión de nadie, es un error.
- **Informa** lo que es criterio del operador: que el feed no traiga el cuerpo
  de las notas, que el `robots.txt` sea restrictivo, que la ventana temporal
  parezca archivo y no cobertura fresca.
"""
import ipaddress
import logging
from datetime import datetime
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

from ..config import settings

# `_parsear_entry` es privado de `ingestion` y aun así se importa, a propósito:
# **es la definición de qué cuenta como un item utilizable**, y el sondeo tiene
# que contar exactamente lo mismo que después va a ingerir el pipeline. Una
# segunda copia del criterio acá sería la clase de duplicado que se desincroniza
# sin que nadie lo note, y haría que el alta prometiera items que la ingesta
# después descarta.
from .extraccion import USER_AGENT as USER_AGENT_ARTICULOS, leer_robots
from .ingestion import USER_AGENT, _parsear_entry, url_utilizable

# Resolver un host a sus IPs es genérico, no tiene nada de proveedores de IA.
# Se importa en vez de copiarse por lo mismo que `models/tipos.py` centraliza
# `JSONVariant`: dos implementaciones de la misma comprobación de seguridad se
# arreglan una sola vez y queda la otra viva.
from .proveedores.base import _resolver

logger = logging.getLogger(__name__)

# **Política de red propia, y más corta que la de los feeds.** `_descargar_feed`
# reintenta 3 veces con backoff exponencial hasta 10 s: son casi 20 s por feed,
# y acá hay una persona esperando la respuesta de un alta. Es el mismo criterio
# que ya tomó `extraccion._descargar_pagina` frente a la misma tentación.
TIMEOUT_SONDEO_SEGUNDOS = 10.0

# A partir de qué antigüedad la ventana del feed huele a archivo y no a
# cobertura fresca.
#
# El número sale de lo medido en la Fase 2 y en la etapa 5: los feeds generales
# son ventanas móviles cortas —La Nación 7 h, Perfil 7,1 h, TN 23 h— mientras
# que los de sección guardan meses hacia atrás. Tres días deja pasar cualquier
# general holgadamente y marca lo que evidentemente no lo es.
#
# Es un **aviso y no un rechazo**: un medio chico que publica dos notas por
# semana tiene una ventana ancha y es perfectamente legítimo.
HORAS_QUE_HUELEN_A_ARCHIVO = 72.0

# Rangos que un feed no puede alcanzar nunca.
#
# **Acá sí se bloquean las direcciones privadas, y es la diferencia deliberada
# con `proveedores.base.REDES_PROHIBIDAS`, que solo bloquea link-local.** Aquel
# comentario explica por qué: un modelo de IA en `localhost:11434` o un vLLM en
# la red interna es justamente el caso que ese backlog existe para habilitar, y
# es además el escenario donde los cuerpos de los artículos no salen de la
# máquina.
#
# Ese razonamiento no se traslada. Un medio de noticias en `127.0.0.1` o en
# `10.0.0.5` no tiene ningún uso legítimo, y sin este bloqueo el endpoint sería
# un escáner de la red interna a pedido de quien llame: el motor pide la URL, y
# aunque no devuelva el cuerpo, la diferencia entre "no responde" y "responde
# pero no es un feed" ya dice si hay algo escuchando ahí.
#
# No es hermético —entre esta comprobación y el request hay una segunda
# resolución de DNS, así que un dominio que cambie de respuesta se lo saltea—
# pero cierra el caso directo. Ver specs/roadmap.md, punto 3, "Seguridad".
REDES_PROHIBIDAS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    # NAT64: encapsula una IPv4 y `is_global` lo da por global.
    ipaddress.ip_network("64:ff9b::/96"),
)


class FeedInservible(Exception):
    """
    El feed no sirve, y no es una cuestión de criterio.

    La levanta todo lo que bloquea el alta. Se distingue de un aviso —que viaja
    en la respuesta y no impide guardar— porque acá no hay nada que el operador
    pueda decidir: una URL que no resuelve o un XML que no parsea no se vuelven
    aceptables porque alguien esté dispuesto a aceptarlos.
    """


def _direcciones_efectivas(direccion):
    """
    La dirección, más la IPv4 que pueda venir escondida adentro.

    **Sin esto el filtro se evade con una sola línea.** `::ffff:127.0.0.1` es
    una IPv4 mapeada en IPv6: apunta a loopback, pero como objeto es un
    `IPv6Address`, y `IPv6Address in IPv4Network("127.0.0.0/8")` da `False`. O
    sea que pasaba el filtro entero.

    No era teoría: en Windows no conecta y parecía inofensivo, pero verificado
    dentro de `python:3.12-slim` —que es el destino real de despliegue— **pasa
    el filtro y conecta**. Un `Location: http://[::ffff:127.0.0.1]/` alcanzaba
    para atravesar la validación por salto.

    Se desenvuelven las tres formas que `ipaddress` sabe reconocer: mapeada,
    6to4 (`2002::/16`) y Teredo (`2001::/32`), que encapsulan una IPv4 cada una
    a su manera.

    **Segunda capa, y conviene decirlo:** hoy `is_global` ya rechaza las tres
    por su cuenta, asi que romper esta funcion no reabre el agujero — la prueba
    de mutacion lo confirmo. Se mantiene igual porque `ipaddress` **ya cambio
    de semantica una vez y nos mordio**: en Python 3.13 `is_private` dejo de
    cubrir el CGNAT `100.64/10`, que era justo lo que la primera version de
    este filtro daba por cubierto. Depender de una sola propiedad de la libreria
    estandar para una defensa de seguridad es la apuesta que ya perdimos.
    """
    yield direccion
    for atributo in ("ipv4_mapped", "sixtofour"):
        adentro = getattr(direccion, atributo, None)
        if adentro is not None:
            yield adentro
    teredo = getattr(direccion, "teredo", None)
    if teredo is not None:
        # Devuelve (servidor, cliente): los dos son IPv4 y los dos importan.
        yield from teredo


def _esta_prohibida(direccion) -> bool:
    """
    Si esta dirección es un destino al que un feed no puede apuntar.

    La regla de fondo es **`is_global`, y en positivo**: un medio de noticias
    vive en una dirección ruteable en internet, punto. Enumerar rangos malos es
    una carrera que se pierde —la primera versión de esta función dejaba pasar
    `100.64.0.1`, el espacio CGNAT, porque en Python 3.13 `is_private` da
    `False` ahí—; preguntar por lo que SÍ está permitido no tiene ese problema.
    Comprobado contra los 5 medios del roster: todos resuelven a direcciones
    globales, varios por IPv6.

    `REDES_PROHIBIDAS` se conserva igual y se comprueba primero, por dos
    motivos: es la lista **auditable** —se lee y se entiende qué se bloquea y
    por qué, que es la mitad del valor de una defensa— y cubre `64:ff9b::/96`
    (NAT64), que `is_global` considera global aunque encapsule una IPv4.
    """
    if any(direccion in red for red in REDES_PROHIBIDAS):
        return True
    return not direccion.is_global


def _validar_forma_de_url(url: str, que_es: str):
    """
    Las tres comprobaciones que NO tocan la red: esquema, dominio y credenciales.

    Devuelve `(limpia, partes)`. Se separó de `validar_url_de_feed` cuando entró
    `validar_url_de_logo`, que necesita exactamente estas tres y ninguna más —
    y la alternativa era una segunda copia de la lista de esquemas peligrosos,
    justo la clase de duplicado que se desincroniza el día que haya que agregar
    uno.

    `que_es` es cómo se nombra el campo en el mensaje de error ("El feed", "El
    logo"), porque un error que no dice qué campo revisar obliga a adivinar.
    """
    limpia = (url or "").strip()
    if not limpia:
        raise FeedInservible(f"{que_es} no puede estar vacío.")

    try:
        partes = urlparse(limpia)
    except ValueError as error:
        raise FeedInservible(f"URL malformada: {limpia[:120]!r} ({error})") from error

    if partes.scheme not in ("http", "https"):
        # Sin esto entran `file://`, `gopher://` y demás, que son la vía clásica
        # para convertir un SSRF en lectura de archivos de la máquina — y, en el
        # logo, `javascript:`, que no es SSRF sino XSS almacenado esperando a
        # que una interfaz lo ponga en un `href`.
        raise FeedInservible(
            f"{que_es} tiene que ser http o https, y vino "
            f"{partes.scheme or '(nada)'!r}: {limpia[:120]!r}"
        )
    if not partes.hostname:
        raise FeedInservible(f"{que_es} no tiene dominio: {limpia[:120]!r}")
    if partes.username or partes.password:
        raise FeedInservible(
            f"{que_es} no puede llevar credenciales embebidas "
            f"(`https://usuario:clave@host`)."
        )
    return limpia, partes


def validar_url_de_logo(url: str) -> str:
    """
    La URL del logo, o levanta. **Comprueba la forma y no la red, a propósito.**

    Era la única URL del medio que no pasaba por ningún validador: `url_base` y
    los feeds ya iban a `validar_url_de_feed` dentro del sondeo, y el logo no
    iba a ninguna parte porque el motor nunca lo pide. Justamente por eso hacía
    falta — es la que una interfaz va a poner adentro de un atributo de HTML.
    `javascript:alert(...)` guardado en `logo_url` y devuelto tal cual por
    `GET /medios` es XSS almacenado, y el que lo dispara es el visor, no el
    motor. Verificado antes del arreglo: se guardaba y se devolvía intacto.

    **No se resuelve el host ni se comprueba `REDES_PROHIBIDAS`**, y la
    diferencia con `validar_url_de_feed` es deliberada: el motor nunca baja esta
    URL —`models/medio.py` la define como "la URL, no los bytes" y ningún paso
    del pipeline la mira—, así que no hay SSRF que cerrar. Quien la va a pedir
    es el navegador de quien mire la interfaz, y a esa altura la dirección
    privada que alcanza es la SUYA, no la del motor. Resolver DNS acá sería
    pagar una consulta de red por cada alta para defender algo que no existe, y
    encima rompería un logo servido desde la intranet de quien despliega esto.
    """
    limpia, _ = _validar_forma_de_url(url, "El logo del medio")
    return limpia


def validar_url_de_feed(url: str) -> str:
    """
    La URL del feed normalizada, o levanta explicando qué tiene de malo.

    Adaptación de `proveedores.base.validar_base_url` — **revisada, no copiada**,
    porque el destino es distinto: allá es un endpoint de API con forma conocida
    y acá es cualquier sitio web. Tres de las cuatro reglas se conservan —hoy en
    `_validar_forma_de_url`, compartidas con el logo—; la que cambia es el juego
    de redes prohibidas, y el motivo está en `REDES_PROHIBIDAS`.
    """
    limpia, partes = _validar_forma_de_url(url, "La URL del feed")

    for direccion in _resolver(partes.hostname):
        for efectiva in _direcciones_efectivas(direccion):
            if _esta_prohibida(efectiva):
                comose = "" if efectiva == direccion else f" (via {direccion})"
                raise FeedInservible(
                    f"El feed apunta a una dirección privada o local "
                    f"({efectiva}){comose}, que no es donde vive un medio de "
                    f"noticias. Ver `REDES_PROHIBIDAS` en services/medios.py."
                )

    return limpia


# Cuántos redirects se siguen antes de rendirse.
#
# httpx por su cuenta permite 20. Cinco alcanza de sobra y el número está
# medido: de los 8 feeds del roster **3 redirigen y ninguno da más de un
# salto** (TN y El Cronista migraron a Arc y mantienen viva la URL vieja;
# `gente.com.ar` manda a `revistagente.com`). Un feed que necesite más de
# cinco está mal configurado, y el tope es además lo que corta un bucle que
# no repita URLs exactas.
def host_normalizado(url: str) -> str:
    """
    El host de una URL, en minúsculas y sin el `www.` de adelante.

    `www.gente.com.ar` y `gente.com.ar` son el mismo lugar y tratarlos como
    distintos sólo produciría una advertencia que nadie merece.
    """
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def hosts_ajenos(urls_nuevas: Sequence[str], urls_conocidas: Sequence[str]) -> List[str]:
    """
    De `urls_nuevas`, los hosts que no aparecen en `urls_conocidas`.

    **Es la guarda contra apuntar un medio a contenido que no es suyo**, y el
    daño que evita no es técnico sino de atribución: si a "La Nación" se le
    cambia el feed por el de otra redacción, las síntesis dicen que La Nación
    publicó algo que publicó otro, **firmado**, y el back-end lo recibe como
    legítimo. Es la misma familia que el destino de entrega — redirigir el
    producto, no filtrar una credencial.

    **Compara hosts y no dominios registrables, y es una decisión medida.** La
    regla natural sería "el feed tiene que vivir en el dominio del medio", pero
    de los ocho medios cargados el 11/09/2026 **uno no la cumple**: Revista
    Gente declara `revistagente.com` y sirve su RSS desde `gente.com.ar`. Una
    regla dura habría rechazado un medio que funciona. Además, sin librería de
    sufijos públicos, `lanacion.com.ar` no se puede reducir a su dominio
    registrable sin adivinar.

    Por eso esto **no bloquea**: devuelve los hosts nuevos para que quien llama
    pida una confirmación explícita que los nombre. Un cambio de ruta en el
    mismo host pasa sin ruido; un host distinto —incluido un subdominio, que
    también puede ser de otro— se pregunta.
    """
    conocidos = {host_normalizado(u) for u in urls_conocidas}
    ajenos: List[str] = []
    for url in urls_nuevas:
        host = host_normalizado(url)
        if host and host not in conocidos and host not in ajenos:
            ajenos.append(host)
    return ajenos


MAX_SALTOS_REDIRECT = 5


def bajar_siguiendo_redirects(url: str, *, agente: str, timeout: float) -> str:
    """
    El cuerpo de `url`, siguiendo redirects **pero validando cada salto**.

    Existe porque `follow_redirects=True` de httpx es un agujero de SSRF con
    esta forma de uso: nosotros validamos la URL que nos mandan, y httpx
    después se va sola a donde diga el `Location`. Un host público que
    responda `302 -> http://169.254.169.254/latest/meta-data/` atraviesa el
    filtro entero. Está verificado, no es teórico: con `follow_redirects=True`
    el motor leyó el cuerpo de un servicio en `127.0.0.1` cuya URL directa el
    validador sí rechazaba.

    **Se sigue el redirect en vez de rechazarlo, y eso fue una decisión.**
    Rechazarlo era más simple y de superficie cero, pero está medido que
    habría rechazado 3 de los 7 medios en producción — y sobre todo: el
    redirect es **cómo los medios gestionan sus migraciones**. TN y El Cronista
    ya se mudaron a Arc y lo que mantiene viva la URL que tenemos sembrada es
    justamente ese 301. Fijar la URL final nos rompería en la mudanza
    siguiente, en silencio. Ver specs/change_logs.md.

    Lo que hace que esto sea seguro es que **cada URL que se va a pedir pasa
    por `validar_url_de_feed`, incluida la primera y todas las intermedias** —
    no solo la última—. Un `Location` que apunte a la red interna corta acá.

    **No cierra el TOCTOU**, y conviene decirlo: entre que se valida la IP y
    que httpx conecta hay una segunda resolución de DNS, así que un dominio que
    cambie de respuesta entre las dos se lo saltea. Es el mismo límite que
    `proveedores.base` ya asume y documenta; cerrarlo exige conectar a la IP ya
    validada preservando Host y SNI, que es artillería desproporcionada para un
    motor de un operador. Cierra el caso directo, no al atacante decidido.
    """
    visitadas: List[str] = []
    actual = url

    for _ in range(MAX_SALTOS_REDIRECT + 1):
        # La primera vuelta revalida lo que `_sondear_un_feed` ya validó, y es
        # a propósito: así la función es segura la llame quien la llame, en vez
        # de depender de que su llamador se haya acordado. Cuesta una consulta
        # de DNS que el sistema ya tiene cacheada.
        actual = validar_url_de_feed(actual)

        if actual in visitadas:
            raise FeedInservible(
                "El feed entra en un bucle de redirects: "
                + " -> ".join(visitadas + [actual])
            )
        visitadas.append(actual)

        try:
            respuesta = httpx.get(
                actual,
                timeout=timeout,
                follow_redirects=False,
                headers={"User-Agent": agente},
            )
        except httpx.InvalidURL as error:
            # **`InvalidURL` NO hereda de `HTTPError`** — es la única de httpx
            # que no lo hace, verificado recorriendo el MRO. Sin esta rama se
            # escapaba del `except` de abajo y salía como un 500: bastaba con
            # mandar una URL de feed de más de 64 KB.
            raise FeedInservible(
                f"{actual[:120]!r}... no es una URL que se pueda pedir ({error})"
            ) from error
        except httpx.HTTPError as error:
            raise FeedInservible(
                f"{actual} no responde ({type(error).__name__}: {error})"
            ) from error

        # **Se mira el header y no `respuesta.is_redirect`**: en httpx 0.28
        # `is_redirect` da `True` para cualquier 3xx, tenga `Location` o no, y
        # un 3xx sin `Location` no es un salto sino una respuesta rara que
        # corresponde tratar como error más abajo.
        destino = respuesta.headers.get("location")
        if not (300 <= respuesta.status_code < 400 and destino):
            try:
                respuesta.raise_for_status()
            except httpx.HTTPError as error:
                raise FeedInservible(
                    f"{actual} no responde ({type(error).__name__}: {error})"
                ) from error
            return respuesta.text

        # `urljoin` resuelve un `Location` relativo —`/feed/nuevo`, que es la
        # forma más común— contra la URL que acabamos de pedir. Sin esto, un
        # relativo llegaría al validador como una URL sin esquema ni dominio y
        # el feed se rechazaría por una causa inventada.
        actual = urljoin(actual, destino)

    raise FeedInservible(
        f"El feed encadena más de {MAX_SALTOS_REDIRECT} redirects: "
        + " -> ".join(visitadas)
    )


def _bajar(url: str) -> str:
    """El XML del feed. Levanta `FeedInservible` si no se pudo traer."""
    return bajar_siguiendo_redirects(
        url, agente=USER_AGENT, timeout=TIMEOUT_SONDEO_SEGUNDOS
    )


def _fecha_declarada(entry) -> Optional[datetime]:
    """
    La fecha que el item declara, o `None`.

    **No se usa la que devuelve `_parsear_entry`**, y la diferencia importa: esa
    cae en `ahora_utc()` cuando el item no trae fecha, que es lo correcto para
    persistir pero convertiría la ventana temporal en cero para un feed sin
    fechas — una medición inventada, justo la que este sondeo reporta.
    """
    if entry.get("published_parsed"):
        return datetime(*entry.published_parsed[:6])
    return None


def _sondear_un_feed(url: str) -> dict:
    """Lo que se puede averiguar de un feed sin ingerirlo. Ver `sondear`."""
    url = validar_url_de_feed(url)
    xml = _bajar(url)

    feed = feedparser.parse(xml)
    # `bozo` se prende ante cualquier rareza del XML, y feedparser igual suele
    # sacar los items adelante. Solo es fatal si además no hay nada que leer.
    if feed.bozo and not feed.entries:
        raise FeedInservible(
            f"{url} responde pero no se pudo leer como RSS o Atom "
            f"({type(feed.bozo_exception).__name__}: {feed.bozo_exception})"
        )

    items: List[dict] = []
    fechas: List[datetime] = []
    for entry in feed.entries:
        datos = _parsear_entry(entry, permitir_sin_cuerpo=True)
        if datos is None or not url_utilizable(datos["url"]):
            continue
        items.append(datos)
        declarada = _fecha_declarada(entry)
        if declarada is not None:
            fechas.append(declarada)

    if not items:
        raise FeedInservible(
            f"{url} responde y parsea, pero no trae un solo item utilizable "
            f"(hacen falta título, link y guid). Trajo {len(feed.entries)} entradas."
        )

    ventana = None
    if len(fechas) >= 2:
        ventana = round((max(fechas) - min(fechas)).total_seconds() / 3600, 1)

    canal = feed.feed
    return {
        "url": url,
        "items": len(items),
        "con_cuerpo": sum(1 for d in items if d["contenido_limpio"]),
        "ventana_horas": ventana,
        "idioma_declarado": (canal.get("language") or "").strip() or None,
        "logo_declarado": (canal.get("image") or {}).get("href") or None,
        "titulo_declarado": (canal.get("title") or "").strip() or None,
        # Se guarda una URL real del feed para preguntarle al `robots.txt` por
        # una nota concreta en vez de por la raíz del sitio: los medios suelen
        # permitir `/` y prohibir rutas puntuales.
        "_ejemplo": items[0]["url"],
    }


def _sondear_robots(url_base: str, url_de_ejemplo: Optional[str]) -> dict:
    """
    Qué dice el `robots.txt` del medio sobre bajarle una nota.

    **Nunca levanta**: que no se pueda leer es un dato para el operador, no un
    motivo para rechazar el alta. Recordar que esto solo importa si el medio va
    a entrar con `extraer_por_url`; para leer un feed que el medio publica no
    hace falta permiso de crawler.
    """
    base = (url_base or "").strip().rstrip("/")
    try:
        parser = leer_robots(base)
    except Exception as error:
        return {
            "legible": False,
            "permite_extraer": None,
            "crawl_delay": None,
            "detalle": (
                f"no se pudo leer {base}/robots.txt "
                f"({type(error).__name__}: {error})"
            ),
        }

    permite = None
    if url_de_ejemplo:
        try:
            permite = parser.can_fetch(USER_AGENT_ARTICULOS, url_de_ejemplo)
        except Exception as error:  # una URL rara puede reventar `can_fetch`
            logger.warning(f"No se pudo evaluar el robots.txt de {base}: {error}")

    try:
        demora = parser.crawl_delay(USER_AGENT_ARTICULOS)
    except Exception:
        demora = None

    return {
        "legible": True,
        "permite_extraer": permite,
        "crawl_delay": demora,
        "detalle": None,
    }


def sondear(url_base: str, feeds: Sequence[str]) -> Tuple[dict, List[str]]:
    """
    Comprueba los feeds de un medio y devuelve `(informe, avisos)`.

    Levanta `FeedInservible` ante lo que bloquea el alta. **Todos los feeds
    tienen que servir**, no alcanza con que sirva uno: la lista la manda el
    operador de forma explícita, así que uno roto es un error de tipeo que
    conviene ver ahora y no descubrir por un mail de alerta cada quince minutos.
    El mensaje dice cuál falló y por qué.

    Los avisos son lo que el operador tiene que decidir, no lo que impide seguir.
    """
    if not feeds:
        raise FeedInservible("Hace falta al menos un feed RSS.")

    # **Se sondea cada feed una sola vez.** Sin esto la lista se recorría tal
    # cual y una repetida costaba un pedido cada vez: medido antes del arreglo,
    # 500 copias de la misma URL en un solo POST daban 501 pedidos reales al
    # mismo servidor, con nuestro User-Agent puesto. `AltaMedio` ya deduplica lo
    # que entra por la API, así que esto cubre el otro camino: `PATCH` sondea la
    # lista **guardada**, que en una fila anterior a esta versión —o cargada por
    # `scripts/seed_medios.py`, que no deduplica— puede traer repetidas.
    #
    # `dict.fromkeys` y no `set` porque el orden importa: `informes[0]` es el
    # feed del que sale el artículo de ejemplo para probar el robots.txt, y con
    # un set sería uno cualquiera en cada corrida.
    feeds = list(dict.fromkeys(f.strip() for f in feeds))

    # **`url_base` pasa por el mismo validador que los feeds, y no es simetría
    # decorativa**: `_sondear_robots` le pide `{url_base}/robots.txt`, así que un
    # `url_base` apuntando a la red interna sería exactamente el SSRF que
    # `REDES_PROHIBIDAS` existe para cerrar, entrando por la puerta de al lado.
    validar_url_de_feed(url_base)

    informes = [_sondear_un_feed(u) for u in feeds]

    total_items = sum(i["items"] for i in informes)
    total_con_cuerpo = sum(i["con_cuerpo"] for i in informes)
    robots = _sondear_robots(url_base, informes[0]["_ejemplo"])

    avisos: List[str] = []

    if total_con_cuerpo == 0:
        avisos.append(
            f"Ninguno de los {total_items} items trae el cuerpo de la nota en el "
            f"feed (`content:encoded`): este medio publica solo el copete. Si "
            f"querés que el motor lo busque en la página del artículo, mandá "
            f"`extraer_por_url: true` — pero fijate antes sus términos de uso, "
            f"porque retener el cuerpo suele ser una decisión del medio y no un "
            f"descuido. Sin esa bandera, sus noticias entran sin texto y no "
            f"sirven para agrupar ni sintetizar."
        )
    elif total_con_cuerpo < total_items:
        avisos.append(
            f"Solo {total_con_cuerpo} de {total_items} items traen el cuerpo en "
            f"el feed. Los que no lo traigan se descartan en la ingesta salvo "
            f"que actives `extraer_por_url`."
        )

    for informe in informes:
        ventana = informe["ventana_horas"]
        if ventana is None:
            avisos.append(
                f"{informe['url']} no declara fecha en sus items, así que no se "
                f"pudo medir qué ventana cubre. La ingesta les va a poner la "
                f"hora en que las vio."
            )
        elif ventana > HORAS_QUE_HUELEN_A_ARCHIVO:
            avisos.append(
                f"{informe['url']} cubre {ventana} h entre su item más viejo y "
                f"el más nuevo: parece un archivo y no una ventana de cobertura "
                f"fresca. Los feeds generales que ya corren van de 7 a 23 h. Un "
                f"archivo trae notas viejas que casi nunca llegan a formar par."
            )

    if not robots["legible"]:
        avisos.append(
            f"No se pudo leer el robots.txt del medio: {robots['detalle']}. "
            f"Solo importa si vas a usar `extraer_por_url`: sin poder leerlo, el "
            f"motor falla cerrado y no extrae nada de este medio."
        )
    elif robots["permite_extraer"] is False:
        avisos.append(
            "El robots.txt del medio NO habilita bajar sus notas con nuestro "
            "User-Agent. El feed se puede leer igual, pero `extraer_por_url` no "
            "va a traer nada: el motor respeta el robots.txt."
        )

    if robots["legible"] and robots["crawl_delay"]:
        avisos.append(
            f"El robots.txt pide un crawl-delay de {robots['crawl_delay']} s y "
            f"el motor hoy pausa {settings.EXTRACCION_PAUSA_SEGUNDOS} s entre "
            f"artículos (`EXTRACCION_PAUSA_SEGUNDOS`). Si vas a extraer de este "
            f"medio, subilo para respetarlo."
        )

    # Lo que el canal declara de sí mismo, para que la interfaz lo ofrezca como
    # default. **Se propone, no se impone**: el tag `<language>` es opcional y
    # muchos feeds lo traen mal, así que el valor que se guarda es el que manda
    # el operador.
    sugerencias = {
        "idioma": next(
            (i["idioma_declarado"] for i in informes if i["idioma_declarado"]), None
        ),
        "logo_url": next(
            (i["logo_declarado"] for i in informes if i["logo_declarado"]), None
        ),
        "nombre": next(
            (i["titulo_declarado"] for i in informes if i["titulo_declarado"]), None
        ),
    }

    informe = {
        "feeds": [
            {k: v for k, v in i.items() if not k.startswith("_")} for i in informes
        ],
        "items_totales": total_items,
        "items_con_cuerpo": total_con_cuerpo,
        "robots": robots,
        "sugerencias": sugerencias,
    }
    return informe, avisos
