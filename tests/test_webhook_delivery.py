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
from src.tiempo import ahora_utc
from src.models import Cluster, Medio, Noticia, PublicacionRedes, Sintesis
from src.services import eventos, webhook_delivery
from src.services.entrega import guardar_url
from src.services.webhook_delivery import (
    BackendNoDisponible,
    EntregaRechazada,
    construir_payload,
    entregar_pendientes,
    entregar_sintesis,
    firmar,
    serializar,
    sintesis_pendientes,
)
from tests.conftest import contar_queries

SECRETO = "secreto-de-prueba"
URL = "https://backend.sinruido.test/webhooks/sintesis"


@pytest.fixture(autouse=True)
def webhook_configurado(session: Session):
    """
    Deja el webhook configurado y sin cooldown de alertas entre tests.

    **El destino se escribe con `guardar_url` y el secreto se parchea**, y esa
    asimetría es el punto 11 entero: desde esta versión el destino vive en la
    base y lo cambia el operador, mientras que `WEBHOOK_SECRET` se queda en el
    entorno porque una credencial compartida con otro equipo no va en una fila
    que se respalda y se dumpea. Ver `models/entrega.py`.
    """
    guardar_url(session, URL)
    with patch.object(settings, "WEBHOOK_SECRET", SECRETO):
        webhook_delivery.enviar_alerta = MagicMock(return_value=True)
        yield


@pytest.fixture
def sin_espera():
    """
    Saca la espera creciente de `tenacity` entre reintentos.

    Un `ConnectError` dispara 3 intentos con esperas de 2 y 4 segundos: seis por
    test, y el de los diez barridos serían sesenta. **No mockea lo que se
    prueba** — los tres intentos siguen ocurriendo, sólo se les saca el reloj.
    """
    from tenacity import wait_none

    original = webhook_delivery._postear.retry.wait
    webhook_delivery._postear.retry.wait = wait_none()
    yield
    webhook_delivery._postear.retry.wait = original


