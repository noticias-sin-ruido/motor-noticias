"""
Tests del alta de medios por el operador (backlog punto 3).

Se mockea `medios.httpx.get` —la frontera con la red para los feeds— y
`medios.leer_robots`, con el mismo patrón que `test_extraccion.py` y
`test_ingestion.py`. Ningún test sale a internet.
"""
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from src.models import Medio
from src.services import medios
from src.main import MAX_FEEDS_POR_MEDIO, MAX_LARGO_URL
from src.services.medios import (
    FeedInservible,
    sondear,
    validar_url_de_feed,
    validar_url_de_logo,
)


# --------------------------------------------------------------------------
# Andamios
# --------------------------------------------------------------------------

CABECERA = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss version="2.0" '
    'xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>'
)


def _item(n: int, fecha: str = "Wed, 02 Sep 2026 12:00:00 -0300", cuerpo: bool = True) -> str:
    encoded = (
        f"<content:encoded><![CDATA[<p>Cuerpo largo de la nota {n}.</p>]]></content:encoded>"
        if cuerpo
        else ""
    )
    return (
        f"<item><title>Titulo {n}</title>"
        f"<link>https://medio.test/nota-{n}</link>"
        f"<guid>guid-{n}</guid>"
        f"<pubDate>{fecha}</pubDate>{encoded}</item>"
    )


def _feed(
    items: str = "",
    idioma: str = "es",
    logo: str = "https://medio.test/logo.png",
    titulo: str = "Medio Test",
) -> str:
    canal = f"<title>{titulo}</title>"
    if idioma:
        canal += f"<language>{idioma}</language>"
    if logo:
        canal += f"<image><url>{logo}</url><title>{titulo}</title>"
        canal += "<link>https://medio.test</link></image>"
    return CABECERA + canal + items + "</channel></rss>"


FEED_SANO = _feed(_item(1) + _item(2) + _item(3))
FEED_SIN_CUERPO = _feed(_item(1, cuerpo=False) + _item(2, cuerpo=False))


def _respuesta(texto: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        text=texto,
        request=httpx.Request("GET", "https://medio.test/feed"),
    )


class _RobotsFalso:
    """Lo único que el sondeo le pide a un `RobotFileParser`."""

    def __init__(self, permite=True, demora=None):
        self._permite = permite
        self._demora = demora

    def can_fetch(self, agente, url):
        return self._permite

    def crawl_delay(self, agente):
        return self._demora


@pytest.fixture
def red(request):
    """
    Mockea las dos salidas a la red del sondeo: el feed y el `robots.txt`.

    Por default sirve un feed sano y un robots permisivo. Los tests que quieren
    otra cosa parchean encima con su propio `patch.object`.
    """
    with patch.object(
        medios.httpx, "get", return_value=_respuesta(FEED_SANO)
    ) as get, patch.object(
        medios, "leer_robots", return_value=_RobotsFalso()
    ) as robots:
        yield get, robots


ALTA = {
    "nombre": "Medio Test",
    "url_base": "https://medio.test",
    "feeds_rss": ["https://medio.test/feed"],
}


# --------------------------------------------------------------------------
# La validación de destino (SSRF)
# --------------------------------------------------------------------------


class TestValidarUrlDeFeed:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://medio.test/feed",
            "gopher://medio.test/feed",
            "",
            "   ",
            "https://",
            "https://usuario:clave@medio.test/feed",
        ],
    )
    def test_rechaza_lo_que_no_es_un_feed_alcanzable(self, url):
        with pytest.raises(FeedInservible):
            validar_url_de_feed(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8000/feed",
            "http://localhost/feed",
            "http://10.0.0.5/feed",
            "http://192.168.1.10/feed",
            "http://172.16.0.1/feed",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/feed",
        ],
    )
    def test_rechaza_la_red_interna(self, url):
        """
        El endpoint hace que el motor pida una URL elegida por quien llama. Sin
        esto sería un escáner de la red interna: aunque el cuerpo no se devuelva,
        la diferencia entre "no responde" y "responde pero no es un feed" ya
        delata qué hay escuchando.
        """
        with pytest.raises(FeedInservible, match="privada o local"):
            validar_url_de_feed(url)

    def test_acepta_un_medio_de_verdad(self):
        assert validar_url_de_feed("  https://www.perfil.com/feed  ") == (
            "https://www.perfil.com/feed"
        )

    def test_es_mas_estricto_que_el_de_proveedores_y_a_proposito(self):
        """
        **Fija la divergencia deliberada.** `proveedores.base.validar_base_url`
        permite `localhost` porque un modelo de IA local es justamente el caso
        que ese backlog existe para habilitar. Un medio de noticias en localhost
        no tiene uso legítimo, así que acá se bloquea.

        Si alguien "unifica" los dos validadores, este test se cae y explica por
        qué no hay que hacerlo.
        """
        from src.services.proveedores.base import validar_base_url

        assert validar_base_url("http://localhost:11434") == "http://localhost:11434"
        with pytest.raises(FeedInservible):
            validar_url_de_feed("http://localhost:11434/feed")


# --------------------------------------------------------------------------
# El sondeo
# --------------------------------------------------------------------------


