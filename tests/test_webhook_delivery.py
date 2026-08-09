"""
Tests de la entrega de síntesis al back-end.

Se mockea `httpx.post`, que es la frontera con el otro sistema. Lo que se prueba
es lo nuestro: la forma del payload (que es un contrato con otro equipo), que la
firma se calcule sobre los bytes que realmente viajan, y el comportamiento ante
cada tipo de respuesta — que es donde estaba el riesgo de reintentar para
siempre o de dejar de reintentar antes de tiempo.
"""
import hashlib
import hmac
import json
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlmodel import Session

from src.config import settings
from src.models import Cluster, Medio, Noticia, Sintesis
from src.services import webhook_delivery
from src.services.webhook_delivery import (
    EntregaRechazada,
    construir_payload,
    entregar_pendientes,
    entregar_sintesis,
    firmar,
    serializar,
    sintesis_pendientes,
)

SECRETO = "secreto-de-prueba"
URL = "https://backend.sinruido.test/webhooks/sintesis"


@pytest.fixture(autouse=True)
def webhook_configurado():
    """Deja el webhook configurado y sin cooldown de alertas entre tests."""
    with patch.object(settings, "WEBHOOK_URL", URL), patch.object(
        settings, "WEBHOOK_SECRET", SECRETO
    ):
        webhook_delivery.enviar_alerta = MagicMock(return_value=True)
        yield


@pytest.fixture
def medios(session: Session) -> list:
    creados = []
    for nombre in ["La Nación", "TN"]:
        m = Medio(
            nombre=nombre,
            url_base=f"https://{nombre[:3].lower()}.com",
            feed_rss=f"https://{nombre[:3].lower()}.com/rss",
        )
        session.add(m)
        creados.append(m)
    session.commit()
    for m in creados:
        session.refresh(m)
    return creados


@pytest.fixture
def sintesis(session: Session, medios) -> Sintesis:
    """Una síntesis completa, con su hecho, sus fuentes y su comparativa."""
    cluster = Cluster(titulo_evento="Un hecho que cubrieron dos medios", estado="abierto")
    session.add(cluster)
    session.commit()
    session.refresh(cluster)

    noticias = []
    for numero, medio in enumerate(medios, start=1):
        noticia = Noticia(
            medio_id=medio.id,
            cluster_id=cluster.id,
            titulo=f"Titular {numero}",
            url=f"https://test.com/{numero}",
            guid=f"guid-{numero}",
            contenido_limpio="Cuerpo de la nota.",
            fecha_publicacion=datetime(2026, 8, 9, 10, numero, 0),
        )
        session.add(noticia)
        noticias.append(noticia)
    session.commit()

    item = Sintesis(
        cluster_id=cluster.id,
        titulo_angulo="El hecho central",
        resumen_neutro="Pasó algo, contado sin adjetivos.",
        puntos_clave=["Un hecho verificado", "Otro hecho verificado"],
        comparativa_enfoques={
            "TN": {"destaco": "El operativo", "omitio": "El comunicado", "cita": "una frase"},
            "La Nación": {"destaco": "El comunicado", "omitio": "El operativo", "cita": "otra frase"},
        },
        topico="deportes",
        topico_secundario="espectaculos",
        fecha_generacion=datetime(2026, 8, 9, 12, 0, 0),
    )
    item.noticias = noticias
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def respuesta_mock(codigo: int = 200, texto: str = "") -> MagicMock:
    respuesta = MagicMock()
    respuesta.status_code = codigo
    respuesta.text = texto
    # httpx levanta para todo 4xx y 5xx; el filtro de qué se reintenta lo hace
    # `_postear` antes de llegar acá.
    if codigo >= 400:
        respuesta.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {codigo}", request=MagicMock(), response=MagicMock()
        )
    return respuesta


