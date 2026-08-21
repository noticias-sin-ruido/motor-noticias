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
from src.models import Cluster, Medio, Noticia, PublicacionRedes, Sintesis
from src.services import synthesis
from src.services.synthesis import (
    TWEET_LIMITE,
    TWEET_MIN_HASHTAGS,
    AnguloGenerado,
    EnfoqueMedio,
    RespuestaSintesis,
    SintesisBloqueada,
    ajustar_a_tweet,
    peso_tweet,
    peso_x,
)
from src.services.topicos import Subtopico, Topico
from tests.conftest import contar_queries


@pytest.fixture
def medios(session: Session) -> list:
    creados = []
    for nombre in ["La Nación", "TN", "Ciudad"]:
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


def angulo(
    titulo="El hecho",
    notas=(1, 2),
    id_existente=None,
    topicos=(Topico.SOCIEDAD,),
    subtopicos=(),
    voces=("TN", "La Nación"),
    relevancia_social=False,
    resumen_redes=None,
    hashtags=(),
) -> AnguloGenerado:
    """
    Un ángulo publicable por defecto.

    `voces` son los medios que aparecen en la comparativa, y por defecto son dos
    porque un ángulo con una sola voz **no se publica**: el filtro exige el
    mínimo de medios tanto en las noticias como en la comparativa escrita.
    Pasarle un solo medio es la forma de probar ese descarte.

    `relevancia_social` en `False` por defecto: la mayoría de los ángulos no
    va a redes, y así los tests que no la mencionan no crean una fila de
    `PublicacionRedes` sin querer.
    """
    return AnguloGenerado(
        id_existente=id_existente,
        titulo_angulo=titulo,
        resumen_neutro="Pasó algo, contado sin adjetivos.",
        puntos_clave=["Un hecho verificado"],
        topicos=list(topicos),
        subtopicos=list(subtopicos),
        relevancia_social=relevancia_social,
        resumen_redes=resumen_redes,
        hashtags=list(hashtags),
        comparativa_enfoques=[
            EnfoqueMedio(medio=m, destaco="X", omitio="Y", cita="una frase")
            for m in voces
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
            hours=settings.HORAS_MAXIMAS_SIN_SINTETIZAR + 1
        )
        session.add(cluster)
        session.commit()
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)

        assert synthesis.clusters_pendientes(session) == []

    def test_el_plazo_esta_desacoplado_de_la_ventana_del_cluster(
        self, session: Session, medios
    ):
        """
        Antes el corte era `HORAS_CLUSTER_ABIERTO * 2` = 24 h, y ese
        acoplamiento no tenía razón de ser. Un cluster de 30 h ya no se pierde:
        una caída de fin de semana largo son ~60 h.
        """
        cluster = crear_cluster(session)
        cluster.fecha_creacion = datetime.utcnow() - timedelta(hours=30)
        session.add(cluster)
        session.commit()
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)

        assert [c.id for c in synthesis.clusters_pendientes(session)] == [cluster.id]