class TestSondeo:
    def test_informa_lo_que_encontro(self, red):
        informe, avisos = sondear("https://medio.test", ["https://medio.test/feed"])

        assert informe["items_totales"] == 3
        assert informe["items_con_cuerpo"] == 3
        assert informe["feeds"][0]["items"] == 3
        assert informe["robots"]["legible"] is True
        assert avisos == []

    def test_propone_idioma_logo_y_nombre_del_canal(self, red):
        """
        El canal RSS los declara, así que el sondeo los ofrece como default. Se
        **proponen**, no se imponen: el tag es opcional y muchos feeds lo traen
        mal, por eso el valor que se guarda es el que manda el operador.
        """
        informe, _ = sondear("https://medio.test", ["https://medio.test/feed"])

        assert informe["sugerencias"]["idioma"] == "es"
        assert informe["sugerencias"]["logo_url"] == "https://medio.test/logo.png"
        assert informe["sugerencias"]["nombre"] == "Medio Test"

    def test_un_feed_que_no_responde_bloquea(self, red):
        get, _ = red
        get.side_effect = httpx.ConnectError("sin ruta al host")

        with pytest.raises(FeedInservible, match="no responde"):
            sondear("https://medio.test", ["https://medio.test/feed"])

    def test_un_404_bloquea(self, red):
        """El caso de `/feed/internacionales` de Perfil, que el medio publica y da 404."""
        get, _ = red
        get.return_value = _respuesta("<html>404</html>", status=404)

        with pytest.raises(FeedInservible, match="no responde"):
            sondear("https://medio.test", ["https://medio.test/feed"])

    def test_lo_que_no_es_rss_bloquea(self, red):
        get, _ = red
        get.return_value = _respuesta("esto no es xml ni de casualidad")

        with pytest.raises(FeedInservible, match="no se pudo leer como RSS"):
            sondear("https://medio.test", ["https://medio.test/feed"])

    def test_un_feed_sin_items_utilizables_bloquea(self, red):
        """Un feed bien formado pero cuyos items no tienen link ni guid."""
        get, _ = red
        get.return_value = _respuesta(
            _feed("<item><title>Sin link ni guid</title></item>")
        )

        with pytest.raises(FeedInservible, match="no trae un solo item utilizable"):
            sondear("https://medio.test", ["https://medio.test/feed"])

    def test_un_feed_vacio_bloquea(self, red):
        get, _ = red
        get.return_value = _respuesta(_feed())

        with pytest.raises(FeedInservible, match="no trae un solo item utilizable"):
            sondear("https://medio.test", ["https://medio.test/feed"])

    def test_todos_los_feeds_tienen_que_servir(self, red):
        """
        No alcanza con que sirva uno: la lista la manda el operador de forma
        explícita, así que uno roto es un error de tipeo que conviene ver ahora y
        no descubrir por un mail de alerta cada quince minutos.
        """
        get, _ = red
        get.side_effect = [_respuesta(FEED_SANO), _respuesta("", status=500)]

        with pytest.raises(FeedInservible, match="no responde"):
            sondear(
                "https://medio.test",
                ["https://medio.test/feed", "https://medio.test/roto"],
            )

    def test_un_feed_sin_cuerpo_avisa_pero_no_bloquea(self, red):
        """
        El caso de Clarín y Perfil: 0 de 438 items con `content:encoded`. Que el
        medio retenga el cuerpo es una decisión suya, y qué hacer al respecto es
        criterio del operador — no algo que el motor pueda dictaminar.
        """
        get, _ = red
        get.return_value = _respuesta(FEED_SIN_CUERPO)

        informe, avisos = sondear("https://medio.test", ["https://medio.test/feed"])

        assert informe["items_con_cuerpo"] == 0
        assert any(a.startswith("Ninguno de los 2 items") for a in avisos)

    def test_una_ventana_de_archivo_avisa(self, red):
        get, _ = red
        get.return_value = _respuesta(
            _feed(
                _item(1, fecha="Wed, 02 Sep 2026 12:00:00 -0300")
                + _item(2, fecha="Mon, 05 Jan 2026 12:00:00 -0300")
            )
        )

        _, avisos = sondear("https://medio.test", ["https://medio.test/feed"])

        assert any("archivo" in a for a in avisos)

    def test_un_feed_sin_fechas_avisa_y_no_inventa_la_ventana(self, red):
        """
        `_parsear_entry` cae en `ahora_utc()` cuando el item no trae fecha, que
        es lo correcto para persistir pero daría una ventana de 0 h. El sondeo
        mide sobre la fecha declarada justamente para no reportar una medición
        que nadie hizo.
        """
        get, _ = red
        get.return_value = _respuesta(
            _feed(_item(1, fecha="") + _item(2, fecha=""))
        )

        informe, avisos = sondear("https://medio.test", ["https://medio.test/feed"])

        assert informe["feeds"][0]["ventana_horas"] is None
        assert any("no declara fecha" in a for a in avisos)

    def test_un_robots_ilegible_avisa_pero_no_bloquea(self, red):
        _, robots = red
        robots.side_effect = httpx.ConnectError("no responde")

        informe, avisos = sondear("https://medio.test", ["https://medio.test/feed"])

        assert informe["robots"]["legible"] is False
        assert any("robots.txt" in a for a in avisos)

    def test_un_robots_que_prohibe_avisa_pero_no_bloquea(self, red):
        _, robots = red
        robots.return_value = _RobotsFalso(permite=False)

        _, avisos = sondear("https://medio.test", ["https://medio.test/feed"])

        assert any("NO habilita" in a for a in avisos)

    def test_el_crawl_delay_se_informa(self, red):
        _, robots = red
        robots.return_value = _RobotsFalso(demora=10)

        _, avisos = sondear("https://medio.test", ["https://medio.test/feed"])

        assert any("crawl-delay" in a for a in avisos)

    def test_sondear_no_manda_mail_de_alerta(self, red):
        """
        La trampa que motivó separar `leer_robots` de `_parser_robots`: esta
        última avisa por mail cuando no puede leer un `robots.txt`, lo cual está
        bien para la ingesta —perder un medio entero es grave— pero convertiría
        cada sondeo de un dominio caído en un mail al operador. Peor: cualquiera
        con acceso al endpoint podría usarlo para inundarle la casilla.
        """
        from src.services import extraccion

        with patch.object(
            medios, "leer_robots", side_effect=httpx.ConnectError("caido")
        ), patch.object(extraccion.alerts, "enviar_alerta") as alerta:
            sondear("https://medio.test", ["https://medio.test/feed"])

        alerta.assert_not_called()

    def test_la_url_base_tambien_se_valida(self, red):
        """
        `_sondear_robots` le pide `{url_base}/robots.txt`, así que un `url_base`
        apuntando a la red interna sería el mismo SSRF entrando por la puerta de
        al lado.
        """
        with pytest.raises(FeedInservible, match="privada o local"):
            sondear("http://192.168.0.1", ["https://medio.test/feed"])

    def test_sin_feeds_no_hay_alta(self, red):
        with pytest.raises(FeedInservible, match="al menos un feed"):
            sondear("https://medio.test", [])