class TestPayload:
    def test_tiene_la_forma_del_contrato(self, session: Session, sintesis: Sintesis):
        payload = construir_payload(session, sintesis)

        assert payload["version"] == webhook_delivery.VERSION_PAYLOAD
        assert payload["sintesis"]["id"] == sintesis.id
        assert payload["sintesis"]["titulo"] == "El hecho central"
        assert payload["sintesis"]["puntos_clave"] == [
            "Un hecho verificado",
            "Otro hecho verificado",
        ]
        assert payload["hecho"]["id"] == sintesis.cluster_id
        assert payload["hecho"]["abierto"] is True
        assert len(payload["comparativa"]) == 2
        assert len(payload["fuentes"]) == 2

    def test_la_comparativa_viaja_con_el_id_del_medio(
        self, session: Session, sintesis: Sintesis, medios
    ):
        """
        El nombre para mostrar no sirve como identificador del otro lado: cambia
        con un rebranding o al corregirle una tilde.
        """
        payload = construir_payload(session, sintesis)
        por_nombre = {e["medio"]["nombre"]: e["medio"]["id"] for e in payload["comparativa"]}

        assert por_nombre["TN"] == medios[1].id
        assert por_nombre["La Nación"] == medios[0].id

    def test_descarta_la_comparativa_de_un_medio_que_no_existe(
        self, session: Session, sintesis: Sintesis
    ):
        sintesis.comparativa_enfoques = dict(
            sintesis.comparativa_enfoques,
            Inventado={"destaco": "x", "omitio": "y", "cita": "z"},
        )
        session.add(sintesis)
        session.commit()

        payload = construir_payload(session, sintesis)
        nombres = [e["medio"]["nombre"] for e in payload["comparativa"]]

        assert "Inventado" not in nombres
        assert len(nombres) == 2

    def test_lleva_el_topico_para_que_el_backend_pueda_filtrar(
        self, session: Session, sintesis: Sintesis
    ):
        payload = construir_payload(session, sintesis)

        assert payload["sintesis"]["topico"] == "deportes"
        assert payload["sintesis"]["topico_secundario"] == "espectaculos"

    def test_el_topico_secundario_viaja_como_null_si_no_hay(
        self, session: Session, sintesis: Sintesis
    ):
        sintesis.topico_secundario = None
        session.add(sintesis)
        session.commit()

        assert construir_payload(session, sintesis)["sintesis"]["topico_secundario"] is None

    def test_no_manda_el_titulo_del_cluster(self, session: Session, sintesis: Sintesis):
        """
        `titulo_evento` es el titular de la primera nota que formó el cluster,
        o sea el encuadre de un medio. Mandarlo como nombre del hecho sería
        entregar como neutro justo lo que el producto se propone no hacer.
        """
        payload = construir_payload(session, sintesis)

        assert "titulo" not in payload["hecho"]

    def test_las_fechas_llevan_zona_explicita(self, session: Session, sintesis: Sintesis):
        """Sin la Z, del otro lado se interpretan como hora local."""
        payload = construir_payload(session, sintesis)

        assert payload["sintesis"]["fecha_generacion"] == "2026-08-09T12:00:00Z"
        assert payload["fuentes"][0]["fecha_publicacion"].endswith("Z")

    def test_es_creada_la_primera_vez_y_actualizada_despues(
        self, session: Session, sintesis: Sintesis
    ):
        assert construir_payload(session, sintesis)["evento"] == "sintesis.creada"

        sintesis.fecha_envio = datetime.utcnow()
        assert construir_payload(session, sintesis)["evento"] == "sintesis.actualizada"

    def test_las_fuentes_van_por_fecha_de_publicacion(
        self, session: Session, sintesis: Sintesis
    ):
        payload = construir_payload(session, sintesis)
        fechas = [f["fecha_publicacion"] for f in payload["fuentes"]]

        assert fechas == sorted(fechas)


class TestFirma:
    def test_se_calcula_sobre_los_bytes_que_viajan(self, session: Session, sintesis: Sintesis):
        cuerpo = serializar(construir_payload(session, sintesis))
        firma = firmar(cuerpo, "1754740800")

        esperada = hmac.new(
            SECRETO.encode("utf-8"), b"1754740800." + cuerpo, hashlib.sha256
        ).hexdigest()
        assert firma == f"sha256={esperada}"

    def test_el_timestamp_esta_dentro_de_lo_firmado(self, session: Session, sintesis: Sintesis):
        """
        Si viajara solo en el header, un request capturado se podría reenviar
        con la fecha cambiada y la firma seguiría validando.
        """
        cuerpo = serializar(construir_payload(session, sintesis))

        assert firmar(cuerpo, "1754740800") != firmar(cuerpo, "1754740900")

    def test_el_cuerpo_serializado_no_escapa_los_acentos(
        self, session: Session, sintesis: Sintesis
    ):
        cuerpo = serializar(construir_payload(session, sintesis))

        assert "La Nación".encode("utf-8") in cuerpo
        assert json.loads(cuerpo.decode("utf-8"))["sintesis"]["id"] == sintesis.id