class TestVencidosSinSintetizar:
    """
    Que un cluster viejo deje de ser candidato está bien; que desaparezca sin
    que nadie se entere, no. Medido: 30 clusters publicables con 85 notas se
    perdieron así, todos sin haberse intentado una sola vez.
    """

    def _vencido(self, session, medios, n_medios=2):
        cluster = crear_cluster(session)
        cluster.fecha_creacion = datetime.utcnow() - timedelta(
            hours=settings.HORAS_MAXIMAS_SIN_SINTETIZAR + 1
        )
        session.add(cluster)
        session.commit()
        for i in range(n_medios):
            crear_noticia(session, medios[i], i + 1, cluster)
        return cluster

    def test_denuncia_el_que_podria_haber_publicado(self, session: Session, medios):
        cluster = self._vencido(session, medios)

        with patch.object(synthesis, "enviar_alerta") as alerta:
            perdidos = synthesis.descartar_vencidos_sin_sintetizar(session)

        assert perdidos == 1
        assert alerta.called
        assert str(cluster.id) in alerta.call_args.kwargs["cuerpo"]

    def test_no_denuncia_el_que_no_tenia_con_que_comparar(
        self, session: Session, medios
    ):
        """Un cluster que caduca con un solo medio no perdió ninguna publicación."""
        self._vencido(session, medios, n_medios=1)

        with patch.object(synthesis, "enviar_alerta") as alerta:
            perdidos = synthesis.descartar_vencidos_sin_sintetizar(session)

        assert perdidos == 0
        assert not alerta.called

    def test_no_repite_el_aviso_en_la_corrida_siguiente(self, session: Session, medios):
        """
        Se les pone la marca para que sea terminal. Una alerta que se repite sin
        novedad en cada corrida es una alerta que se deja de leer.
        """
        self._vencido(session, medios)

        with patch.object(synthesis, "enviar_alerta"):
            assert synthesis.descartar_vencidos_sin_sintetizar(session) == 1

        with patch.object(synthesis, "enviar_alerta") as alerta:
            assert synthesis.descartar_vencidos_sin_sintetizar(session) == 0
        assert not alerta.called

    def test_no_toca_los_que_siguen_en_plazo(self, session: Session, medios):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)

        assert synthesis.descartar_vencidos_sin_sintetizar(session) == 0
        session.refresh(cluster)
        assert cluster.noticias_al_sintetizar is None

    def test_subir_el_plazo_los_vuelve_a_poner_en_carrera(
        self, session: Session, medios
    ):
        """
        La alerta recomienda subir `HORAS_MAXIMAS_SIN_SINTETIZAR`, así que eso
        tiene que servir de algo. Antes se los marcaba con el conteo real de
        noticias y la guarda anti-bucle los salteaba igual: la recomendación era
        mentira.
        """
        cluster = self._vencido(session, medios)
        with patch.object(synthesis, "enviar_alerta"):
            synthesis.descartar_vencidos_sin_sintetizar(session)

        session.refresh(cluster)
        assert cluster.noticias_al_sintetizar == synthesis.MARCA_CADUCADO
        assert synthesis.clusters_pendientes(session) == []

        # El operador sube el plazo, como dice el mail.
        with patch.object(settings, "HORAS_MAXIMAS_SIN_SINTETIZAR",
                          settings.HORAS_MAXIMAS_SIN_SINTETIZAR + 24):
            assert [c.id for c in synthesis.clusters_pendientes(session)] == [cluster.id]

    def test_el_aviso_no_se_lo_puede_tragar_el_cooldown(
        self, session: Session, medios
    ):
        """
        El descarte es terminal: si el cooldown silencia el mail, esa
        información no aparece nunca más. El pipeline corre cada 15 minutos y el
        cooldown es de 60.
        """
        self._vencido(session, medios)

        with patch.object(synthesis, "enviar_alerta") as alerta:
            synthesis.descartar_vencidos_sin_sintetizar(session)

        assert alerta.call_args.kwargs["ignorar_cooldown"] is True

    def test_no_toca_los_que_ya_se_intentaron(self, session: Session, medios):
        """
        Con la marca puesta ya se los miró: si no publicaron fue por criterio,
        no por caducidad.
        """
        cluster = self._vencido(session, medios)
        cluster.noticias_al_sintetizar = 2
        session.add(cluster)
        session.commit()

        with patch.object(synthesis, "enviar_alerta") as alerta:
            assert synthesis.descartar_vencidos_sin_sintetizar(session) == 0
        assert not alerta.called


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
                topicos=[Topico.SOCIEDAD],
                subtopicos=[],
                relevancia_social=False,
                comparativa_enfoques=[
                    EnfoqueMedio(medio="La Nacion", destaco="X", omitio="Y", cita="z"),
                    EnfoqueMedio(medio="TN", destaco="X", omitio="Y", cita="z"),
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
                topicos=[Topico.SOCIEDAD],
                subtopicos=[],
                relevancia_social=False,
                comparativa_enfoques=[
                    EnfoqueMedio(medio="TN", destaco="X", omitio="Y", cita="z"),
                    EnfoqueMedio(medio="La Nación", destaco="X", omitio="Y", cita="z"),
                    EnfoqueMedio(medio="Clarín", destaco="X", omitio="Y", cita="z"),
                ],
                notas=[1, 2],
            )
        ])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        assert sorted(guardada.comparativa_enfoques) == ["La Nación", "TN"]

    def test_si_al_sacar_al_inventado_queda_una_sola_voz_no_se_publica(
        self, session: Session, medios
    ):
        """
        Consecuencia de combinar los dos filtros, y es la correcta: si una de
        las dos voces era inventada, no había dos voces.
        """
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        respuesta = RespuestaSintesis(angulos=[
            AnguloGenerado(
                titulo_angulo="El hecho",
                resumen_neutro="...",
                puntos_clave=[],
                topicos=[Topico.SOCIEDAD],
                subtopicos=[],
                relevancia_social=False,
                comparativa_enfoques=[
                    EnfoqueMedio(medio="TN", destaco="X", omitio="Y", cita="z"),
                    EnfoqueMedio(medio="Clarín", destaco="X", omitio="Y", cita="z"),
                ],
                notas=[1, 2],
            )
        ])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            stats = synthesis.sintetizar_cluster(session, cluster)

        assert stats["descartados"] == 1
        assert session.exec(select(Sintesis)).all() == []