# --------------------------------------------------------------------------
# POST /medios
# --------------------------------------------------------------------------


class TestAltaMedio:
    def test_da_de_alta_y_lo_deja_habilitado(self, client, session: Session, red):
        """
        **Nace activo**, a diferencia de `POST /modelos`: allá prender uno apaga
        a los demás, acá los medios conviven y sumar uno es aditivo.
        """
        respuesta = client.post("/medios", json=ALTA)

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["medio"]["activo"] is True
        assert cuerpo["medio"]["nombre"] == "Medio Test"
        assert cuerpo["sondeo"]["items_totales"] == 3

        guardado = session.exec(select(Medio).where(Medio.nombre == "Medio Test")).one()
        assert guardado.activo is True

    def test_guarda_los_datos_que_manda_el_operador(self, client, session, red):
        respuesta = client.post(
            "/medios",
            json={**ALTA, "idioma": "pt", "pais": "BR", "logo_url": "https://x.test/l.png"},
        )

        medio = respuesta.json()["medio"]
        assert (medio["idioma"], medio["pais"]) == ("pt", "BR")
        assert medio["logo_url"] == "https://x.test/l.png"

    def test_los_defaults_son_los_del_modelo(self, client, red):
        medio = client.post("/medios", json=ALTA).json()["medio"]

        assert medio["idioma"] == "es"
        assert medio["pais"] is None
        assert medio["extraer_por_url"] is False

    def test_extraer_por_url_lo_decide_el_operador(self, client, red):
        """
        El sondeo detecta que el feed no trae cuerpo y lo informa, **pero no
        prende la bandera solo**: marca los medios donde el motor va a buscar a
        la página el cuerpo que el medio eligió no publicar, y cruzar esa línea
        es decisión de quien acepta los términos.
        """
        get, _ = red
        get.return_value = _respuesta(FEED_SIN_CUERPO)

        sin_bandera = client.post("/medios", json=ALTA).json()
        assert sin_bandera["medio"]["extraer_por_url"] is False
        assert any(
            a.startswith("Ninguno de los") for a in sin_bandera["avisos"]
        )

        con_bandera = client.post(
            "/medios",
            json={**ALTA, "nombre": "Otro", "extraer_por_url": True},
        ).json()
        assert con_bandera["medio"]["extraer_por_url"] is True

    def test_los_avisos_viajan_sin_bloquear(self, client, red):
        get, _ = red
        get.return_value = _respuesta(FEED_SIN_CUERPO)

        respuesta = client.post("/medios", json=ALTA)

        assert respuesta.status_code == 200
        assert respuesta.json()["avisos"]

    def test_un_feed_inservible_devuelve_422_y_no_guarda(self, client, session, red):
        get, _ = red
        get.return_value = _respuesta("", status=404)

        respuesta = client.post("/medios", json=ALTA)

        assert respuesta.status_code == 422
        assert session.exec(select(Medio)).all() == []

    def test_un_nombre_repetido_devuelve_409(self, client, red):
        client.post("/medios", json=ALTA)
        respuesta = client.post("/medios", json={**ALTA, "url_base": "https://otro.test"})

        assert respuesta.status_code == 409
        assert "Ya existe" in respuesta.json()["detalle"]

    def test_la_carrera_entre_el_select_y_el_insert_sale_como_409(self, client, red):
        """
        El SELECT de duplicados deja una ventana hasta el INSERT. El índice único
        de `Medio.nombre` protege el dato; esto comprueba que además protege la
        respuesta, que si no salía como un 500 por una carrera normal.
        """
        with patch.object(
            Session,
            "commit",
            side_effect=IntegrityError("INSERT", {}, Exception("duplicado")),
        ):
            respuesta = client.post("/medios", json=ALTA)

        assert respuesta.status_code == 409

    def test_un_campo_de_mas_se_rechaza(self, client, red):
        """
        Mismo criterio que `AltaModelo`: descartar en silencio un campo que
        alguien creyó que el motor iba a leer es la peor respuesta.
        """
        respuesta = client.post("/medios", json={**ALTA, "activo": False})

        assert respuesta.status_code == 422

    def test_hace_falta_al_menos_un_feed(self, client, red):
        respuesta = client.post("/medios", json={**ALTA, "feeds_rss": []})

        assert respuesta.status_code == 422


