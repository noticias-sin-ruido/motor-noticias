"""
Tests del servicio de síntesis.

Se mockea `llamar_modelo`, que es la frontera con el proveedor: pegarle de
verdad costaría plata, necesitaría clave y daría respuestas distintas en cada
corrida. Lo que sí se prueba es todo lo nuestro — cuándo se dispara, el filtro
por ángulo, el congelamiento de la descomposición y el manejo de fallos.

Cómo habla cada proveedor y cómo se elige cuál se prueba en `test_modelos.py`.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, select

from src.config import settings
from src.models import (
    Adaptador,
    Cluster,
    Medio,
    ModeloIA,
    Noticia,
    PublicacionRedes,
    Sintesis,
)
from src.services import synthesis
from src.services.proveedores import (
    PREFIJO_API_KEY_ENV,
    AdaptadorNoImplementado,
    ErrorDeProveedor,
    ProveedorNoConfigurado,
    RespuestaBloqueada,
)
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

# El modelo que resuelve `modelo_activo` en este archivo. **Desde la etapa 4 del
# punto 2 siempre tiene que haber uno**: sin fila activa la síntesis no corre,
# así que un test de persistencia que no lo provea estaría probando esa rama en
# vez de la que le interesa. Se inyecta con la fixture de abajo.
MODELO = ModeloIA(
    id=1, nombre="modelo-de-prueba", adaptador=Adaptador.GEMINI, modelo="un-modelo"
)


@pytest.fixture(autouse=True)
def modelo_configurado(session: Session):
    """
    Deja un modelo activo en la base, para todos los tests del archivo.

    **Se inserta la fila de verdad en vez de devolver un objeto suelto.**
    `sintetizar_pendientes` hace `session.expunge(modelo)` para evitar el N+1
    que provoca `expire_on_commit`, y expulsar algo que nunca estuvo adjunto
    levanta `InvalidRequestError`. Un doble que no está en la sesión obligaría a
    poner una guarda en producción para sostener una situación que en
    producción no existe.

    No vuelve ciegos a los tests de la rama contraria: los que prueban qué pasa
    **sin** proveedor parchean el seam que corresponda —`cadena_de_modelos`
    para `sintetizar_pendientes`, `modelo_activo` para `sintetizar_cluster`— y
    ese parche gana porque se aplica más adentro.
    """
    fila = ModeloIA(
        nombre="modelo-de-prueba",
        adaptador=Adaptador.GEMINI,
        modelo="un-modelo",
        activo=True,
    )
    session.add(fila)
    session.commit()
    session.refresh(fila)
    yield fila


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
            "sin_modelo": False, "sin_credencial": False,
            # El que salió bien quedó atribuido, y nadie se cayó de la cadena:
            # un fallo suelto no alcanza para agotar a un modelo.
            "por_modelo": {"modelo-de-prueba": 1}, "agotados": {},
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
    """
    La frontera con el proveedor, y **la política de reintentos**, que es lo
    único que vive de este lado.

    Cómo habla cada proveedor se prueba en `test_modelos.py`, contra su
    adaptador. Acá se mockea `construir` —o sea el adaptador entero— porque lo
    que se mide es qué hace `llamar_modelo` con lo que el adaptador le levante:
    qué traduce, qué reintenta y qué no.
    """

    def _proveedor(self, **kwargs) -> MagicMock:
        proveedor = MagicMock()
        proveedor.generar.configure_mock(**kwargs)
        return proveedor

    def test_devuelve_lo_que_da_el_adaptador(self):
        esperada = RespuestaSintesis(angulos=[])
        proveedor = self._proveedor(return_value=esperada)

        with patch.object(synthesis, "construir", return_value=proveedor):
            assert synthesis.llamar_modelo("un prompt", MODELO) is esperada

        proveedor.generar.assert_called_once_with("un prompt", RespuestaSintesis)

    def test_el_bloqueo_por_filtros_se_traduce_y_no_se_reintenta(self):
        """
        Los adaptadores hablan `RespuestaBloqueada` para no depender de este
        módulo; acá se convierte al vocabulario que entiende
        `sintetizar_pendientes`. Reintentar no sirve: la misma entrada da el
        mismo bloqueo.
        """
        proveedor = self._proveedor(side_effect=RespuestaBloqueada("filtros"))

        with patch.object(synthesis, "construir", return_value=proveedor):
            with pytest.raises(SintesisBloqueada):
                synthesis.llamar_modelo("un prompt", MODELO)

        assert proveedor.generar.call_count == 1

    def test_un_error_del_proveedor_se_reintenta(self):
        """Un 429 o un JSON mal armado se arreglan solos en el intento siguiente."""
        proveedor = self._proveedor(side_effect=ErrorDeProveedor("HTTP 429"))

        with patch.object(synthesis, "construir", return_value=proveedor):
            with pytest.raises(ValueError):
                synthesis.llamar_modelo("un prompt", MODELO)

        assert proveedor.generar.call_count == 3

    @pytest.mark.parametrize(
        "error", [ProveedorNoConfigurado("falta la key"), AdaptadorNoImplementado("no hay")]
    )
    def test_lo_que_no_se_arregla_solo_no_se_reintenta(self, error):
        """
        Sin esta rama eran tres intentos con espera creciente por cluster: con
        37 clusters, entre 2 y 4 minutos de sleeps puros por corrida, y todo
        contado como "fallido" en vez de "mal configurado".
        """
        proveedor = self._proveedor(side_effect=error)

        with patch.object(synthesis, "construir", return_value=proveedor):
            with pytest.raises(synthesis.SintesisSinConfigurar):
                synthesis.llamar_modelo("un prompt", MODELO)

        assert proveedor.generar.call_count == 1

    def test_sin_modelo_activo_no_se_sintetiza(self, session: Session, medios):
        """
        **El cambio de la etapa 4.** Antes, ningún modelo activo significaba
        "usá el camino histórico de Gemini" y el motor sintetizaba igual. Ese
        camino se borró: el motor no tiene proveedor de reserva escondido, así
        que no elegir ninguno es configuración incompleta y se dice.
        """
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)

        # `sintetizar_cluster` resuelve por su cuenta con `modelo_activo` (el
        # centinela `_RESOLVER`), no con la cadena: la cadena la arma
        # `sintetizar_pendientes`. Cada uno se parchea en su propio seam.
        with patch.object(synthesis, "modelo_activo", return_value=None):
            with pytest.raises(synthesis.SintesisSinConfigurar):
                synthesis.sintetizar_cluster(session, cluster)

    def test_la_corrida_entera_corta_una_sola_vez_sin_modelo(
        self, session: Session, medios
    ):
        """
        Se corta arriba y no cluster por cluster: dejar que cada uno lo
        descubra daba 26 excepciones con 26 tracebacks para una sola causa,
        todas contadas como "fallidas" — que sugiere un problema con los
        clusters cuando el problema es que falta configurar el motor.
        """
        for i in range(3):
            cluster = crear_cluster(session)
            crear_noticia(session, medios[0], i * 2 + 1, cluster)
            crear_noticia(session, medios[1], i * 2 + 2, cluster)

        with patch.object(synthesis, "cadena_de_modelos", return_value=[]):
            with patch.object(synthesis, "construir") as construir:
                stats = synthesis.sintetizar_pendientes(session)

        assert stats["sin_modelo"] is True
        assert stats["fallidos"] == 0, "no son clusters fallidos, es falta de config"
        assert stats["sintetizados"] == 0
        construir.assert_not_called()

    def test_sin_credencial_corta_la_corrida_en_vez_de_fallar_cluster_por_cluster(
        self, session: Session, medios
    ):
        """
        Una credencial que falta no se arregla entre un cluster y el siguiente.

        Sin la rama que corta, esto caía en el `except Exception` genérico: un
        traceback y un "fallido" **por cada cluster**, para una sola causa. Ese
        conteo apunta a un problema con los clusters cuando el problema es la
        configuración del motor — el mismo diagnóstico engañoso que ya se había
        corregido para "no hay ninguna fila activa".
        """
        for i in range(4):
            cluster = crear_cluster(session)
            crear_noticia(session, medios[0], i * 2 + 1, cluster)
            crear_noticia(session, medios[1], i * 2 + 2, cluster)

        falla = synthesis.SintesisSinConfigurar("falta MODELO_API_KEY")
        with patch.object(synthesis, "llamar_modelo", side_effect=falla) as llamar:
            stats = synthesis.sintetizar_pendientes(session)

        assert stats["sin_credencial"] is True
        assert stats["fallidos"] == 0, "no son clusters fallidos, es falta de credencial"
        assert llamar.call_count == 1, (
            f"se intentaron {llamar.call_count} clusters con la misma credencial "
            f"que ya se sabe que falta"
        )

        # Y quedan en carrera: la marca solo se escribe cuando la síntesis se
        # persiste, así que la corrida siguiente los retoma sola.
        assert len(synthesis.clusters_pendientes(session)) == 4

    def test_sin_modelo_no_caduca_clusters_ni_culpa_al_plazo(
        self, session: Session, medios
    ):
        """
        El barrido de caducados marca lo que pasó `HORAS_MAXIMAS_SIN_SINTETIZAR`
        sin sintetizarse y avisa **recomendando subir ese plazo**. Sin modelo
        configurado esa recomendación es falsa: no se sintetizó porque no había
        con qué, y subirlo no cambia nada.

        Peor: el cluster quedaría marcado como caducado por una causa que el
        motor tiene identificada treinta líneas más abajo, en
        `stats["sin_modelo"]`.
        """
        viejo = crear_cluster(session)
        viejo.fecha_creacion = datetime.now() - timedelta(
            hours=settings.HORAS_MAXIMAS_SIN_SINTETIZAR + 1
        )
        crear_noticia(session, medios[0], 1, viejo)
        crear_noticia(session, medios[1], 2, viejo)
        session.add(viejo)
        session.commit()

        with patch.object(synthesis, "cadena_de_modelos", return_value=[]):
            with patch.object(synthesis, "enviar_alerta") as alerta:
                stats = synthesis.sintetizar_pendientes(session)

        assert stats["sin_modelo"] is True
        assert stats["vencidos_sin_publicar"] == 0
        alerta.assert_not_called()

        # Y el cluster sigue intacto: entra en carrera solo en cuanto haya un
        # modelo prendido, sin necesidad de tocar el plazo.
        session.refresh(viejo)
        assert viejo.noticias_al_sintetizar is None


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


class TestCadenaDeFallback:
    """
    Multimodelo (punto 6-bis): si el titular falla, el cluster se reintenta con
    el suplente en vez de perderse. Ver `synthesis._intentar_con_la_cadena`.
    """

    @pytest.fixture
    def suplente(self, session: Session, monkeypatch) -> ModeloIA:
        """
        Un segundo modelo **con credencial propia**, que es lo que lo hace
        entrar a la cadena. Compartir la variable del titular sería compartir su
        cuota, y entonces no serviría de suplente.
        """
        variable = f"{PREFIJO_API_KEY_ENV}SUPLENTE"
        monkeypatch.setenv(variable, "clave-del-suplente")
        fila = ModeloIA(
            nombre="suplente",
            adaptador=Adaptador.GEMINI,
            modelo="otro-modelo",
            activo=False,
            api_key_env=variable,
        )
        session.add(fila)
        session.commit()
        session.refresh(fila)
        return fila

    def _un_cluster(self, session, medios, n=1):
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], n, cluster)
        crear_noticia(session, medios[1], n + 1, cluster)
        return cluster

    def _resultado(self):
        return {"creados": 1, "actualizados": 0, "descartados": 0}

    def _por_modelo(self, reglas):
        """
        Reemplaza a `sintetizar_cluster` decidiendo por el nombre del modelo.

        `reglas` mapea nombre -> excepción a levantar; los que no están salen
        bien. Deja registrado el orden real de intentos, que es lo que estos
        tests miran.
        """
        intentos = []

        def falso(_session, _cluster, modelo):
            intentos.append(modelo.nombre)
            problema = reglas.get(modelo.nombre)
            if problema is not None:
                raise problema
            return self._resultado()

        return falso, intentos

    def test_el_fallo_del_titular_cae_al_suplente(self, session, medios, suplente):
        self._un_cluster(session, medios)
        falso, intentos = self._por_modelo(
            {"modelo-de-prueba": RuntimeError("429 rate limit")}
        )

        with patch.object(synthesis, "sintetizar_cluster", side_effect=falso):
            stats = synthesis.sintetizar_pendientes(session)

        assert intentos == ["modelo-de-prueba", "suplente"]
        assert stats["sintetizados"] == 1
        assert stats["fallidos"] == 0
        assert stats["por_modelo"] == {"suplente": 1}

    def test_sin_configurar_tambien_cae_al_suplente(self, session, medios, suplente):
        """
        Hasta multimodelo esto cortaba la corrida entera, y era lo correcto con
        un solo proveedor: una credencial que falta no se arregla entre un
        cluster y el siguiente. Con cadena significa "usá el que sigue".
        """
        self._un_cluster(session, medios)
        falso, intentos = self._por_modelo(
            {"modelo-de-prueba": synthesis.SintesisSinConfigurar("falta la key")}
        )

        with patch.object(synthesis, "sintetizar_cluster", side_effect=falso):
            stats = synthesis.sintetizar_pendientes(session)

        assert intentos == ["modelo-de-prueba", "suplente"]
        assert stats["sintetizados"] == 1
        assert stats["sin_credencial"] is False

    def test_el_bloqueo_de_contenido_no_se_prueba_con_otro(
        self, session, medios, suplente
    ):
        """
        **La excepción deliberada.** El proveedor rechazó el contenido por sus
        filtros; buscar otro que sí lo acepte es rodear una negativa de
        seguridad, y destruye la señal de que el producto no puede cubrir cierto
        material — que es una decisión de producto, no un fallo a sortear.
        """
        self._un_cluster(session, medios)
        falso, intentos = self._por_modelo(
            {"modelo-de-prueba": synthesis.SintesisBloqueada("policiales")}
        )

        with patch.object(synthesis, "sintetizar_cluster", side_effect=falso):
            stats = synthesis.sintetizar_pendientes(session)

        assert intentos == ["modelo-de-prueba"], "no tenía que probar el suplente"
        assert stats["bloqueados"] == 1
        assert stats["sintetizados"] == 0

    def test_dos_fallos_seguidos_sacan_al_titular_de_la_cadena(
        self, session, medios, suplente
    ):
        """
        **El cortocircuito.** Sin esto, con la cuota del titular agotada cada
        cluster paga sus 3 reintentos de `tenacity` antes de caer al suplente.
        Del tercer cluster en adelante el titular ya no se intenta.
        """
        for i in range(3):
            self._un_cluster(session, medios, n=i * 2 + 1)
        falso, intentos = self._por_modelo(
            {"modelo-de-prueba": RuntimeError("429 rate limit")}
        )

        with patch.object(synthesis, "sintetizar_cluster", side_effect=falso):
            stats = synthesis.sintetizar_pendientes(session)

        assert intentos == [
            "modelo-de-prueba", "suplente",
            "modelo-de-prueba", "suplente",
            "suplente",
        ]
        assert "modelo-de-prueba" in stats["agotados"]
        assert stats["sintetizados"] == 3
        assert stats["por_modelo"] == {"suplente": 3}

    def test_un_exito_corta_la_racha(self, session, medios, suplente):
        """
        Dos fallos separados por un éxito no son "seguidos". Sin el reset, un
        modelo sano se caería de la cadena por dos casualidades lejanas.
        """
        for i in range(3):
            self._un_cluster(session, medios, n=i * 2 + 1)

        intentos = []
        vueltas = {"n": 0}

        def falso(_session, _cluster, modelo):
            intentos.append(modelo.nombre)
            if modelo.nombre == "modelo-de-prueba":
                vueltas["n"] += 1
                # Falla en el 1er y 3er intento suyo, funciona en el 2do.
                if vueltas["n"] != 2:
                    raise RuntimeError("timeout")
            return self._resultado()

        with patch.object(synthesis, "sintetizar_cluster", side_effect=falso):
            stats = synthesis.sintetizar_pendientes(session)

        assert stats["agotados"] == {}, "el éxito del medio corta la racha"
        assert intentos.count("modelo-de-prueba") == 3

    def test_un_modelo_explicito_no_cae_al_suplente(self, session, medios, suplente):
        """
        Elegir un modelo es elegirlo: caer en silencio a otro dejaría en
        `modelo_usado` una serie histórica que dice que se usó uno que nadie
        pidió, que es justo la comparación que esa columna existe para habilitar.
        """
        self._un_cluster(session, medios)
        titular = session.exec(
            select(ModeloIA).where(ModeloIA.nombre == "modelo-de-prueba")
        ).one()
        falso, intentos = self._por_modelo(
            {"modelo-de-prueba": RuntimeError("429 rate limit")}
        )

        with patch.object(synthesis, "sintetizar_cluster", side_effect=falso):
            stats = synthesis.sintetizar_pendientes(session, modelo=titular)

        assert intentos == ["modelo-de-prueba"], "no había cadena que recorrer"
        assert stats["sintetizados"] == 0
        # Un fallo suelto es un cluster fallido, no falta de proveedor: el
        # modelo todavía no está agotado (hace falta el segundo seguido).
        assert stats["fallidos"] == 1
        assert stats["sin_credencial"] is False

    def test_el_suplente_sin_credencial_propia_no_entra(self, session, medios):
        """
        El otro lado de `cadena_de_modelos`: sin variable propia no hay cadena,
        así que el fallo del titular se cuenta como fallo del cluster, igual que
        antes de multimodelo.
        """
        session.add(
            ModeloIA(
                nombre="mismo-proveedor",
                adaptador=Adaptador.GEMINI,
                modelo="otro",
                activo=False,
            )
        )
        session.commit()
        self._un_cluster(session, medios)
        falso, intentos = self._por_modelo(
            {"modelo-de-prueba": RuntimeError("429 rate limit")}
        )

        with patch.object(synthesis, "sintetizar_cluster", side_effect=falso):
            stats = synthesis.sintetizar_pendientes(session)

        assert intentos == ["modelo-de-prueba"], "el que comparte credencial no entra"
        assert stats["fallidos"] == 1


class TestLaCadenaConTraduccionRealDeExcepciones:
    """
    La cadena recorrida **sin mockear `sintetizar_cluster`**, para que corra la
    traducción real de excepciones y el `rollback` entre modelos.

    `TestCadenaDeFallback` prueba la lógica de la cadena —qué cae, qué agota, qué
    corta la racha— y para eso mockea el servicio entero, que es lo correcto:
    aísla la decisión. Pero por eso mismo **nunca ejercita** el camino que va de
    `leer_api_key` a `ProveedorNoConfigurado` a `SintesisSinConfigurar`, que es
    justamente donde vivía la fuga del nombre de la variable: un test en verde
    con el servicio mockeado no puede ver qué mensaje se construye de verdad.

    Acá el único mock es la frontera de red del adaptador. Todo lo de adentro
    —resolver la credencial, levantar, traducir, caer al siguiente, sanear— es
    el código real.
    """

    def _modelo(self, nombre: str, variable: str) -> ModeloIA:
        return ModeloIA(
            nombre=nombre,
            adaptador=Adaptador.OPENAI_COMPATIBLE,
            modelo="un-modelo",
            base_url="https://proveedor.test/v1",
            activo=False,
            api_key_env=variable,
        )

    @pytest.fixture
    def sin_credencial(self, session: Session, medios) -> None:
        """
        El modelo activo del archivo apunta a `VARIABLE_UNICA`, que
        `conftest.sin_credencial_de_ia` deja sin valor. Alcanza: lo que se
        ejercita es el camino real desde `leer_api_key`.

        **No se agrega un suplente a propósito.** El primer intento de escribir
        este test puso dos modelos con variables inexistentes, y no entró
        ninguno de los dos a la cadena: `_tiene_credencial_propia` filtra a los
        suplentes cuya variable no resuelve, así que un suplente "sin
        credencial" es un estado que no existe. La cadena solo puede tener
        suplentes que SÍ pueden autenticarse.
        """
        cluster = crear_cluster(session)
        crear_noticia(session, medios[0], 1, cluster)
        crear_noticia(session, medios[1], 2, cluster)
        session.commit()

    def test_la_corrida_corta_por_el_camino_real(
        self, session: Session, sin_credencial
    ):
        """
        Sin mockear el servicio: `leer_api_key` levanta de verdad,
        `llamar_modelo` traduce de verdad y la cadena corta de verdad.
        """
        stats = synthesis.sintetizar_pendientes(session)

        assert stats["agotados"] == {"modelo-de-prueba": "sin_configurar"}
        assert stats["sin_credencial"] is True
        # No es un cluster fallido: es el motor sin proveedores.
        assert stats["fallidos"] == 0

    def test_el_nombre_de_la_variable_no_llega_a_las_stats(
        self, session: Session, sin_credencial
    ):
        """
        Las stats se expanden en el cuerpo del 200 de `POST /synthesize`, así
        que todo lo que entre acá es público. Con el servicio mockeado esto no
        se puede ver: el mensaje lo pone el mock.
        """
        stats = synthesis.sintetizar_pendientes(session)

        assert "MODELO_API_KEY" not in str(stats)

    def test_el_mensaje_real_no_nombra_la_variable(self, session: Session):
        """
        **El test que faltaba.** El mensaje que llega hasta acá es el que
        construye `llamar_modelo` de verdad, no uno inventado por un mock — que
        es exactamente por qué el test mockeado de `test_api.py` pasaba en verde
        mientras el real filtraba el nombre de la variable de entorno.
        """
        with pytest.raises(synthesis.SintesisSinConfigurar) as capturado:
            synthesis.llamar_modelo(
                "un prompt",
                self._modelo("solo", f"{PREFIJO_API_KEY_ENV}NO_EXISTE_UNO"),
            )

        mensaje = str(capturado.value)
        assert "NO_EXISTE_UNO" not in mensaje
        assert "solo" in mensaje, "tiene que decir qué modelo falla"

    def test_el_adaptador_que_no_existe_si_dice_cual_es(self, session: Session):
        """
        La otra rama de `SintesisSinConfigurar`, y la contracara: este mensaje
        **sí** viaja entero. No nombra variables ni configuración del operador
        — dice qué adaptador falta y qué usar en su lugar, que es lo que el
        operador necesita para arreglarlo.
        """
        modelo = ModeloIA(
            nombre="con-adaptador-reservado",
            adaptador=Adaptador.ANTHROPIC,
            modelo="un-modelo",
        )

        with pytest.raises(synthesis.SintesisSinConfigurar) as capturado:
            synthesis.llamar_modelo("un prompt", modelo)

        assert "anthropic" in str(capturado.value)