class TestComparativaCompleta:
    """
    Una publicación que dice comparar y muestra una sola voz no es el producto.
    Medido en una corrida real: dos ángulos tenían notas de La Nación y El
    Cronista —así que pasaban el filtro de noticias— pero el modelo escribió una
    sola entrada de comparativa, y salían igual.
    """

    def _cluster_de_dos_medios(self, session, medios):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        return cluster

    def test_descarta_el_angulo_con_una_sola_voz(self, session: Session, medios):
        cluster = self._cluster_de_dos_medios(session, medios)
        # Notas de dos medios, pero el modelo describe uno solo.
        respuesta = RespuestaSintesis(angulos=[angulo(notas=(1, 2), voces=("TN",))])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            stats = synthesis.sintetizar_cluster(session, cluster)

        assert stats["creados"] == 0
        assert stats["descartados"] == 1
        assert session.exec(select(Sintesis)).all() == []

    def test_publica_cuando_estan_las_dos_voces(self, session: Session, medios):
        cluster = self._cluster_de_dos_medios(session, medios)
        respuesta = RespuestaSintesis(angulos=[angulo(notas=(1, 2))])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            stats = synthesis.sintetizar_cluster(session, cluster)

        assert stats["creados"] == 1
        guardada = session.exec(select(Sintesis)).one()
        assert sorted(guardada.comparativa_enfoques) == ["La Nación", "TN"]

    def test_descarta_al_medio_que_no_aporto_notas_a_ese_angulo(
        self, session: Session, medios
    ):
        """
        La comparativa se valida contra los medios de ESTE ángulo, no contra los
        del cluster. Con el alcance amplio, un ángulo podía publicarse
        describiendo a un medio que no aparece en sus `fuentes`: un enfoque sin
        una sola nota que lo respalde.
        """
        cluster = self._cluster_de_dos_medios(session, medios)   # La Nación + TN
        crear_noticia(session, medios[2], 3, cluster)            # Ciudad, en el cluster

        # El ángulo se apoya solo en las notas 1 y 2, pero el modelo describe a
        # Ciudad, que está en el cluster pero no en este ángulo.
        respuesta = RespuestaSintesis(
            angulos=[angulo(notas=(1, 2), voces=("TN", "La Nación", "Ciudad"))]
        )

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        medios_de_las_notas = {m.nombre for m in
                               [session.get(Medio, n.medio_id) for n in guardada.noticias]}
        assert set(guardada.comparativa_enfoques) <= medios_de_las_notas

    def test_una_resintesis_no_le_quita_medios_a_lo_ya_publicado(
        self, session: Session, medios
    ):
        """
        specs/webhook_contract.md le promete al back-end que la comparativa suma
        medios y no los quita. Antes se pisaba entera, así que una re-síntesis
        podía degradar un ángulo publicado de dos voces a una — peor que no
        haberlo publicado.
        """
        cluster = self._cluster_de_dos_medios(session, medios)
        n3 = crear_noticia(session, medios[1], 3, cluster)
        publicada = Sintesis(
            cluster_id=cluster.id,
            titulo_angulo="El hecho",
            resumen_neutro="Original.",
            comparativa_enfoques={
                "TN": {"destaco": "X", "omitio": "Y", "cita": "z"},
                "La Nación": {"destaco": "A", "omitio": "B", "cita": "c"},
            },
            enviado_backend=True,
        )
        publicada.noticias = [n3]
        session.add(publicada)
        session.commit()
        session.refresh(publicada)

        # El modelo vuelve a describir solo a TN.
        respuesta = RespuestaSintesis(
            angulos=[angulo(notas=(1, 2), id_existente=publicada.id, voces=("TN",))]
        )
        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            stats = synthesis.sintetizar_cluster(session, cluster)

        assert stats["actualizados"] == 1
        session.refresh(publicada)
        assert sorted(publicada.comparativa_enfoques) == ["La Nación", "TN"]
        # Y la entrada de TN sí se actualiza con lo nuevo.
        assert publicada.comparativa_enfoques["TN"]["cita"] == "una frase"