# --------------------------------------------------------------------------
# GET /medios y PATCH /medios/{id}
# --------------------------------------------------------------------------


class TestListarMedios:
    def test_lista_activos_y_deshabilitados(self, client, session):
        session.add(Medio(nombre="Uno", url_base="https://uno.test", feeds_rss=["u"]))
        session.add(
            Medio(
                nombre="Dos", url_base="https://dos.test", feeds_rss=["d"], activo=False
            )
        )
        session.commit()

        cuerpo = client.get("/medios").json()

        assert (cuerpo["total"], cuerpo["activos"]) == (2, 1)
        assert [m["nombre"] for m in cuerpo["medios"]] == ["Dos", "Uno"]


class TestHabilitarMedio:
    @pytest.fixture
    def medio(self, session: Session) -> Medio:
        fila = Medio(
            nombre="Medio Test",
            url_base="https://medio.test",
            feeds_rss=["https://medio.test/feed"],
        )
        session.add(fila)
        session.commit()
        session.refresh(fila)
        return fila

    def test_deshabilitar_no_borra(self, client, session, medio):
        respuesta = client.patch(f"/medios/{medio.id}?activo=false")

        assert respuesta.status_code == 200
        assert respuesta.json()["medio"]["activo"] is False
        # Sigue existiendo, con todo lo suyo.
        assert session.get(Medio, medio.id) is not None
        assert session.get(Medio, medio.id).feeds_rss == ["https://medio.test/feed"]

    def test_deshabilitar_no_toca_la_red(self, client, medio):
        """
        **La válvula de escape.** Si un medio está devolviendo basura o golpeando
        de más, hay que poder apagarlo con la conexión caída y su servidor
        muerto. Un apagado que dependa de que el feed responda falla justo cuando
        se lo necesita.
        """
        with patch.object(
            medios, "sondear", side_effect=AssertionError("no debería sondear")
        ):
            respuesta = client.patch(f"/medios/{medio.id}?activo=false")

        assert respuesta.status_code == 200

    def test_habilitar_vuelve_a_sondear(self, client, session, medio, red):
        medio.activo = False
        session.add(medio)
        session.commit()

        respuesta = client.patch(f"/medios/{medio.id}?activo=true")

        assert respuesta.status_code == 200
        assert respuesta.json()["medio"]["activo"] is True
        assert respuesta.json()["sondeo"]["items_totales"] == 3

    def test_habilitar_con_el_feed_caido_no_lo_prende(self, client, session, medio, red):
        """El feed pudo morirse entre el alta y hoy, y conviene verlo al apretar."""
        medio.activo = False
        session.add(medio)
        session.commit()
        get, _ = red
        get.return_value = _respuesta("", status=410)

        respuesta = client.patch(f"/medios/{medio.id}?activo=true")

        assert respuesta.status_code == 422
        session.refresh(medio)
        assert medio.activo is False

    def test_un_medio_que_no_existe_devuelve_404(self, client):
        assert client.patch("/medios/9999?activo=false").status_code == 404

    def test_habilitar_no_apaga_a_los_demas(self, client, session, medio, red):
        """
        A diferencia de `PATCH /modelos/{id}`: allá la credencial es una sola y
        dos proveedores prendidos son un estado que no se puede usar. Acá el
        clustering **necesita** varios medios para encontrar el mismo hecho
        contado por distintas redacciones.
        """
        otro = Medio(nombre="Otro", url_base="https://otro.test", feeds_rss=["o"])
        session.add(otro)
        session.commit()

        client.patch(f"/medios/{medio.id}?activo=true")

        session.refresh(otro)
        assert otro.activo is True


# --------------------------------------------------------------------------
# El círculo completo: la bandera manda sobre la ingesta
# --------------------------------------------------------------------------


class TestLaBanderaMandaSobreLaIngesta:
    """
    Lo que le da sentido a todo lo anterior. `ingerir_todos_los_medios` filtra
    por `Medio.activo` desde la Fase 2; estos tests fijan que el endpoint que
    ahora escribe esa bandera efectivamente cambia lo que el pipeline sale a
    buscar — que es lo que "deshabilitar" significa para quien lo usa.
    """

    @pytest.fixture
    def dos_medios(self, session: Session):
        # URLs de verdad: al rehabilitar, el endpoint las vuelve a sondear y
        # `validar_url_de_feed` rechaza cualquier cosa que no sea una URL.
        uno = Medio(
            nombre="Uno", url_base="https://uno.test", feeds_rss=["https://uno.test/feed"]
        )
        dos = Medio(
            nombre="Dos", url_base="https://dos.test", feeds_rss=["https://dos.test/feed"]
        )
        session.add(uno)
        session.add(dos)
        session.commit()
        session.refresh(uno)
        session.refresh(dos)
        return uno, dos

    def _ingeridos(self, session) -> list:
        from src.services import ingestion

        with patch.object(ingestion, "ingerir_medio", return_value={}) as ingerir:
            ingestion.ingerir_todos_los_medios(session)
        return [llamada.args[1].nombre for llamada in ingerir.call_args_list]

    def test_un_medio_deshabilitado_no_se_ingiere(self, client, session, dos_medios):
        uno, _ = dos_medios

        client.patch(f"/medios/{uno.id}?activo=false")

        assert self._ingeridos(session) == ["Dos"]

    def test_y_vuelve_apenas_se_lo_rehabilita(self, client, session, dos_medios, red):
        uno, _ = dos_medios
        client.patch(f"/medios/{uno.id}?activo=false")
        assert self._ingeridos(session) == ["Dos"]

        client.patch(f"/medios/{uno.id}?activo=true")

        assert sorted(self._ingeridos(session)) == ["Dos", "Uno"]