@pytest.fixture
def medios(session: Session) -> list:
    creados = []
    for nombre in ["La Nación", "TN"]:
        m = Medio(
            nombre=nombre,
            url_base=f"https://{nombre[:3].lower()}.com",
            feeds_rss=[f"https://{nombre[:3].lower()}.com/rss"],
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
        topicos=["deportes", "espectaculos"],
        subtopicos=["futbol"],
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

    def test_lleva_los_topicos_para_que_el_backend_pueda_filtrar(
        self, session: Session, sintesis: Sintesis
    ):
        payload = construir_payload(session, sintesis)

        assert payload["sintesis"]["topicos"] == ["deportes", "espectaculos"]
        assert payload["sintesis"]["subtopicos"] == ["futbol"]

    def test_subtopicos_viaja_como_lista_vacia_si_no_hay(
        self, session: Session, sintesis: Sintesis
    ):
        sintesis.subtopicos = []
        session.add(sintesis)
        session.commit()

        assert construir_payload(session, sintesis)["sintesis"]["subtopicos"] == []

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

    def test_publicacion_redes_viaja_null_si_no_es_relevante(
        self, session: Session, sintesis: Sintesis
    ):
        """La mayoría de las síntesis no la tiene: no todo ángulo va a redes."""
        assert construir_payload(session, sintesis)["sintesis"]["publicacion_redes"] is None

    def test_publicacion_redes_viaja_completa_si_existe(
        self, session: Session, sintesis: Sintesis
    ):
        sintesis.publicacion_redes = PublicacionRedes(
            resumen_redes="Un párrafo corto para redes.",
            hashtags=["messi", "futbol"],
        )
        session.add(sintesis)
        session.commit()

        payload = construir_payload(session, sintesis)

        assert payload["sintesis"]["publicacion_redes"] == {
            "resumen": "Un párrafo corto para redes.",
            "hashtags": ["messi", "futbol"],
        }


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

    def test_no_cuenta_el_intento_si_el_backend_no_esta(
        self, session: Session, sintesis: Sintesis, sin_espera: None
    ):
        """
        Que nadie conteste no es información sobre esta síntesis.

        Este test afirmaba lo contrario hasta el punto 16 del backlog: se contaba
        el intento para que un back-end caído alcanzara el tope. El efecto real
        era que 75 minutos de caída (5 barridos de 15) descartaban material de
        forma permanente. `intentos_envio` mide rechazos del contenido, no
        barridos que corrieron sin nadie del otro lado.
        """
        with patch("httpx.post", side_effect=httpx.ConnectError("sin conexión")):
            with pytest.raises(BackendNoDisponible):
                entregar_sintesis(session, sintesis)

        assert sintesis.intentos_envio == 0
        assert sintesis.enviado_backend is False

    def test_un_rechazo_del_contenido_si_cuenta(
        self, session: Session, sintesis: Sintesis
    ):
        """La otra mitad del punto 16: un 4xx sí dice algo sobre esta fila."""
        with patch("httpx.post", return_value=respuesta_mock(422, "campo faltante")):
            with pytest.raises(EntregaRechazada):
                entregar_sintesis(session, sintesis)

        assert sintesis.intentos_envio == 1

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
        self, session: Session, sintesis: Sintesis, sin_espera: None
    ):
        """El barrido de la corrida siguiente lo reintenta solo, indefinidamente."""
        with patch("httpx.post", side_effect=httpx.ConnectError("sin conexión")):
            stats = entregar_pendientes(session)

        assert stats["entregadas"] == 0
        assert stats["backend_no_disponible"] is True
        assert sintesis.enviado_backend is False
        # No cuenta: el contador es sobre rechazos, no sobre caídas.
        assert sintesis.intentos_envio == 0

    def test_deja_de_tomar_las_que_agotaron_los_intentos(
        self, session: Session, sintesis: Sintesis
    ):
        sintesis.intentos_envio = settings.WEBHOOK_MAX_INTENTOS
        session.add(sintesis)
        session.commit()

        with patch("httpx.post", return_value=respuesta_mock(200)) as post:
            stats = entregar_pendientes(session)

        assert post.call_count == 0
        # No se avisa de nuevo: ya estaba agotada de antes. Pero sigue
        # contada para que el operador la vea.
        assert stats["agotadas"] == 0
        assert stats["agotadas_total"] == 1

    def test_forzar_reincluye_las_agotadas(self, session: Session, sintesis: Sintesis):
        sintesis.intentos_envio = settings.WEBHOOK_MAX_INTENTOS
        session.add(sintesis)
        session.commit()

        with patch("httpx.post", return_value=respuesta_mock(200)) as post:
            stats = entregar_pendientes(session, forzar=True)

        assert post.call_count == 1
        assert stats["entregadas"] == 1

    def test_avisa_cuando_una_sintesis_agota_los_intentos(
        self, session: Session, sintesis: Sintesis
    ):
        """
        Justo al cruzar el tope, que es cuando hay algo nuevo que contar.

        **Agota con un 4xx y no con una caída.** Desde el punto 16 una caída no
        cuenta intento, así que ya no puede llevar nada al tope: lo único que
        agota es un rechazo del contenido, que es lo que el contador mide.
        """
        sintesis.intentos_envio = settings.WEBHOOK_MAX_INTENTOS - 1
        session.add(sintesis)
        session.commit()

        with patch("httpx.post", return_value=respuesta_mock(422, "campo faltante")):
            stats = entregar_pendientes(session)

        assert stats["agotadas"] == 1
        assert webhook_delivery.enviar_alerta.called

    def test_no_vuelve_a_avisar_por_una_que_ya_estaba_agotada(
        self, session: Session, sintesis: Sintesis
    ):
        """
        Antes se avisaba por todas las trabadas en cada barrido: una sola
        síntesis mandaba un mail por hora para siempre, y el aviso que hay que
        leer terminaba perdido entre los que no.
        """
        sintesis.intentos_envio = settings.WEBHOOK_MAX_INTENTOS
        session.add(sintesis)
        session.commit()

        stats = entregar_pendientes(session)

        assert stats["agotadas"] == 0
        assert not webhook_delivery.enviar_alerta.called
        # Pero el operador la sigue viendo en las estadísticas.
        assert stats["agotadas_total"] == 1

    def test_sin_configurar_no_hace_nada_y_no_falla(self, session: Session, sintesis: Sintesis):
        """
        En desarrollo el webhook todavía no existe. Hacer fallar el paso
        convertiría en ruido la alerta del pipeline.
        """
        guardar_url(session, None)
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