class TestTopico:
    """
    Tópicos y subtópicos son lo que le permite al back-end armar secciones y
    filtros. Los elige el modelo de las listas cerradas; acá se prueba cómo se
    guardan y cómo se completa la jerarquía.
    """

    def test_guarda_el_topico_del_angulo(self, session: Session, medios):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        respuesta = RespuestaSintesis(angulos=[angulo(topicos=[Topico.DEPORTES])])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        assert guardada.topicos == ["deportes"]
        assert guardada.subtopicos == []

    def test_guarda_dos_topicos_pares(self, session: Session, medios):
        """
        La muerte del padre de Messi la publicaron TN en deportes y Paparazzi en
        espectáculos: con un solo tópico desaparecería de una de las secciones.
        Son dos categorías con el mismo derecho, no una principal y otra
        secundaria.
        """
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        respuesta = RespuestaSintesis(
            angulos=[angulo(topicos=[Topico.DEPORTES, Topico.ESPECTACULOS])]
        )

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        assert guardada.topicos == ["deportes", "espectaculos"]

    def test_subtopicos_vacios_se_guardan_como_lista_vacia(
        self, session: Session, medios
    ):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        respuesta = RespuestaSintesis(angulos=[angulo(subtopicos=[])])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        assert guardada.subtopicos == []

    def test_guarda_el_subtopico_y_su_padre(self, session: Session, medios):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        respuesta = RespuestaSintesis(
            angulos=[angulo(topicos=[Topico.DEPORTES], subtopicos=[Subtopico.FUTBOL])]
        )

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        assert guardada.topicos == ["deportes"]
        assert guardada.subtopicos == ["futbol"]

    def test_agrega_el_padre_del_subtopico_si_el_modelo_no_lo_incluyo(
        self, session: Session, medios
    ):
        """
        La garantía mecánica que motivó el rediseño: el modelo puede elegir un
        subtópico sin haber incluido su categoría entre los tópicos, y el
        código lo completa -- no depende de que el modelo lo haga bien.
        """
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        respuesta = RespuestaSintesis(
            angulos=[angulo(topicos=[Topico.ECONOMIA], subtopicos=[Subtopico.FUTBOL])]
        )

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        assert set(guardada.topicos) == {"economia", "deportes"}
        assert guardada.subtopicos == ["futbol"]