# --------------------------------------------------------------------------
# SSRF por redirect: el agujero que encontro el ataque del 03/09/2026
# --------------------------------------------------------------------------


def _redirige_a(destino: str, host_publico: str = "medio-publico.test"):
    """
    httpx falso: la primera URL (publica) responde un 302 hacia `destino`.

    Es la forma exacta del ataque. El validador aprueba la primera URL porque
    es publica; lo que hay que comprobar es que el SALTO tambien se valide.
    """

    def falso(url, **kwargs):
        pedido = httpx.Request("GET", url)
        if host_publico in str(url):
            return httpx.Response(
                302, headers={"location": destino}, request=pedido
            )
        return httpx.Response(200, text=FEED_SANO, request=pedido)

    return falso


class TestRedirectsValidadosSalto:
    """
    `follow_redirects=True` era un SSRF: se validaba la URL que mandaban y
    httpx despues se iba sola a donde dijera el `Location`. Verificado en su
    momento contra un servicio senuelo — el motor leyo el cuerpo de algo que
    escuchaba en 127.0.0.1 y cuya URL directa el validador si rechazaba.
    """

    @pytest.mark.parametrize(
        "destino",
        [
            "http://127.0.0.1:9998/secreto",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/admin",
            "http://192.168.1.1/",
            "http://100.64.0.1/",
            "file:///etc/passwd",
            "http://[::ffff:127.0.0.1]/secreto",
            "http://[2002:7f00:1::]/",
        ],
    )
    def test_un_redirect_hacia_adentro_se_corta(self, destino):
        with patch.object(medios.httpx, "get", side_effect=_redirige_a(destino)):
            with pytest.raises(FeedInservible):
                medios._bajar("https://medio-publico.test/feed")

    def test_un_redirect_legitimo_se_sigue(self):
        """
        No alcanza con cortar todo: 3 de los 8 feeds del roster redirigen, y
        `gente.com.ar` cambia de dominio a `revistagente.com`. Rechazarlos
        habria dado de baja tres medios en produccion.
        """
        with patch.object(
            medios.httpx,
            "get",
            side_effect=_redirige_a("https://www.revistagente.com/feed/"),
        ):
            assert medios._bajar("https://medio-publico.test/feed") == FEED_SANO

    def test_un_location_relativo_se_resuelve_contra_la_url_pedida(self):
        """Sin `urljoin`, un `Location: /feed/nuevo` llegaria al validador sin dominio."""
        pedidas = []

        def falso(url, **kwargs):
            pedidas.append(str(url))
            pedido = httpx.Request("GET", url)
            if str(url).endswith("/feed"):
                return httpx.Response(
                    302, headers={"location": "/otro/lugar"}, request=pedido
                )
            return httpx.Response(200, text=FEED_SANO, request=pedido)

        with patch.object(medios.httpx, "get", side_effect=falso):
            assert medios._bajar("https://medio-publico.test/feed") == FEED_SANO

        assert pedidas[1] == "https://medio-publico.test/otro/lugar"

    def test_el_primer_salto_tambien_se_valida(self):
        """
        `bajar_siguiendo_redirects` revalida la URL inicial aunque el llamador
        ya lo haya hecho: asi es segura la llame quien la llame, en vez de
        depender de que su llamador se acuerde.
        """
        with pytest.raises(FeedInservible, match="privada o local"):
            medios.bajar_siguiendo_redirects(
                "http://127.0.0.1/feed", agente="x", timeout=1
            )

    def test_una_cadena_infinita_corta_por_el_tope(self):
        def falso(url, **kwargs):
            pedido = httpx.Request("GET", url)
            n = len(str(url))
            return httpx.Response(
                302,
                headers={"location": f"https://medio-publico.test/{'a' * n}"},
                request=pedido,
            )

        with patch.object(medios.httpx, "get", side_effect=falso):
            with pytest.raises(FeedInservible, match="encadena"):
                medios._bajar("https://medio-publico.test/feed")

    def test_un_bucle_se_detecta_por_url_repetida(self):
        with patch.object(
            medios.httpx,
            "get",
            side_effect=_redirige_a("https://medio-publico.test/feed"),
        ):
            with pytest.raises(FeedInservible, match="bucle"):
                medios._bajar("https://medio-publico.test/feed")

    def test_un_3xx_sin_location_no_cuenta_como_salto(self):
        """
        En httpx 0.28 `respuesta.is_redirect` da True para cualquier 3xx tenga
        o no `Location`. Por eso se mira el header y no esa propiedad.
        """

        def falso(url, **kwargs):
            return httpx.Response(304, request=httpx.Request("GET", url))

        with patch.object(medios.httpx, "get", side_effect=falso):
            with pytest.raises(FeedInservible, match="no responde"):
                medios._bajar("https://medio-publico.test/feed")

    def test_una_url_larguisima_es_422_y_no_500(self):
        """
        `httpx.InvalidURL` es la unica excepcion de httpx que NO hereda de
        `HTTPError`, asi que se escapaba del `except` y salia como un 500:
        bastaba con mandar una URL de feed de mas de 64 KB.
        """
        with pytest.raises(FeedInservible):
            medios._bajar("https://medio-publico.test/" + "a" * 200_000)


    def test_httpx_no_sigue_los_redirects_por_su_cuenta(self):
        """
        **El test que faltaba, y lo delato la mutacion.** Los demas mockean
        `httpx.get` entero, asi que si alguien devolviera `follow_redirects` a
        `True` el mock lo ignoraria y todos seguirian en verde — mientras el
        agujero volveria a estar abierto en produccion, porque httpx se iria
        sola al `Location` sin pasar por el validador.

        Por eso este mira el kwarg y no el resultado.
        """
        llamadas = []

        def falso(url, **kwargs):
            llamadas.append(kwargs)
            return httpx.Response(200, text=FEED_SANO, request=httpx.Request("GET", url))

        with patch.object(medios.httpx, "get", side_effect=falso):
            medios._bajar("https://medio-publico.test/feed")

        assert llamadas[0]["follow_redirects"] is False, (
            "httpx volvio a seguir redirects sola: la validacion por salto "
            "queda de adorno. Ver `bajar_siguiendo_redirects`."
        )

    def test_leer_robots_tampoco_deja_que_httpx_los_siga(self):
        from src.services import extraccion

        llamadas = []

        def falso(url, **kwargs):
            llamadas.append(kwargs)
            robots = "User-agent: *" + chr(10) + "Allow: /" + chr(10)
            return httpx.Response(
                200, text=robots, request=httpx.Request("GET", url)
            )

        with patch.object(medios.httpx, "get", side_effect=falso):
            extraccion.leer_robots("https://medio-publico.test")

        assert llamadas[0]["follow_redirects"] is False

    def test_leer_robots_tambien_valida_los_saltos(self):
        """
        Tenia el mismo agujero, y llega ahi un `url_base` que acaban de mandar.

        No se cerro con `follow_redirects=False`, que era lo obvio: **el
        robots.txt de Revista Gente redirige** (1 de 8 medidos), y cortarlos de
        plano lo habria dejado sin extraccion y con un mail de alerta por ciclo.
        """
        from src.services import extraccion

        with patch.object(
            medios.httpx, "get", side_effect=_redirige_a("http://169.254.169.254/")
        ):
            with pytest.raises(FeedInservible):
                extraccion.leer_robots("https://medio-publico.test")