class TestBarridoNoEscala:
    def _crear_pendiente(self, session: Session, medios: list, etiqueta: str) -> Sintesis:
        cluster = Cluster(titulo_evento=f"Hecho {etiqueta}", estado="abierto")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        noticias = []
        for numero, medio in enumerate(medios, start=1):
            noticia = Noticia(
                medio_id=medio.id,
                cluster_id=cluster.id,
                titulo=f"Titular {etiqueta}-{numero}",
                url=f"https://test.com/{etiqueta}-{numero}",
                guid=f"guid-{etiqueta}-{numero}",
                contenido_limpio="Cuerpo de la nota.",
                fecha_publicacion=datetime(2026, 8, 9, 10, numero, 0),
            )
            session.add(noticia)
            noticias.append(noticia)
        session.commit()

        item = Sintesis(
            cluster_id=cluster.id,
            titulo_angulo=f"Ángulo {etiqueta}",
            resumen_neutro="Pasó algo.",
            comparativa_enfoques={},
            fecha_generacion=datetime(2026, 8, 9, 12, 0, 0),
        )
        item.noticias = noticias
        session.add(item)
        session.commit()
        session.refresh(item)

        session.add(
            PublicacionRedes(sintesis_id=item.id, resumen_redes="Copy de redes.", hashtags=["a"])
        )
        session.commit()
        return item

    def test_costo_por_sintesis_no_crece_con_el_backlog(self, session: Session, medios: list):
        """
        Regresión de un N+1 encontrado en una corrida real con 192 pendientes:
        `entregar_sintesis` comitea por síntesis a propósito (el intento tiene
        que quedar contado aunque el proceso se caiga a mitad del barrido),
        pero ese `commit()` expiraba por defecto los atributos de TODOS los
        objetos que la sesión tenía cargados, no solo el recién commiteado.
        Cada síntesis siguiente del loop, al leer o escribir un atributo propio
        (incluida `.publicacion_redes`, ya precargada con `selectinload`)
        disparaba su propia recarga -- fila completa más relaciones. Medido:
        ~780 queries de esas sobre 192 pendientes, más de un tercio del total.

        Se entrega un backlog chico y uno grande por separado (en sesiones
        del mismo test, backlogs disjuntos) y se compara el costo POR
        síntesis: si el bug volviera, el backlog grande costaría más por
        ítem que el chico en vez de mantenerse constante.
        """
        for i in range(2):
            self._crear_pendiente(session, medios, f"pocas-{i}")
        with patch("httpx.post", return_value=respuesta_mock(200)):
            with contar_queries(session) as pocas:
                entregar_pendientes(session)

        for i in range(10):
            self._crear_pendiente(session, medios, f"muchas-{i}")
        with patch("httpx.post", return_value=respuesta_mock(200)):
            with contar_queries(session) as muchas:
                entregar_pendientes(session)

        costo_por_sintesis_pocas = pocas["n"] / 2
        costo_por_sintesis_muchas = muchas["n"] / 10
        assert costo_por_sintesis_muchas <= costo_por_sintesis_pocas + 1


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