class TestEntrega:
    def test_marca_la_sintesis_como_entregada(self, session: Session, sintesis: Sintesis):
        with patch("httpx.post", return_value=respuesta_mock(200)):
            entregar_sintesis(session, sintesis)

        assert sintesis.enviado_backend is True
        assert sintesis.fecha_envio is not None
        assert sintesis.intentos_envio == 1

    def test_manda_el_cuerpo_ya_serializado_y_no_el_diccionario(
        self, session: Session, sintesis: Sintesis
    ):
        """
        Si el cliente HTTP volviera a serializar por su cuenta, cualquier
        diferencia de espaciado rompería la firma del otro lado.
        """
        with patch("httpx.post", return_value=respuesta_mock(200)) as post:
            entregar_sintesis(session, sintesis)

        kwargs = post.call_args.kwargs
        assert "json" not in kwargs
        assert isinstance(kwargs["content"], bytes)
        assert kwargs["headers"][webhook_delivery.HEADER_FIRMA].startswith("sha256=")
        assert kwargs["headers"][webhook_delivery.HEADER_TIMESTAMP].isdigit()

    def test_el_timestamp_es_epoch_utc_real(self, session: Session, sintesis: Sintesis):
        """
        Regresión. Antes se calculaba con `datetime.utcnow().timestamp()`, que
        sobre un datetime naive interpreta la hora como local: desde Argentina
        salía corrido 3 horas y un receptor que valide la ventana anti-replay
        —como pide nuestro contrato— rechazaba todo con 401.
        """
        antes = int(time.time())
        with patch("httpx.post", return_value=respuesta_mock(200)) as post:
            entregar_sintesis(session, sintesis)
        despues = int(time.time())

        enviado = int(post.call_args.kwargs["headers"][webhook_delivery.HEADER_TIMESTAMP])
        assert antes <= enviado <= despues

    def test_cuenta_el_intento_aunque_falle(self, session: Session, sintesis: Sintesis):
        """
        Si el contador solo avanzara con el éxito, un backend caído nunca
        alcanzaría el tope y se reintentaría para siempre.
        """
        with patch("httpx.post", side_effect=httpx.ConnectError("sin conexión")):
            with pytest.raises(httpx.HTTPError):
                entregar_sintesis(session, sintesis)

        assert sintesis.intentos_envio == 1
        assert sintesis.enviado_backend is False

    def test_un_4xx_no_se_reintenta(self, session: Session, sintesis: Sintesis):
        with patch("httpx.post", return_value=respuesta_mock(422, "campo faltante")) as post:
            with pytest.raises(EntregaRechazada):
                entregar_sintesis(session, sintesis)

        assert post.call_count == 1

    def test_un_429_si_se_reintenta(self, session: Session, sintesis: Sintesis):
        """No es un rechazo del contenido sino una condición pasajera."""
        with patch(
            "httpx.post", side_effect=[respuesta_mock(429), respuesta_mock(200)]
        ) as post:
            entregar_sintesis(session, sintesis)

        assert post.call_count == 2
        assert sintesis.enviado_backend is True

    def test_un_5xx_se_reintenta(self, session: Session, sintesis: Sintesis):
        with patch(
            "httpx.post", side_effect=[respuesta_mock(503), respuesta_mock(200)]
        ) as post:
            entregar_sintesis(session, sintesis)

        assert post.call_count == 2
        assert sintesis.enviado_backend is True


