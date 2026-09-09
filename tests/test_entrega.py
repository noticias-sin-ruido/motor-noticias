"""
Tests del destino de entrega configurable (backlog punto 11).

Tres cosas se vigilan acá, y las tres son las que el punto declaró como no
negociables:

1. **Los endpoints exigen token siempre**, aunque el despliegue haya dejado la
   API abierta. Cambiar a dónde salen las síntesis *firmadas* no puede quedar
   sin credencial.
2. **La validación del destino**, que permite la red interna a propósito y
   bloquea link-local.
3. **Cambiar la URL no toca `enviado_backend`**: el destino nuevo recibe desde
   la próxima síntesis, no el histórico entero de golpe.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from src.config import settings
from src.models import Cluster, Sintesis
from src.services.entrega import (
    DestinoInvalido,
    configuracion,
    guardar_url,
    url_de_entrega,
    validar_url_de_entrega,
)
from src.services.webhook_delivery import entregar_pendientes


class TestValidacionDelDestino:
    """
    **La red interna está permitida, y no es un olvido.**

    El plan de este punto decía reusar `medios.REDES_PROHIBIDAS`, que bloquea
    todo lo privado, con el argumento de que acá no hay caso legítimo. Se midió y
    es falso: el destino real de este despliegue es `http://localhost:3011`. La
    regla habría rechazado la única configuración que el motor tuvo, y la
    migración habría sembrado una fila irreingresable.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:3011/webhooks/sintesis",
            "http://127.0.0.1:8080/hook",
            "http://10.0.0.5/hook",
            "https://backend.sinruido.test/webhooks/sintesis",
        ],
    )
    def test_acepta_un_back_end_propio(self, url: str):
        assert validar_url_de_entrega(url) == url

    @pytest.mark.parametrize(
        "url, porque",
        [
            ("file:///etc/passwd", "esquema"),
            ("gopher://algo/x", "esquema"),
            ("https://", "sin dominio"),
            ("https://usuario:clave@back.test/hook", "credenciales embebidas"),
            ("", "vacia"),
        ],
    )
    def test_rechaza_lo_que_no_es_un_destino(self, url: str, porque: str):
        with pytest.raises(DestinoInvalido):
            validar_url_de_entrega(url)

    def test_rechaza_los_metadata_de_las_nubes(self):
        """
        `169.254.169.254` no tiene ningún uso como back-end y reparte
        credenciales de la instancia.
        """
        with pytest.raises(DestinoInvalido, match="link-local"):
            validar_url_de_entrega("http://169.254.169.254/latest/meta-data/")

    def test_no_se_evade_escondiendo_la_ipv4_adentro_de_una_ipv6(self):
        """
        **Sin desenvolver la IPv4 mapeada, el filtro se saltea con una línea.**

        `::ffff:169.254.169.254` apunta a los metadata, pero como objeto es un
        `IPv6Address` y no pertenece a ninguna red IPv4 — así que pasaba entero.
        Está verificado en `medios._direcciones_efectivas` que dentro de
        `python:3.12-slim`, que es el destino real de despliegue, esa forma
        conecta.
        """
        with pytest.raises(DestinoInvalido, match="link-local"):
            validar_url_de_entrega("http://[::ffff:169.254.169.254]/hook")