class TestAjusteATweet:
    """
    El copy tiene que entrar en un posteo de X (280) junto con los hashtags y
    la URL a la nota. El `response_schema` no puede expresar "la suma de estos
    dos campos más una URL no pasa de 280", así que lo garantiza el código.
    """

    def test_las_tildes_y_la_enie_pesan_uno(self):
        """Están en el rango 0-4351 de X. Si pesaran 2, el presupuesto real
        sería mucho más chico de lo calculado."""
        assert peso_x("señor") == 5
        assert peso_x("La inesperada falla eléctrica") == 29

    def test_un_emoji_pesa_dos(self):
        assert peso_x("🔴") == 2

    def test_la_url_entra_por_veintitres_sin_importar_su_largo(self):
        """X envuelve toda URL en t.co. El peso del tweet no puede depender
        del largo de la URL del back-end."""
        corto = peso_tweet("Un gancho corto.", ["futbol"])
        # La URL nunca aparece en los argumentos: siempre se cuenta como 23.
        assert corto == peso_x("Un gancho corto.") + peso_x("#futbol") + 3 + 23

    def test_un_gancho_normal_no_se_toca(self):
        gancho = "La inesperada falla eléctrica durante el partido del equipo del Chiqui Tapia"
        tags = ["barracascentral", "futbolargentino"]

        resultado, hashtags = ajustar_a_tweet(gancho, tags)

        assert resultado == gancho
        assert hashtags == tags

    def test_saca_hashtags_antes_de_tocar_el_texto(self):
        """El texto es la información; los hashtags son decoración."""
        gancho = "x" * 200
        tags = ["unhashtagbastantelargo", "otrohashtaglargo", "terceronolargo", "cuarto"]

        resultado, hashtags = ajustar_a_tweet(gancho, tags)

        assert resultado == gancho, "no debería haber recortado el texto"
        assert len(hashtags) < len(tags)
        assert peso_tweet(resultado, hashtags) <= TWEET_LIMITE

    def test_no_baja_del_minimo_de_hashtags_que_promete_el_contrato(self):
        gancho = "y" * 240
        tags = ["unhashtagbastantelargo", "otrohashtaglargo", "terceronolargo"]

        resultado, hashtags = ajustar_a_tweet(gancho, tags)

        assert len(hashtags) == TWEET_MIN_HASHTAGS
        # Como no pudo seguir sacando hashtags, recortó el texto.
        assert resultado != gancho
        assert peso_tweet(resultado, hashtags) <= TWEET_LIMITE

    def test_recorta_en_borde_de_palabra_y_no_a_mitad(self):
        gancho = ("palabra " * 40).strip()

        resultado, _ = ajustar_a_tweet(gancho, ["uno", "dos"])

        assert resultado.endswith("…")
        assert "palabr…" not in resultado, "cortó una palabra al medio"

    def test_hashtags_desproporcionados_se_van_todos(self):
        """Antes que devolver un texto mutilado para hacerles lugar."""
        gancho = "Un gancho que vale más que los hashtags."
        tags = ["h" * 130, "i" * 130]

        resultado, hashtags = ajustar_a_tweet(gancho, tags)

        assert hashtags == []
        assert resultado == gancho
        assert peso_tweet(resultado, hashtags) <= TWEET_LIMITE

    def test_siempre_entra_en_el_limite(self):
        """La garantía, sobre casos variados."""
        casos = [
            ("z" * 300, ["a", "b", "c", "d", "e"]),
            ("z" * 254, []),
            ("Corto.", []),
            ("Corto.", ["x" * 200]),
            ("ñ" * 250, ["ñ" * 30, "á" * 30]),
        ]
        for gancho, tags in casos:
            resultado, hashtags = ajustar_a_tweet(gancho, tags)
            assert peso_tweet(resultado, hashtags) <= TWEET_LIMITE, (gancho[:20], tags)