class TestBarrido:
    def test_entrega_solo_lo_pendiente(self, session: Session, sintesis: Sintesis):
        entregada = Sintesis(
            cluster_id=sintesis.cluster_id,
            titulo_angulo="Ya entregada",
            resumen_neutro="x",
            enviado_backend=True,
        )
        session.add(entregada)
        session.commit()

        with patch("httpx.post", return_value=respuesta_mock(200)) as post:
            stats = entregar_pendientes(session)

        assert post.call_count == 1
        assert stats["entregadas"] == 1
        assert stats["pendientes"] == 1

    def test_una_que_falla_no_frena_a_las_demas(self, session: Session, sintesis: Sintesis):
        otra = Sintesis(
            cluster_id=sintesis.cluster_id, titulo_angulo="Otro ángulo", resumen_neutro="x"
        )
        session.add(otra)
        session.commit()

        with patch(
            "httpx.post",
            side_effect=[respuesta_mock(422, "error"), respuesta_mock(200)],
        ):
            stats = entregar_pendientes(session)

        assert stats["rechazadas"] == 1
        assert stats["entregadas"] == 1

    def test_un_backend_caido_deja_todo_pendiente_sin_romper(
        self, session: Session, sintesis: Sintesis
    ):
        """El barrido de la corrida siguiente lo reintenta solo."""
        with patch("httpx.post", side_effect=httpx.ConnectError("sin conexión")):
            stats = entregar_pendientes(session)

        assert stats["fallidas"] == 1
        assert stats["entregadas"] == 0
        assert sintesis.enviado_backend is False
        assert sintesis.intentos_envio == 1

    def test_deja_de_tomar_las_que_agotaron_los_intentos(
        self, session: Session, sintesis: Sintesis
    ):
        sintesis.intentos_envio = settings.WEBHOOK_MAX_INTENTOS
        session.add(sintesis)
        session.commit()

        with patch("httpx.post", return_value=respuesta_mock(200)) as post:
            stats = entregar_pendientes(session)

        assert post.call_count == 0
        assert stats["agotadas"] == 1

    def test_forzar_reincluye_las_agotadas(self, session: Session, sintesis: Sintesis):
        sintesis.intentos_envio = settings.WEBHOOK_MAX_INTENTOS
        session.add(sintesis)
        session.commit()

        with patch("httpx.post", return_value=respuesta_mock(200)) as post:
            stats = entregar_pendientes(session, forzar=True)

        assert post.call_count == 1
        assert stats["entregadas"] == 1

    def test_avisa_cuando_hay_sintesis_agotadas(self, session: Session, sintesis: Sintesis):
        sintesis.intentos_envio = settings.WEBHOOK_MAX_INTENTOS
        session.add(sintesis)
        session.commit()

        entregar_pendientes(session)

        assert webhook_delivery.enviar_alerta.called

    def test_sin_configurar_no_hace_nada_y_no_falla(self, session: Session, sintesis: Sintesis):
        """
        En desarrollo el webhook todavía no existe. Hacer fallar el paso
        convertiría en ruido la alerta del pipeline.
        """
        with patch.object(settings, "WEBHOOK_URL", None):
            with patch("httpx.post") as post:
                stats = entregar_pendientes(session)

        assert stats["estado"] == "sin configurar"
        assert post.call_count == 0
        assert sintesis.enviado_backend is False

    def test_van_en_orden_de_generacion(self, session: Session, sintesis: Sintesis):
        segunda = Sintesis(
            cluster_id=sintesis.cluster_id, titulo_angulo="Segundo ángulo", resumen_neutro="x"
        )
        session.add(segunda)
        session.commit()

        pendientes = sintesis_pendientes(session)

        assert [s.id for s in pendientes] == sorted(s.id for s in pendientes)


class TestResincronizacion:
    def test_una_resintesis_vuelve_a_ponerla_pendiente(
        self, session: Session, sintesis: Sintesis
    ):
        """
        El contenido cambió, así que hay que reenviarlo aunque ya se haya
        entregado antes. Lo hace `synthesis._persistir`; acá se verifica que el
        barrido lo tome.
        """
        with patch("httpx.post", return_value=respuesta_mock(200)):
            entregar_pendientes(session)
        assert sintesis.enviado_backend is True

        sintesis.enviado_backend = False
        sintesis.intentos_envio = 0
        session.add(sintesis)
        session.commit()

        with patch("httpx.post", return_value=respuesta_mock(200)) as post:
            stats = entregar_pendientes(session)

        assert post.call_count == 1
        assert stats["entregadas"] == 1