class TestIPv4EscondidaEnIPv6:
    """
    `::ffff:127.0.0.1` apunta a loopback pero como objeto es un `IPv6Address`,
    y `IPv6Address in IPv4Network("127.0.0.0/8")` da False. Pasaba el filtro
    entero. En Windows no conecta y parecia inofensivo; verificado dentro de
    `python:3.12-slim` —el destino real de despliegue— pasa el filtro Y conecta.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "http://[::ffff:127.0.0.1]/feed",
            "http://[::ffff:7f00:1]/feed",
            "http://[::ffff:10.0.0.1]/feed",
            "http://[2002:7f00:1::]/feed",
            "http://[64:ff9b::7f00:1]/feed",
        ],
    )
    def test_no_se_cuela_por_la_forma_en_que_se_escribe(self, url):
        with pytest.raises(FeedInservible, match="privada o local"):
            validar_url_de_feed(url)

    def test_el_cgnat_tampoco(self):
        """
        `100.64.0.0/10` no lo cubre `is_private` en Python 3.13 (da False), y
        por eso la regla de fondo es `is_global` en positivo y no una lista de
        rangos malos.
        """
        with pytest.raises(FeedInservible, match="privada o local"):
            validar_url_de_feed("http://100.64.0.1/feed")

    def test_las_direcciones_publicas_siguen_pasando(self):
        """La comprobacion en positivo no puede llevarse puesto un medio real."""
        for url in [
            "https://www.perfil.com/feed",
            "https://www.gente.com.ar/feed/",
            "http://8.8.8.8/feed",
        ]:
            assert validar_url_de_feed(url) == url


# --------------------------------------------------------------------------
# Las cotas de entrada (tanda 3 de la auditoría)
# --------------------------------------------------------------------------
#
# Los cuatro hallazgos que quedaron abiertos al cerrar el punto 3. Ninguno
# filtraba datos, y por eso fueron a una tanda aparte; lo que tenían en común es
# que el motor aceptaba y **persistía** entradas que después alguien más iba a
# tener que interpretar o renderizar.


class TestFeedsDistintosYAcotados:
    """
    La amplificación: `feeds_rss` no deduplicaba ni tenía techo.

    Medido antes del arreglo con el motor vivo: 500 copias de la misma URL en un
    solo POST daban **501 pedidos reales** al mismo servidor —lineal, uno por
    copia, más el robots— con nuestro User-Agent puesto, y la fila quedaba con
    las 500 repetidas adentro.
    """

    def test_la_misma_url_repetida_se_sondea_una_sola_vez(self, client, red):
        get, _ = red

        respuesta = client.post(
            "/medios",
            json={**ALTA, "feeds_rss": ["https://medio.test/feed"] * 500},
        )

        assert respuesta.status_code == 200
        # Un solo pedido, no 500. El del robots.txt no pasa por `httpx.get`:
        # `leer_robots` está mockeado aparte en la fixture `red`.
        assert get.call_count == 1
        assert respuesta.json()["medio"]["feeds_rss"] == ["https://medio.test/feed"]

    def test_deduplica_antes_de_medir_el_techo(self, client, red):
        """
        **El orden es la decisión.** Una lista pegada con repetidas pide pocos
        feeds distintos aunque sea larga; cortarla primero por largo la
        rechazaría entera y obligaría a limpiarla a mano para descubrir que
        siempre estuvo dentro del límite.
        """
        muchas_copias = ["https://medio.test/feed", "https://medio.test/otro"] * 60

        respuesta = client.post("/medios", json={**ALTA, "feeds_rss": muchas_copias})

        assert respuesta.status_code == 200
        assert respuesta.json()["medio"]["feeds_rss"] == [
            "https://medio.test/feed",
            "https://medio.test/otro",
        ]

    def test_el_techo_cuenta_feeds_distintos(self, client, red):
        distintos = [f"https://medio.test/feed-{n}" for n in range(MAX_FEEDS_POR_MEDIO + 1)]

        respuesta = client.post("/medios", json={**ALTA, "feeds_rss": distintos})

        assert respuesta.status_code == 422
        assert "feeds distintos" in respuesta.text

    def test_justo_en_el_techo_entra(self, client, red):
        distintos = [f"https://medio.test/feed-{n}" for n in range(MAX_FEEDS_POR_MEDIO)]

        respuesta = client.post("/medios", json={**ALTA, "feeds_rss": distintos})

        assert respuesta.status_code == 200
        assert len(respuesta.json()["medio"]["feeds_rss"]) == MAX_FEEDS_POR_MEDIO

    def test_el_sondeo_tambien_deduplica_por_su_cuenta(self, red):
        """
        La segunda capa, y **cubre el otro camino**: `PATCH` sondea la lista
        guardada, no la que entra por la API, y una fila anterior a esta versión
        —o cargada por `seed_medios.py`, que no deduplica— puede traer repetidas.
        Sin esto, rehabilitar esa fila reabría la amplificación entera.
        """
        get, _ = red

        informe, _avisos = sondear(
            "https://medio.test", ["https://medio.test/feed"] * 50
        )

        assert get.call_count == 1
        assert len(informe["feeds"]) == 1

    def test_rehabilitar_una_fila_con_repetidas_no_amplifica(
        self, client, session: Session, red
    ):
        """El camino de arriba, entero, contra la base."""
        get, _ = red
        fila = Medio(
            nombre="Legado",
            url_base="https://medio.test",
            feeds_rss=["https://medio.test/feed"] * 40,
            activo=False,
        )
        session.add(fila)
        session.commit()
        session.refresh(fila)

        respuesta = client.patch(f"/medios/{fila.id}?activo=true")

        assert respuesta.status_code == 200
        assert get.call_count == 1


class TestCotasDeLargo:
    """
    Se persistieron 500 KB en `url_base` y otros 500 KB en `logo_url`, y
    `GET /medios` los devolvía tal cual.
    """

    # El valor gigante se arma ADENTRO del test y no en el `parametrize`:
    # pytest usa los parámetros para el id del test y lo exporta en
    # `PYTEST_CURRENT_TEST`, y Windows corta las variables de entorno en 32767
    # caracteres. Con 500 KB adentro la suite revienta en el teardown.
    @pytest.mark.parametrize("campo", ["url_base", "logo_url"])
    def test_una_url_enorme_no_entra(self, client, red, campo):
        respuesta = client.post(
            "/medios", json={**ALTA, campo: "https://medio.test/" + "A" * 500_000}
        )

        assert respuesta.status_code == 422

    def test_un_feed_enorme_tampoco(self, client, red):
        """
        La cota va en el **elemento** de la lista y no solo en la lista: sin eso
        un único feed de 500 KB pasaba.
        """
        respuesta = client.post(
            "/medios",
            json={**ALTA, "feeds_rss": ["https://medio.test/feed?q=" + "A" * 500_000]},
        )

        assert respuesta.status_code == 422

    def test_las_urls_del_roster_entran_con_lugar_de_sobra(self, client, red):
        """
        La cota no puede llevarse puesto un feed que existe. La más larga del
        roster medido es la de Ciudad Magazine, con 62 caracteres: entra 33
        veces en el techo.
        """
        respuesta = client.post(
            "/medios",
            json={
                **ALTA,
                "url_base": "https://www.ciudad.com.ar",
                "feeds_rss": [
                    "https://www.ciudad.com.ar/arc/outboundfeeds/rss/?outputType=xml"
                ],
            },
        )

        assert respuesta.status_code == 200
        assert max(len(u) for u in respuesta.json()["medio"]["feeds_rss"]) < MAX_LARGO_URL


class TestLogoUrlValidado:
    """
    `logo_url` es el único campo del medio que una interfaz va a poner adentro
    de un atributo de HTML. Antes del arreglo se guardaba
    `javascript:alert(document.cookie)` y `GET /medios` lo devolvía intacto: XSS
    almacenado esperando a la aplicación de escritorio.
    """

    @pytest.mark.parametrize(
        "logo",
        [
            "javascript:alert(document.cookie)",
            "JavaScript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "file:///etc/passwd",
            "vbscript:msgbox(1)",
            "https://usuario:clave@medio.test/logo.png",
            "https://",
        ],
    )
    def test_un_logo_que_no_es_http_no_entra(self, client, session, red, logo):
        respuesta = client.post("/medios", json={**ALTA, "logo_url": logo})

        assert respuesta.status_code == 422
        # Y no quedó nada guardado a medias.
        assert session.exec(select(Medio)).all() == []

    def test_el_error_dice_que_es_el_logo(self, client, red):
        """Un 422 que no dice qué campo revisar obliga a adivinar."""
        respuesta = client.post(
            "/medios", json={**ALTA, "logo_url": "javascript:alert(1)"}
        )

        assert "logo" in respuesta.json()["detalle"].lower()

    def test_un_logo_normal_sigue_entrando(self, client, red):
        respuesta = client.post(
            "/medios", json={**ALTA, "logo_url": "https://medio.test/logo.png"}
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["medio"]["logo_url"] == "https://medio.test/logo.png"

    def test_en_blanco_es_sin_logo_y_no_un_error(self, client, red):
        """
        Un formulario que deja el campo vacío manda `""`, no `null`. Guardar `""`
        sería un tercer estado, y rechazarlo sería un 422 por dejar un campo
        opcional en blanco.
        """
        for vacio in ("", "   "):
            respuesta = client.post(
                "/medios",
                json={**ALTA, "nombre": f"Medio {vacio!r}", "logo_url": vacio},
            )

            assert respuesta.status_code == 200
            assert respuesta.json()["medio"]["logo_url"] is None

    def test_no_resuelve_el_host_del_logo(self, red):
        """
        **La diferencia deliberada con el feed.** El motor nunca baja esta URL:
        quien la pide es el navegador de quien mire la interfaz. Un logo servido
        desde la intranet de quien despliega esto tiene que entrar, y resolver
        DNS acá sería pagar una consulta de red por alta para defender un SSRF
        que no existe.
        """
        assert validar_url_de_logo("http://192.168.1.10/logo.png")
        assert validar_url_de_logo("http://localhost:8080/logo.png")

        # Mientras que el feed, que el motor SÍ baja, las sigue rechazando.
        with pytest.raises(FeedInservible, match="privada o local"):
            validar_url_de_feed("http://192.168.1.10/feed")

    def test_el_logo_se_valida_antes_de_gastar_pedidos(self, client, red):
        """No tiene sentido sondear tres feeds para después rechazar por el logo."""
        get, _ = red

        client.post("/medios", json={**ALTA, "logo_url": "javascript:alert(1)"})

        assert get.call_count == 0


class TestIdiomaYPaisConForma:
    """
    Ocho caracteres alcanzan para `<script>`, que es exactamente el largo de
    `idioma`. Son códigos, no texto libre, así que la forma se exige entera.
    """

    @pytest.mark.parametrize(
        "campo, valor",
        [
            ("idioma", "<script>"),
            ("idioma", "../../et"),
            ("idioma", "e"),
            ("idioma", "es_AR_x"),
            ("pais", "<>"),
            ("pais", "A1"),
        ],
    )
    def test_lo_que_no_es_un_codigo_no_entra(self, client, red, campo, valor):
        respuesta = client.post("/medios", json={**ALTA, campo: valor})

        assert respuesta.status_code == 422

    @pytest.mark.parametrize(
        "idioma, pais", [("es", "AR"), ("pt", "BR"), ("en", "US"), ("pt-BR", None)]
    )
    def test_los_codigos_de_verdad_entran(self, client, red, idioma, pais):
        respuesta = client.post(
            "/medios", json={**ALTA, "idioma": idioma, "pais": pais}
        )

        assert respuesta.status_code == 200

    def test_el_nombre_sigue_siendo_texto_libre(self, client, red):
        """
        **Dónde se traza la línea.** `logo_url`, `idioma` y `pais` se acotan
        porque son URLs y códigos: valores con forma, y en el caso del logo el
        esquema es la parte ejecutable. `nombre` es texto para mostrar, y
        escaparlo al renderizar es trabajo del que lo renderiza — filtrarlo acá
        sería romper un medio que se llame `Página/12` o `AM 750 & Co.`
        """
        respuesta = client.post("/medios", json={**ALTA, "nombre": "Página/12 & Co."})

        assert respuesta.status_code == 200
        assert respuesta.json()["medio"]["nombre"] == "Página/12 & Co."


class TestIdFueraDeRango:
    """
    `Medio.id` es `INTEGER` en Postgres —32 bits—, así que un id más grande no
    es "no encontrado": es un valor que la columna no puede representar. Antes
    del arreglo reventaba con `OverflowError` en SQLite y `numeric out of range`
    en Postgres, o sea un 500 por una entrada mala.
    """

    @pytest.mark.parametrize("id_malo", ["99999999999999999999999", "2147483648", "-1", "0"])
    def test_un_id_imposible_es_422_y_no_500(self, client, id_malo):
        respuesta = client.patch(f"/medios/{id_malo}?activo=false")

        assert respuesta.status_code == 422

    def test_un_id_valido_que_no_existe_sigue_siendo_404(self, client):
        """La cota no puede tapar la diferencia entre 'imposible' y 'no está'."""
        respuesta = client.patch("/medios/12345?activo=false")

        assert respuesta.status_code == 404
