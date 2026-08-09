"""
Tests del servicio de síntesis.

Se mockea `llamar_modelo`, que es la frontera con Gemini: pegarle de verdad
costaría plata, necesitaría clave y daría respuestas distintas en cada corrida.
Lo que sí se prueba es todo lo nuestro — cuándo se dispara, el filtro por
ángulo, el congelamiento de la descomposición y el manejo de fallos.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, select

from src.config import settings
from src.models import Cluster, Medio, Noticia, Sintesis
from src.services import synthesis
from src.services.synthesis import (
    AnguloGenerado,
    EnfoqueMedio,
    RespuestaSintesis,
    SintesisBloqueada,
)


@pytest.fixture
def medios(session: Session) -> list:
    creados = []
    for nombre in ["La Nación", "TN", "Ciudad"]:
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


def crear_cluster(session: Session, marca=None, estado="abierto") -> Cluster:
    cluster = Cluster(
        titulo_evento="Un hecho",
        estado=estado,
        noticias_al_sintetizar=marca,
        fecha_creacion=datetime.utcnow() - timedelta(hours=1),
    )
    session.add(cluster)
    session.commit()
    session.refresh(cluster)
    return cluster


def crear_noticia(session: Session, medio: Medio, n: int, cluster: Cluster) -> Noticia:
    noticia = Noticia(
        medio_id=medio.id,
        cluster_id=cluster.id,
        titulo=f"Titular {n}",
        url=f"https://test.com/{n}",
        guid=f"guid-{n}",
        contenido_limpio=f"Cuerpo de la nota {n}.",
        fecha_publicacion=datetime.utcnow(),
        embedding=[0.1] * 384,
    )
    session.add(noticia)
    session.commit()
    session.refresh(noticia)
    return noticia


def angulo(titulo="El hecho", notas=(1, 2), id_existente=None) -> AnguloGenerado:
    return AnguloGenerado(
        id_existente=id_existente,
        titulo_angulo=titulo,
        resumen_neutro="Pasó algo, contado sin adjetivos.",
        puntos_clave=["Un hecho verificado"],
        comparativa_enfoques=[
            EnfoqueMedio(medio="TN", destaco="X", omitio="Y", cita="una frase")
        ],
        notas=list(notas),
    )


class TestClustersPendientes:
    def test_toma_el_cluster_nunca_sintetizado(self, session: Session, medios):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)

        assert [c.id for c in synthesis.clusters_pendientes(session)] == [cluster.id]

    def test_ignora_el_cluster_de_un_solo_medio(self, session: Session, medios):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[0], 2, cluster)

        assert synthesis.clusters_pendientes(session) == []

    def test_no_reintenta_si_no_llego_nada_nuevo(self, session: Session, medios):
        """
        La guarda contra el bucle: se intentó, ningún ángulo llegó al mínimo y
        no se creó ninguna síntesis. Sin la marca se reintentaría para siempre.
        """
        cluster = crear_cluster(session, marca=2)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)

        assert cluster.sintesis == []
        assert synthesis.clusters_pendientes(session) == []

    def test_detecta_material_nuevo_de_los_mismos_medios(self, session: Session, medios):
        """
        El caso que el conteo por medio no veía: TN y La Nación ya estaban y los
        dos publican sobre un ángulo nuevo. No entró ningún medio, pero hay
        material publicable.
        """
        cluster = crear_cluster(session, marca=2)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        crear_noticia(session, medios[0], 3, cluster)
        crear_noticia(session, medios[1], 4, cluster)

        assert [c.id for c in synthesis.clusters_pendientes(session)] == [cluster.id]

    def test_ignora_las_noticias_que_ya_tienen_angulo(self, session: Session, medios):
        cluster = crear_cluster(session, marca=1)
        n1 = crear_noticia(session, medios[0], 1, cluster)
        n2 = crear_noticia(session, medios[1], 2, cluster)
        sintesis = Sintesis(
            cluster_id=cluster.id, titulo_angulo="Ya publicado", resumen_neutro="..."
        )
        sintesis.noticias = [n1, n2]
        session.add(sintesis)
        session.commit()

        # Las dos noticias ya están cubiertas: no queda material sin ángulo.
        assert synthesis.clusters_pendientes(session) == []

    def test_ignora_los_clusters_viejos(self, session: Session, medios):
        """El recorte por fecha evita revivir noticias viejas al arrancar."""
        cluster = crear_cluster(session)
        cluster.fecha_creacion = datetime.utcnow() - timedelta(
            hours=settings.HORAS_CLUSTER_ABIERTO * 2 + 1
        )
        session.add(cluster)
        session.commit()
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)

        assert synthesis.clusters_pendientes(session) == []


class TestSintetizarCluster:
    def _preparar(self, session, medios, n_medios=2):
        cluster = crear_cluster(session)
        for i in range(n_medios):
            crear_noticia(session, medios[i], i + 1, cluster)
        return cluster

    def test_crea_un_angulo_con_sus_noticias(self, session: Session, medios):
        cluster = self._preparar(session, medios)
        respuesta = RespuestaSintesis(angulos=[angulo(notas=(1, 2))])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            stats = synthesis.sintetizar_cluster(session, cluster)

        assert stats["creados"] == 1
        guardada = session.exec(select(Sintesis)).one()
        assert guardada.titulo_angulo == "El hecho"
        assert len(guardada.noticias) == 2
        assert guardada.comparativa_enfoques["TN"]["cita"] == "una frase"

    def test_descarta_el_angulo_de_un_solo_medio(self, session: Session, medios):
        """
        El filtro va sobre el ángulo, no sobre el cluster: un cluster con varios
        medios puede contener un ángulo que cubrió uno solo.
        """
        cluster = self._preparar(session, medios)
        respuesta = RespuestaSintesis(
            angulos=[angulo(titulo="Bueno", notas=(1, 2)),
                     angulo(titulo="Una sola voz", notas=(1,))]
        )

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            stats = synthesis.sintetizar_cluster(session, cluster)

        assert stats == {"creados": 1, "actualizados": 0, "descartados": 1}
        assert session.exec(select(Sintesis)).one().titulo_angulo == "Bueno"

    def test_deja_la_marca_aunque_no_publique_nada(self, session: Session, medios):
        cluster = self._preparar(session, medios)
        respuesta = RespuestaSintesis(angulos=[angulo(notas=(1,))])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        session.refresh(cluster)
        assert cluster.sintesis == []
        assert cluster.noticias_al_sintetizar == 2
        assert synthesis.clusters_pendientes(session) == []  # no se reintenta

    def test_ignora_indices_de_notas_inventados(self, session: Session, medios):
        """El modelo puede referenciar notas que no le mandamos."""
        cluster = self._preparar(session, medios)
        respuesta = RespuestaSintesis(angulos=[angulo(notas=(1, 2, 99))])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        assert len(session.exec(select(Sintesis)).one().noticias) == 2


class TestComparativaValidada:
    """
    Los nombres de medio que devuelve el modelo se validan contra los del
    cluster, y se guardan como figuran en la base.
    """

    def test_corrige_el_nombre_sin_tilde(self, session: Session, medios):
        """
        Pasó en la primera corrida real: el modelo devolvió "La Nacion" y en la
        base el medio es "La Nación". Sin normalizar, la comparativa quedaba sin
        forma de vincularse al medio.
        """
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        respuesta = RespuestaSintesis(angulos=[
            AnguloGenerado(
                titulo_angulo="El hecho",
                resumen_neutro="...",
                puntos_clave=[],
                comparativa_enfoques=[
                    EnfoqueMedio(medio="La Nacion", destaco="X", omitio="Y", cita="z")
                ],
                notas=[1, 2],
            )
        ])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        assert "La Nación" in guardada.comparativa_enfoques
        assert "La Nacion" not in guardada.comparativa_enfoques

    def test_descarta_un_medio_que_no_esta_en_el_cluster(self, session: Session, medios):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        respuesta = RespuestaSintesis(angulos=[
            AnguloGenerado(
                titulo_angulo="El hecho",
                resumen_neutro="...",
                puntos_clave=[],
                comparativa_enfoques=[
                    EnfoqueMedio(medio="TN", destaco="X", omitio="Y", cita="z"),
                    EnfoqueMedio(medio="Clarín", destaco="X", omitio="Y", cita="z"),
                ],
                notas=[1, 2],
            )
        ])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        assert list(guardada.comparativa_enfoques) == ["TN"]


class TestDescomposicionCongelada:
    """
    Las re-síntesis actualizan o agregan ángulos, nunca los reparten de nuevo:
    del otro lado el backend tiene likes y comentarios colgando de cada id.
    """

    def _con_angulo_publicado(self, session, medios):
        cluster = crear_cluster(session)
        n1 = crear_noticia(session, medios[0], 1, cluster)
        n2 = crear_noticia(session, medios[1], 2, cluster)
        sintesis = Sintesis(
            cluster_id=cluster.id,
            titulo_angulo="El hecho",
            resumen_neutro="Versión original.",
            enviado_backend=True,
        )
        sintesis.noticias = [n1, n2]
        session.add(sintesis)
        session.commit()
        session.refresh(sintesis)
        cluster.noticias_al_sintetizar = 2
        session.add(cluster)
        session.commit()
        return cluster, sintesis

    def test_actualiza_conservando_el_id_y_el_titulo(self, session: Session, medios):
        cluster, original = self._con_angulo_publicado(session, medios)
        crear_noticia(session, medios[2], 3, cluster)
        respuesta = RespuestaSintesis(
            angulos=[angulo(titulo="OTRO TITULO", notas=(1, 2, 3), id_existente=original.id)]
        )

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            stats = synthesis.sintetizar_cluster(session, cluster)

        assert stats["actualizados"] == 1
        session.refresh(original)
        assert original.titulo_angulo == "El hecho"     # no se renombra
        assert original.resumen_neutro == "Pasó algo, contado sin adjetivos."
        assert original.enviado_backend is False        # hay que reentregarlo
        assert len(session.exec(select(Sintesis)).all()) == 1

    def test_no_le_quita_noticias_a_un_angulo_publicado(self, session: Session, medios):
        cluster, original = self._con_angulo_publicado(session, medios)
        crear_noticia(session, medios[2], 3, cluster)
        # El modelo devuelve solo la nota 3, olvidándose de las dos originales.
        respuesta = RespuestaSintesis(
            angulos=[angulo(notas=(3,), id_existente=original.id)]
        )

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        session.refresh(original)
        assert len(original.noticias) == 3  # suma, no reemplaza

    def test_un_id_inventado_se_trata_como_angulo_nuevo(self, session: Session, medios):
        cluster, original = self._con_angulo_publicado(session, medios)
        crear_noticia(session, medios[2], 3, cluster)
        respuesta = RespuestaSintesis(
            angulos=[angulo(titulo="Ángulo nuevo", notas=(1, 3), id_existente=9999)]
        )

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            stats = synthesis.sintetizar_cluster(session, cluster)

        assert stats["creados"] == 1
        assert len(session.exec(select(Sintesis)).all()) == 2


class TestManejoDeFallos:
    def _preparar(self, session, medios):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        return cluster

    def test_el_bloqueo_del_proveedor_se_cuenta_aparte(self, session: Session, medios):
        """No es un error técnico: si se repite, es una decisión de producto."""
        self._preparar(session, medios)

        with patch.object(synthesis, "llamar_modelo", side_effect=SintesisBloqueada("...")):
            stats = synthesis.sintetizar_pendientes(session)

        assert stats["bloqueados"] == 1
        assert stats["fallidos"] == 0

    def test_un_cluster_que_falla_no_arrastra_a_los_demas(self, session: Session, medios):
        primero = self._preparar(session, medios)
        segundo = crear_cluster(session)
        crear_noticia(session, medios[0], 10, segundo)
        crear_noticia(session, medios[1], 11, segundo)

        respuesta = RespuestaSintesis(angulos=[angulo(notas=(1, 2))])
        llamadas = {"n": 0}

        def falla_la_primera(_prompt):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                raise RuntimeError("timeout")
            return respuesta

        with patch.object(synthesis, "llamar_modelo", side_effect=falla_la_primera):
            stats = synthesis.sintetizar_pendientes(session)

        assert stats == {
            "pendientes": 2, "sintetizados": 1, "creados": 1,
            "actualizados": 0, "descartados": 0, "bloqueados": 0, "fallidos": 1,
        }

    def test_el_cluster_que_fallo_se_reintenta(self, session: Session, medios):
        """La marca solo se escribe cuando la síntesis llegó a persistirse."""
        cluster = self._preparar(session, medios)

        with patch.object(synthesis, "llamar_modelo", side_effect=RuntimeError("boom")):
            synthesis.sintetizar_pendientes(session)

        session.refresh(cluster)
        assert cluster.noticias_al_sintetizar is None
        assert [c.id for c in synthesis.clusters_pendientes(session)] == [cluster.id]


class TestLlamarModelo:
    """La frontera con Gemini: se mockea el cliente, no la función."""

    def _cliente_que_responde(self, texto: str, finish_reason: str = "STOP"):
        cliente = MagicMock()
        respuesta = MagicMock()
        respuesta.text = texto
        candidato = MagicMock()
        candidato.finish_reason = finish_reason
        respuesta.candidates = [candidato]
        cliente.models.generate_content.return_value = respuesta
        return cliente

    def test_manda_esquema_y_acota_el_razonamiento(self):
        """
        Los tokens de razonamiento se facturan como salida, que es ~80% del
        costo de esta fase. Se usa `thinking_level` y no `thinking_budget`:
        gemini-3.5-flash-lite rechaza `thinking_budget=0` con un 400.
        """
        cliente = self._cliente_que_responde('{"angulos": []}')

        with patch.object(synthesis, "get_cliente", return_value=cliente):
            synthesis.llamar_modelo("un prompt")

        config = cliente.models.generate_content.call_args.kwargs["config"]
        assert config["response_schema"] is RespuestaSintesis
        assert config["response_mime_type"] == "application/json"
        assert config["thinking_config"] == {
            "thinking_level": settings.GEMINI_THINKING_LEVEL
        }
        assert "thinking_budget" not in config["thinking_config"]

    def test_el_bloqueo_por_seguridad_no_se_reintenta(self):
        """Reintentar no sirve: la misma entrada da el mismo bloqueo."""
        cliente = self._cliente_que_responde("", finish_reason="SAFETY")

        with patch.object(synthesis, "get_cliente", return_value=cliente):
            with pytest.raises(SintesisBloqueada):
                synthesis.llamar_modelo("un prompt")

        assert cliente.models.generate_content.call_count == 1

    def test_una_respuesta_vacia_se_reintenta(self):
        cliente = self._cliente_que_responde("")

        with patch.object(synthesis, "get_cliente", return_value=cliente):
            with pytest.raises(ValueError):
                synthesis.llamar_modelo("un prompt")

        assert cliente.models.generate_content.call_count == 3

    def test_sin_api_key_falla_sin_reintentar(self, monkeypatch):
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "tu_api_key_aca")
        monkeypatch.setattr(synthesis, "_cliente", None)

        with pytest.raises(synthesis.SintesisSinConfigurar):
            synthesis.llamar_modelo("un prompt")


class TestConstruirPrompt:
    def test_incluye_los_cuerpos_y_los_angulos_publicados(self, session: Session, medios):
        cluster = crear_cluster(session)
        n1 = crear_noticia(session, medios[0], 1, cluster)
        n2 = crear_noticia(session, medios[1], 2, cluster)
        publicado = Sintesis(
            cluster_id=cluster.id, titulo_angulo="Ya publicado", resumen_neutro="..."
        )
        session.add(publicado)
        session.commit()
        session.refresh(publicado)

        evidencia = {
            "medios": ["La Nación", "TN"],
            "nucleo_comun": {"entidades": ["Fulano"], "terminos": ["algo"]},
            "por_medio": {"La Nación": {"terminos_propios": ["propio"],
                                        "entidades_exclusivas": [],
                                        "entidades_omitidas": []}},
        }
        prompt = synthesis.construir_prompt(
            evidencia, [n1, n2], {m.id: m.nombre for m in medios}, [publicado]
        )

        assert "Cuerpo de la nota 1." in prompt
        assert "NOTA 2 | TN" in prompt
        assert f"id={publicado.id}: Ya publicado" in prompt
        assert "NO los renombres" in prompt
        assert "PISTAS A VERIFICAR" in prompt

    def test_la_primera_sintesis_no_menciona_angulos_previos(self, session: Session, medios):
        cluster = crear_cluster(session)
        n1 = crear_noticia(session, medios[0], 1, cluster)
        evidencia = {
            "medios": ["La Nación"],
            "nucleo_comun": {"entidades": [], "terminos": []},
            "por_medio": {},
        }

        prompt = synthesis.construir_prompt(
            evidencia, [n1], {m.id: m.nombre for m in medios}, []
        )

        assert "es la primera síntesis" in prompt
        assert "id_existente` en null" in prompt