class TestPublicacionRedes:
    """
    Copy para redes sociales: lo genera Gemini en la misma llamada que el
    resto del ángulo, pero solo para el subconjunto que marca de relevancia
    nacional (`AnguloGenerado.relevancia_social`). No hay forma de expresar
    esa condición en el `response_schema` estructurado, así que `_persistir`
    la refuerza en código -- eso es lo que se prueba acá.
    """

    def test_guarda_el_copy_ya_ajustado_al_tweet(self, session: Session, medios):
        """
        Se guarda listo para publicar, no crudo: si el recorte quedara del
        lado del back-end, tendrían que cortar sin saber qué sobra.
        """
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        respuesta = RespuestaSintesis(angulos=[
            angulo(
                relevancia_social=True,
                resumen_redes="w" * 235,
                hashtags=["unhashtaglargo", "otrohashtaglargo", "tercerohashtag"],
            )
        ])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        redes = guardada.publicacion_redes
        assert peso_tweet(redes.resumen_redes, redes.hashtags) <= TWEET_LIMITE

    def test_no_crea_fila_si_no_es_relevante(self, session: Session, medios):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        respuesta = RespuestaSintesis(angulos=[angulo(relevancia_social=False)])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        assert guardada.publicacion_redes is None

    def test_crea_la_fila_si_es_relevante(self, session: Session, medios):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        respuesta = RespuestaSintesis(angulos=[
            angulo(
                relevancia_social=True,
                resumen_redes="Un párrafo corto para redes.",
                hashtags=["messi", "futbol"],
            )
        ])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        assert guardada.publicacion_redes is not None
        assert guardada.publicacion_redes.resumen_redes == "Un párrafo corto para redes."
        assert guardada.publicacion_redes.hashtags == ["messi", "futbol"]

    def test_ignora_relevancia_social_sin_resumen(self, session: Session, medios):
        """
        Safety net: si el modelo marca relevante pero no respetó la
        instrucción de completar el resumen, no se crea una fila vacía.
        """
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        respuesta = RespuestaSintesis(
            angulos=[angulo(relevancia_social=True, resumen_redes=None)]
        )

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        guardada = session.exec(select(Sintesis)).one()
        assert guardada.publicacion_redes is None

    def test_una_resintesis_actualiza_el_contenido(self, session: Session, medios):
        cluster = crear_cluster(session)
        n1 = crear_noticia(session, medios[0], 1, cluster)
        n2 = crear_noticia(session, medios[1], 2, cluster)
        publicada = Sintesis(
            cluster_id=cluster.id, titulo_angulo="El hecho", resumen_neutro="x"
        )
        publicada.noticias = [n1, n2]
        publicada.publicacion_redes = PublicacionRedes(
            resumen_redes="Viejo.", hashtags=["viejo"]
        )
        session.add(publicada)
        session.commit()
        session.refresh(publicada)
        cluster.noticias_al_sintetizar = 2
        session.add(cluster)
        session.commit()
        crear_noticia(session, medios[2], 3, cluster)

        respuesta = RespuestaSintesis(angulos=[
            angulo(
                notas=(1, 2, 3),
                id_existente=publicada.id,
                relevancia_social=True,
                resumen_redes="Nuevo.",
                hashtags=["nuevo"],
            )
        ])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        session.refresh(publicada)
        assert publicada.publicacion_redes.resumen_redes == "Nuevo."
        assert publicada.publicacion_redes.hashtags == ["nuevo"]

    def test_una_resintesis_no_relevante_no_retracta_lo_existente(
        self, session: Session, medios
    ):
        """
        Si una resíntesis posterior deja de marcar relevancia_social, la fila
        ya generada se deja como está: no se retracta un copy que puede estar
        ya publicado en redes. Mismo criterio que "el motor nunca retracta
        una publicación entregada" (specs/webhook_contract.md, punto 9).
        """
        cluster = crear_cluster(session)
        n1 = crear_noticia(session, medios[0], 1, cluster)
        n2 = crear_noticia(session, medios[1], 2, cluster)
        publicada = Sintesis(
            cluster_id=cluster.id, titulo_angulo="El hecho", resumen_neutro="x"
        )
        publicada.noticias = [n1, n2]
        publicada.publicacion_redes = PublicacionRedes(
            resumen_redes="Ya publicado.", hashtags=["a"]
        )
        session.add(publicada)
        session.commit()
        session.refresh(publicada)
        cluster.noticias_al_sintetizar = 2
        session.add(cluster)
        session.commit()
        crear_noticia(session, medios[2], 3, cluster)

        respuesta = RespuestaSintesis(angulos=[
            angulo(notas=(1, 2, 3), id_existente=publicada.id, relevancia_social=False)
        ])

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        session.refresh(publicada)
        assert publicada.publicacion_redes is not None
        assert publicada.publicacion_redes.resumen_redes == "Ya publicado."


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

    def test_no_le_cambia_el_topico_a_un_angulo_publicado(self, session: Session, medios):
        """
        Mover una publicación de Deportes a Espectáculos entre una entrega y la
        siguiente es el mismo problema que renombrarla: del otro lado ya está en
        una sección, con lectores encima.
        """
        cluster, original = self._con_angulo_publicado(session, medios)
        original.topicos = ["deportes"]
        session.add(original)
        session.commit()
        crear_noticia(session, medios[2], 3, cluster)
        respuesta = RespuestaSintesis(
            angulos=[angulo(notas=(1, 2, 3), id_existente=original.id, topicos=[Topico.POLITICA])]
        )

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        session.refresh(original)
        assert original.topicos == ["deportes"]

    def test_completa_el_topico_si_todavia_no_lo_tenia(self, session: Session, medios):
        """
        Las síntesis anteriores a que el campo existiera no tienen nada que
        preservar, solo un hueco que llenar.
        """
        cluster, original = self._con_angulo_publicado(session, medios)
        assert original.topicos == []
        crear_noticia(session, medios[2], 3, cluster)
        respuesta = RespuestaSintesis(
            angulos=[angulo(notas=(1, 2, 3), id_existente=original.id, topicos=[Topico.POLITICA])]
        )

        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            synthesis.sintetizar_cluster(session, cluster)

        session.refresh(original)
        assert original.topicos == ["politica"]

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
        # Las tres voces porque el orden de `enviadas` lo decide el preproceso:
        # no se sabe de antemano a qué medios pertenecen las notas 1 y 3, y la
        # comparativa se valida contra los medios de ESE ángulo.
        respuesta = RespuestaSintesis(
            angulos=[
                angulo(
                    titulo="Ángulo nuevo",
                    notas=(1, 3),
                    id_existente=9999,
                    voces=("TN", "La Nación", "Ciudad"),
                )
            ]
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
        self._preparar(session, medios)
        segundo = crear_cluster(session)
        crear_noticia(session, medios[0], 10, segundo)
        crear_noticia(session, medios[1], 11, segundo)

        respuesta = RespuestaSintesis(angulos=[angulo(notas=(1, 2))])
        llamadas = {"n": 0}

        def falla_la_primera(_prompt, _modelo=None):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                raise RuntimeError("timeout")
            return respuesta

        with patch.object(synthesis, "llamar_modelo", side_effect=falla_la_primera):
            stats = synthesis.sintetizar_pendientes(session)

        assert stats == {
            "vencidos_sin_publicar": 0,
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


class TestClustersPendientesNoEscala:
    """
    Fase 5: `clusters_pendientes` hacía una query de `Noticia` por cada
    cluster candidato (N+1) y cargaba `SintesisNoticia` entera sin filtrar.
    El número de queries no debe crecer con la cantidad de clusters.
    """

    def _cluster_con_dos_medios(self, session, medios, n_base):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], n_base, cluster)
        crear_noticia(session, medios[1], n_base + 1, cluster)
        return cluster

    def test_no_hace_una_query_por_cluster_candidato(self, session: Session, medios):
        for i in range(2):
            self._cluster_con_dos_medios(session, medios, i * 10)
        with contar_queries(session) as pocos:
            synthesis.clusters_pendientes(session)

        for i in range(10):
            self._cluster_con_dos_medios(session, medios, 100 + i * 10)
        with contar_queries(session) as muchos:
            synthesis.clusters_pendientes(session)

        assert muchos["n"] == pocos["n"]


class TestDescartarVencidosNoEscala:
    """
    Fase 5: el acceso a `c.noticias` dentro del comprehension de
    `descartar_vencidos_sin_sintetizar` era lazy-load, una query por cluster
    vencido.
    """

    def _vencido(self, session, medios, n_base):
        cluster = crear_cluster(session)
        cluster.fecha_creacion = datetime.utcnow() - timedelta(
            hours=settings.HORAS_MAXIMAS_SIN_SINTETIZAR + 1
        )
        session.add(cluster)
        session.commit()
        crear_noticia(session, medios[0], n_base, cluster)
        crear_noticia(session, medios[1], n_base + 1, cluster)
        return cluster

    def test_no_hace_una_query_por_cluster_vencido(self, session: Session, medios):
        for i in range(2):
            self._vencido(session, medios, i * 10)
        with patch.object(synthesis, "enviar_alerta"), contar_queries(session) as pocos:
            synthesis.descartar_vencidos_sin_sintetizar(session)

        for i in range(10):
            self._vencido(session, medios, 100 + i * 10)
        with patch.object(synthesis, "enviar_alerta"), contar_queries(session) as muchos:
            synthesis.descartar_vencidos_sin_sintetizar(session)

        assert muchos["n"] == pocos["n"]