class TestLasTresListasNoSeDesalinean:
    """
    **Hay tres listas de redes prohibidas y ninguna importa a la otra.**

    `proveedores.base` bloquea sólo link-local, porque un modelo de IA en
    `localhost:11434` es el caso que ese punto existe para habilitar.
    `services.medios` bloquea toda la red interna, porque un medio de noticias en
    `127.0.0.1` no tiene uso legítimo y el endpoint sería un escáner. Y
    `services.entrega` queda en el medio: permite la red privada —el back-end del
    operador vive ahí— y bloquea link-local.

    Las tres están escritas a mano, y eso es deliberado: son **listas
    auditables**, se leen y se entiende qué bloquea cada una y por qué. Componer
    una de otra las acoplaría y perdería justamente eso.

    Lo que no puede pasar es que se **desalineen en silencio**. `test_medios.py`
    ya fija la divergencia entre las dos originales; cuando entró la tercera,
    nada la ataba a ninguna — así que agregar un rango de metadata nuevo a
    `proveedores.base` habría dejado el destino de entrega sin heredarlo, sin que
    nada avisara. Estos dos tests cierran esa deriva.
    """

    def test_entrega_bloquea_al_menos_lo_que_bloquea_proveedores(self):
        """
        **El piso.** `proveedores.base.REDES_PROHIBIDAS` es lo que no tiene uso
        legítimo en ningún lado —link-local, donde viven los metadata de las
        nubes que reparten credenciales—. Si alguien suma un rango ahí, la
        entrega tiene que heredarlo.

        Si este test se cae, la respuesta no es borrarlo: es agregar el rango que
        falta a `services/entrega.REDES_PROHIBIDAS`, o explicar por escrito por
        qué el destino de entrega sí puede alcanzarlo.
        """
        from src.services.entrega import REDES_PROHIBIDAS as DE_ENTREGA
        from src.services.proveedores.base import REDES_PROHIBIDAS as DE_PROVEEDORES

        faltan = set(DE_PROVEEDORES) - set(DE_ENTREGA)
        assert not faltan, (
            f"proveedores.base bloquea redes que el destino de entrega no: "
            f"{sorted(str(r) for r in faltan)}"
        )

    def test_medios_sigue_siendo_el_mas_estricto_de_los_tres(self):
        """
        **El techo.** El orden es `proveedores ⊆ entrega ⊆ medios`, y cada
        escalón agrega por un motivo escrito. Si la entrega empezara a bloquear
        algo que el alta de medios permite, el orden se rompió y hay que mirar
        cuál de los dos está mal.
        """
        from src.services.entrega import REDES_PROHIBIDAS as DE_ENTREGA
        from src.services.medios import REDES_PROHIBIDAS as DE_MEDIOS

        faltan = set(DE_ENTREGA) - set(DE_MEDIOS)
        assert not faltan, (
            f"la entrega bloquea redes que el alta de medios permite, y medios "
            f"debería ser el más estricto: {sorted(str(r) for r in faltan)}"
        )