class TestBackendNoDisponible:
    """
    Punto 16: separar "el back-end no está" de "esta síntesis no le gusta".

    Lo primero es información sobre la red y no puede costar material; lo
    segundo es información sobre la fila y sí tiene que agotar.
    """

    def test_un_5xx_no_cuenta_intento(
        self, session: Session, sintesis: Sintesis, sin_espera: None
    ):
        """Un 5xx es "no puedo aceptar nada ahora", no "esta síntesis está mal"."""
        with patch("httpx.post", return_value=respuesta_mock(503)):
            with pytest.raises(BackendNoDisponible):
                entregar_sintesis(session, sintesis)

        assert sintesis.intentos_envio == 0

    def test_un_429_no_cuenta_intento(
        self, session: Session, sintesis: Sintesis, sin_espera: None
    ):
        """
        Está, pero pide que pares. Insistir con las otras 31 es lo contrario de
        lo que corresponde, y del contenido no dice nada.
        """
        with patch("httpx.post", return_value=respuesta_mock(429)):
            with pytest.raises(BackendNoDisponible):
                entregar_sintesis(session, sintesis)

        assert sintesis.intentos_envio == 0

    def test_el_barrido_corta_en_la_primera(
        self, session: Session, sintesis: Sintesis, sin_espera: None
    ):
        """
        No tiene sentido intentar las otras contra un servidor que no contesta.
        Es lo que hacía que `POST /deliver` colgara 60 segundos.
        """
        for i in range(4):
            session.add(
                Sintesis(
                    cluster_id=sintesis.cluster_id,
                    titulo_angulo=f"Ángulo {i}",
                    resumen_neutro="x",
                )
            )
        session.commit()

        with patch("httpx.post", side_effect=httpx.ConnectError("sin conexión")) as post:
            stats = entregar_pendientes(session)

        # Los 3 intentos de tenacity sobre UNA sola síntesis, no sobre las cinco.
        assert post.call_count == 3
        assert stats["backend_no_disponible"] is True

    def test_un_4xx_no_corta_el_barrido(self, session: Session, sintesis: Sintesis):
        """Un rechazo del contenido es de esa fila sola: las demás siguen."""
        otra = Sintesis(
            cluster_id=sintesis.cluster_id, titulo_angulo="Otra", resumen_neutro="x"
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
        assert stats["backend_no_disponible"] is False

    def test_diez_barridos_con_el_backend_caido_no_queman_nada(
        self, session: Session, sintesis: Sintesis, sin_espera: None
    ):
        """
        **El test que codifica el punto 16.**

        Con `WEBHOOK_MAX_INTENTOS = 5`, el código anterior sacaba esta síntesis
        del barrido en la quinta corrida y no la reintentaba nunca más. Diez
        barridos son 150 minutos de caída: el doble del umbral viejo.
        """
        with patch("httpx.post", side_effect=httpx.ConnectError("sin conexión")):
            for _ in range(10):
                entregar_pendientes(session)

        session.refresh(sintesis)
        assert sintesis.intentos_envio == 0
        assert sintesis.enviado_backend is False
        # Lo que importa: sigue estando en la cola del barrido siguiente.
        assert sintesis.id in [s.id for s in sintesis_pendientes(session)]

    def test_deja_registrado_el_evento(
        self, session: Session, sintesis: Sintesis, sin_espera: None, monkeypatch
    ):
        """
        El panel de Problemas es el canal que sabemos que funciona.

        Se verifica la llamada y no la fila: `conftest` neutraliza
        `registrar_sin_romper` a propósito, porque abre su propia sesión y
        escribiría en la base real desde la suite.
        """
        registrados = []
        monkeypatch.setattr(
            eventos, "registrar_sin_romper", lambda **kw: registrados.append(kw)
        )

        with patch("httpx.post", side_effect=httpx.ConnectError("sin conexión")):
            entregar_pendientes(session)

        assert [r["clave"] for r in registrados] == ["entrega:backend_no_disponible"]

    def _corrida_caida(self, session: Session) -> None:
        """Una corrida ya cerrada que reportó no haber alcanzado al back-end."""
        from src.models import Corrida
        from src.services.corridas import PASO_ENTREGA

        session.add(
            Corrida(
                fin=ahora_utc(),
                pasos={PASO_ENTREGA: {"backend_no_disponible": True}},
            )
        )
        session.commit()

    def test_avisa_al_alcanzar_el_umbral_y_no_antes(
        self, session: Session, sintesis: Sintesis, sin_espera: None
    ):
        """
        La racha se lee del historial de corridas, así que no hace falta estado
        nuevo: con tres caídas previas, ésta es la cuarta y toca el umbral.
        """
        for _ in range(settings.WEBHOOK_CORRIDAS_ANTES_DE_AVISAR - 1):
            self._corrida_caida(session)

        with patch("httpx.post", side_effect=httpx.ConnectError("sin conexión")):
            entregar_pendientes(session)

        claves = [
            llamada.kwargs.get("clave")
            for llamada in webhook_delivery.enviar_alerta.call_args_list
        ]
        assert "webhook:backend_caido" in claves

    def test_no_avisa_dos_veces_en_el_mismo_episodio(
        self, session: Session, sintesis: Sintesis, sin_espera: None
    ):
        """
        **El punto de la opción B.** Con la racha ya pasada del umbral, la
        corrida siguiente registra el evento pero no vuelve a mandar mail: se
        compara por igualdad, no por "mayor o igual".
        """
        for _ in range(settings.WEBHOOK_CORRIDAS_ANTES_DE_AVISAR + 2):
            self._corrida_caida(session)

        with patch("httpx.post", side_effect=httpx.ConnectError("sin conexión")):
            entregar_pendientes(session)

        claves = [
            llamada.kwargs.get("clave")
            for llamada in webhook_delivery.enviar_alerta.call_args_list
        ]
        assert "webhook:backend_caido" not in claves

    def test_una_entrega_exitosa_reinicia_la_racha(self, session: Session):
        """
        Sin esto haría falta resetear un contador a mano. La racha se corta en
        la primera corrida que no reportó caída, así que se reinicia sola.
        """
        from src.models import Corrida
        from src.services import corridas
        from src.services.corridas import PASO_ENTREGA

        for _ in range(3):
            self._corrida_caida(session)
        session.add(
            Corrida(fin=ahora_utc(), pasos={PASO_ENTREGA: {"entregadas": 2}})
        )
        session.commit()
        for _ in range(2):
            self._corrida_caida(session)

        assert corridas.corridas_seguidas_sin_backend(session) == 2