class TestLaPuertaEstricta:
    """
    `API_TOKEN` es opcional en todo el motor **menos acá**.

    La fixture `api_sin_token` de `conftest` deja la API abierta, que es
    justamente el escenario que estos tests necesitan.
    """

    @pytest.mark.parametrize("metodo, extra", [("get", {}), ("patch", {"json": {"url": None}})])
    def test_sin_api_token_configurado_el_endpoint_no_existe_para_nadie(
        self, client: TestClient, metodo: str, extra: dict
    ):
        respuesta = getattr(client, metodo)("/entrega", **extra)

        # 503 y no 403: falta configuración del servidor, no permiso de quien
        # llama. El mensaje tiene que decir qué hacer.
        assert respuesta.status_code == 503
        assert "API_TOKEN" in respuesta.json()["detail"]

    def test_con_api_token_pide_el_token(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(settings, "API_TOKEN", "el-token")

        assert client.get("/entrega").status_code == 401
        assert (
            client.get("/entrega", headers={"Authorization": "Bearer otro"}).status_code
            == 401
        )
        assert (
            client.get("/entrega", headers={"Authorization": "Bearer el-token"}).status_code
            == 200
        )

    def test_el_resto_de_la_api_sigue_abierta(self, client: TestClient):
        """
        La excepción es de estos dos endpoints y de ninguno más. Si esto empieza
        a fallar, `exigir_token_estricto` se filtró a la puerta general.
        """
        assert client.get("/").status_code == 200
        assert client.get("/modelos").status_code == 200


@pytest.fixture(name="con_token")
def con_token_fixture(client: TestClient, monkeypatch) -> TestClient:
    """Un cliente que ya manda el token, para no repetirlo en cada llamada."""
    monkeypatch.setattr(settings, "API_TOKEN", "el-token")
    client.headers.update({"Authorization": "Bearer el-token"})
    return client


class TestVerYCambiar:
    def test_devuelve_la_url_pero_nunca_el_secreto(
        self, con_token: TestClient, session: Session, monkeypatch
    ):
        """
        **Acá sí va la URL**, a diferencia de `GET /`: esta ruta exige token y el
        operador no puede corregir un destino que no ve. Lo que no sale nunca es
        `WEBHOOK_SECRET`, del que se informa sólo si existe.
        """
        monkeypatch.setattr(settings, "WEBHOOK_SECRET", "no-tiene-que-salir-de-aca")
        guardar_url(session, "https://back.test/hook/z4Kq9mNP")

        respuesta = con_token.get("/entrega")

        assert respuesta.status_code == 200
        entrega = respuesta.json()["entrega"]
        assert entrega["url"] == "https://back.test/hook/z4Kq9mNP"
        assert entrega["configurado"] is True
        assert entrega["secreto_configurado"] is True
        assert "no-tiene-que-salir-de-aca" not in respuesta.text

    def test_avisa_cuando_el_destino_guardado_no_sirve(
        self, con_token: TestClient, session: Session
    ):
        """
        **`configurado` y `valido` no son lo mismo, y la diferencia se ve o no
        se ve.**

        `guardar_url` valida al escribir, pero la fila llega también sembrada por
        la migración desde un `.env` viejo, o editada a mano en la base. Antes de
        esto, el operador veía `configurado: true` mientras el barrido descartaba
        el destino **y sólo lo decía en el log del scheduler**, que corre cada 15
        minutos y no expone su resultado por ninguna ruta. O sea: la API decía que
        estaba configurado y nada entregaba.
        """
        fila = configuracion(session)
        # A mano, salteando `guardar_url`: es el caso que se vigila.
        fila.url = "http://169.254.169.254/latest/meta-data/"
        session.add(fila)
        session.commit()

        entrega = con_token.get("/entrega").json()["entrega"]

        assert entrega["configurado"] is True, "hay una URL guardada, y eso es cierto"
        assert entrega["valido"] is False, "pero el motor no la va a aceptar"
        assert "link-local" in entrega["problema"]

    def test_un_destino_sano_no_reporta_problema(
        self, con_token: TestClient, session: Session
    ):
        guardar_url(session, "https://back.test/hook")

        entrega = con_token.get("/entrega").json()["entrega"]

        assert entrega["valido"] is True
        assert entrega["problema"] is None

    def test_cambiarla_la_deja_guardada_y_la_ve_la_salud(
        self, con_token: TestClient, session: Session
    ):
        respuesta = con_token.patch("/entrega", json={"url": "https://otro.test/hook"})

        assert respuesta.status_code == 200
        assert respuesta.json()["entrega"]["url"] == "https://otro.test/hook"
        assert url_de_entrega(session) == "https://otro.test/hook"
        # `GET /` es ruta abierta: dice que hay destino, sin decir cuál.
        salud = con_token.get("/").json()
        assert salud["entrega_configurada"] is True
        assert "otro.test" not in str(salud)

    def test_null_la_borra_y_la_entrega_deja_de_correr(
        self, con_token: TestClient, session: Session
    ):
        guardar_url(session, "https://back.test/hook")

        respuesta = con_token.patch("/entrega", json={"url": None})

        assert respuesta.status_code == 200
        assert respuesta.json()["entrega"]["configurado"] is False
        assert url_de_entrega(session) is None
        assert con_token.get("/").json()["entrega_configurada"] is False

    def test_un_cuerpo_vacio_no_borra_nada(self, con_token: TestClient, session: Session):
        """
        **`{}` es un 422 y no un borrado.**

        Si `url` tuviera default, un cuerpo vacío sería indistinguible de "borrá
        el destino": el error de tipeo más barato del mundo apagaría la entrega
        sin decir una palabra.
        """
        guardar_url(session, "https://back.test/hook")

        assert con_token.patch("/entrega", json={}).status_code == 422
        assert url_de_entrega(session) == "https://back.test/hook"

    def test_una_url_vacia_tampoco_borra_nada(self, con_token: TestClient, session: Session):
        """
        **La misma protección que `{}`, por la puerta de al lado.**

        `CambioEntrega.url` se hizo obligatorio para que un cuerpo vacío no
        pudiera apagar la entrega en silencio. Pero `{"url": ""}` se colaba
        igual: daba **200 y borraba el destino** — y la cadena vacía es
        exactamente lo que manda un formulario con el campo vaciado, o sea el
        caso más probable de los dos. Verificado antes del arreglo.

        Borrar el destino apaga la entrega. Tiene que costar decirlo: `null`
        explícito y nada más.
        """
        guardar_url(session, "https://back.test/hook")

        for cuerpo in ({"url": ""}, {"url": "   "}):
            respuesta = con_token.patch("/entrega", json=cuerpo)
            assert respuesta.status_code == 422, f"{cuerpo} devolvió {respuesta.status_code}"
            assert url_de_entrega(session) == "https://back.test/hook"

    def test_rechaza_un_campo_de_mas_en_vez_de_ignorarlo(self, con_token: TestClient):
        """
        El campo de más que alguien va a intentar mandar es el secreto.
        Descartárselo en silencio lo dejaría creyendo que el motor lo guardó.
        """
        respuesta = con_token.patch(
            "/entrega", json={"url": "https://back.test/hook", "secreto": "abc123"}
        )

        assert respuesta.status_code == 422

    def test_un_destino_invalido_es_422_y_no_pisa_el_que_estaba(
        self, con_token: TestClient, session: Session
    ):
        guardar_url(session, "https://back.test/hook")

        respuesta = con_token.patch(
            "/entrega", json={"url": "http://169.254.169.254/latest/meta-data/"}
        )

        assert respuesta.status_code == 422
        assert "link-local" in respuesta.json()["detalle"]
        assert url_de_entrega(session) == "https://back.test/hook"


class TestNoReenvia:
    """
    **Cambiar la URL no toca `enviado_backend`.** Es la decisión de fondo del
    punto: cientos de síntesis firmadas saliendo de golpe hacia un back-end que quizá
    recién se levanta, disparadas por lo que para quien lo hace es corregir un
    tipeo. El reenvío masivo ya existe y tiene nombre: `POST /deliver?forzar=true`.
    """

    def test_cambiar_el_destino_no_vuelve_a_poner_nada_pendiente(
        self, con_token: TestClient, session: Session
    ):
        cluster = Cluster(titulo_evento="Un hecho")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        entregadas = [
            Sintesis(
                cluster_id=cluster.id,
                titulo_angulo=f"Angulo {i}",
                resumen_neutro="x",
                enviado_backend=True,
                intentos_envio=1,
            )
            for i in range(3)
        ]
        pendiente = Sintesis(
            cluster_id=cluster.id, titulo_angulo="Sin entregar", resumen_neutro="x"
        )
        session.add_all(entregadas + [pendiente])
        session.commit()

        con_token.patch("/entrega", json={"url": "https://back-nuevo.test/hook"})

        # Se relee de la base y no de los objetos en memoria: lo que se vigila es
        # que nadie haya escrito, no que estos objetos no hayan cambiado.
        session.expire_all()
        marcadas = session.exec(
            select(Sintesis.id).where(Sintesis.enviado_backend == True)  # noqa: E712
        ).all()
        assert len(marcadas) == 3


class TestElBarridoRevalida:
    def test_una_fila_con_un_destino_prohibido_no_se_usa(
        self, session: Session, monkeypatch
    ):
        """
        `guardar_url` valida al escribir, pero la fila también puede llegar
        sembrada por la migración desde un `.env` viejo o editada a mano en la
        base. El validador es el control de seguridad: tiene que custodiar la
        salida real, no sólo el camino de escritura.
        """
        monkeypatch.setattr(settings, "WEBHOOK_SECRET", "secreto")
        fila = configuracion(session)
        # A mano, salteando `guardar_url`: es exactamente el caso que se vigila.
        fila.url = "http://169.254.169.254/latest/meta-data/"
        session.add(fila)
        session.commit()

        with patch("httpx.post") as post:
            stats = entregar_pendientes(session)

        assert stats["estado"] == "destino inválido"
        assert post.call_count == 0
