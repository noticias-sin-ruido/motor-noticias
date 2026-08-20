"""
Tests del servicio de clustering.

Los embeddings se construyen a mano (no se carga ningún modelo) para poder
controlar la similitud exacta entre noticias y probar el comportamiento en
los bordes del umbral.
"""
import math
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, select

from src.config import settings
from src.models import Cluster, Medio, Noticia, Sintesis
from src.services import clustering
from src.services.categorias import categoria_no_evento
from tests.conftest import contar_queries

DIMENSIONES = 384


def vector_con_angulo(grados: float) -> list:
    """
    Vector unitario en el plano de los dos primeros ejes.

    Dos vectores construidos así tienen similitud coseno = cos(diferencia de
    ángulos), lo que permite fijar la similitud exacta entre dos noticias.
    """
    radianes = math.radians(grados)
    vector = [0.0] * DIMENSIONES
    vector[0] = math.cos(radianes)
    vector[1] = math.sin(radianes)
    return vector


@pytest.fixture
def medios(session: Session) -> list:
    creados = []
    for nombre in ["Medio A", "Medio B", "Medio C"]:
        m = Medio(
            nombre=nombre,
            url_base=f"https://{nombre.replace(' ', '').lower()}.com",
            feeds_rss=[f"https://{nombre.replace(' ', '').lower()}.com/rss"],
        )
        session.add(m)
        creados.append(m)
    session.commit()
    for m in creados:
        session.refresh(m)
    return creados


def crear_noticia(
    session: Session,
    medio: Medio,
    n: int,
    embedding=None,
    horas_atras: float = 1,
    cluster_id=None,
) -> Noticia:
    noticia = Noticia(
        medio_id=medio.id,
        cluster_id=cluster_id,
        titulo=f"Titulo {n}",
        url=f"https://test.com/noticia-{n}",
        guid=f"guid-{n}",
        contenido_limpio=f"Cuerpo {n}",
        fecha_publicacion=datetime.utcnow() - timedelta(hours=horas_atras),
        embedding=embedding,
    )
    session.add(noticia)
    session.commit()
    session.refresh(noticia)
    return noticia


class TestCalcularCentroide:
    def test_centroide_de_un_solo_vector_es_ese_vector(self):
        vector = vector_con_angulo(0)
        centroide = clustering.calcular_centroide([vector])
        assert pytest.approx(centroide[0], abs=1e-9) == 1.0

    def test_centroide_queda_normalizado(self):
        centroide = clustering.calcular_centroide(
            [vector_con_angulo(0), vector_con_angulo(60)]
        )
        norma = math.sqrt(sum(x * x for x in centroide))
        assert pytest.approx(norma, abs=1e-9) == 1.0

    def test_centroide_queda_entre_los_miembros(self):
        centroide = clustering.calcular_centroide(
            [vector_con_angulo(0), vector_con_angulo(60)]
        )
        # El promedio de 0 y 60 grados apunta a 30 grados.
        assert pytest.approx(centroide[0], abs=1e-6) == math.cos(math.radians(30))


class TestCategoriaNoEvento:
    """
    Un horóscopo o una receta no son un hecho, y sin hecho no hay enfoques que
    comparar: no entran al agrupamiento por evento. Pero **no se descartan** —
    llevan su categoría para su propio circuito de producto.

    Se clasifican por palabra en la URL completa y no por segmento de sección:
    el segmento identifica el tópico, no el género.
    """

    @pytest.mark.parametrize(
        "url, esperada",
        [
            ("https://www.lanacion.com.ar/horoscopo/asi-le-ira-cada-signo", "horoscopo"),
            ("https://www.revistagente.com/horoscopo/por-que-virgo", "horoscopo"),
            ("https://x.com/espectaculos/la-receta-de-empanadas", "recetas"),
            ("https://x.com/servicios/quiniela-nacional-de-hoy", "juegos"),
        ],
    )
    def test_clasifica_los_generos_sin_hecho(self, url, esperada):
        assert categoria_no_evento(url) == esperada

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.lanacion.com.ar/politica/milei-se-reunio-con-el-gabinete",
            "https://tn.com.ar/deportes/river-confirmo-a-thiago-almada",
            "https://www.lanacion.com.ar/sociedad/un-tren-choco-contra-un-colectivo",
            # 'signos' quedó fuera del patrón: "signos de recuperación" es
            # español corriente y el riesgo de falso positivo no compensaba.
            "https://www.lanacion.com.ar/economia/los-signos-de-la-recuperacion",
        ],
    )
    def test_las_noticias_comunes_no_llevan_categoria(self, url):
        assert categoria_no_evento(url) is None


class TestGeneroDeOpinion:
    """
    Una columna no reporta un hecho, así que no hay enfoques que comparar: es
    **género y no tema**, igual que un horóscopo. Si entra al agrupamiento, la
    síntesis termina comparando una opinión contra crónicas del mismo tema.

    Cada rama del patrón está medida contra las 4.532 noticias reales de la
    base; los números viven en el comentario de `CATEGORIAS_NO_EVENTO`.
    """

    @pytest.mark.parametrize(
        "url, rama",
        [
            ("https://www.perfil.com/noticias/opinion/las-dos-caras-del-aislamiento.phtml",
             "/opinion/ — 29 notas"),
            ("https://www.cronista.com/columnistas/el-equilibrio-fiscal/",
             "/columnistas/ — 56 notas, El Cronista"),
            ("https://www.lanacion.com.ar/editoriales/la-hipocresia-de-putin-nid12082026/",
             "/editoriales/ — 4 notas, La Nación"),
            ("https://www.lanacion.com.ar/editorial/una-nota-suelta/",
             "/editorial/ en singular, por si un medio lo usa así"),
            ("https://www.perfil.com/noticias/modo-fontevecchia/dia-981-el-dilema.phtml",
             "/modo-fontevecchia/ — la sección de opinión de Perfil"),
        ],
    )
    def test_las_columnas_se_clasifican_como_opinion(self, url, rama):
        assert categoria_no_evento(url) == "opinion", rama

    def test_la_rama_del_guion_caza_la_columna_de_la_nacion(self):
        """
        La Nación publica columnas dentro de la sección temática, con el
        género en el slug: `/economia/opinion-...`, título "Opinión. Los
        municipios socios...". Es la única nota que aporta la rama `-`.
        """
        url = "https://www.lanacion.com.ar/economia/opinion-los-municipios-socios-nid09082026/"
        assert categoria_no_evento(url) == "opinion"

    def test_no_caza_opinion_como_palabra_suelta(self):
        """
        **El caso que justifica el anclaje**, y el único que lo protege: sin el
        `/` delante, este patrón se puede desanclar a `opinion|columnistas` y
        toda la suite pasa igual. Es una nota de espectáculos, no una columna.
        """
        url = ("https://www.paparazzi.com.ar/teve/"
               "la-letal-opinion-de-yanina-latorre-de-la-china-suarez/")
        assert categoria_no_evento(url) is None

    def test_editorial_como_casa_editora_no_es_opinion(self):
        """
        El anclaje a segmento también cubre el otro sentido de la palabra: una
        nota sobre una editorial de libros no es un editorial del diario.
        """
        url = "https://www.lanacion.com.ar/cultura/editorial-planeta-lanza-su-catalogo/"
        assert categoria_no_evento(url) is None

    def test_una_noticia_comun_de_esas_secciones_no_se_confunde(self):
        """La sección sola no alcanza: lo que marca el género es el segmento."""
        assert categoria_no_evento(
            "https://www.perfil.com/noticias/economia/el-dolar-cerro-en-alza.phtml"
        ) is None

    def test_no_entran_al_agrupamiento(self, session: Session, medios):
        cluster_previo = len(session.exec(select(Cluster)).all())
        for indice, (medio, url) in enumerate(
            [
                (medios[0], "https://a.com/horoscopo/aries-hoy"),
                (medios[1], "https://b.com/horoscopo/aries-de-hoy"),
            ]
        ):
            noticia = Noticia(
                medio_id=medio.id,
                titulo=f"Horóscopo {indice}",
                url=url,
                guid=f"g-{indice}",
                contenido_limpio="Predicciones.",
                fecha_publicacion=datetime.utcnow(),
                embedding=vector_con_angulo(indice),
            )
            session.add(noticia)
        session.commit()

        stats = clustering.agrupar_pendientes(session)

        assert stats["de_otra_categoria"] == 2
        assert stats["evaluadas"] == 0
        assert len(session.exec(select(Cluster)).all()) == cluster_previo
        # Siguen en la base, con su categoría: no se perdieron.
        guardadas = session.exec(select(Noticia)).all()
        assert len(guardadas) == 2
        assert all(categoria_no_evento(n.url) == "horoscopo" for n in guardadas)


class TestAgruparPendientes:
    def test_crea_cluster_con_dos_noticias_similares(self, session: Session, medios):
        crear_noticia(session, medios[0], 1, embedding=vector_con_angulo(0))
        crear_noticia(session, medios[1], 2, embedding=vector_con_angulo(10))

        stats = clustering.agrupar_pendientes(session)

        assert stats["clusters_creados"] == 1
        assert stats["sin_match"] == 0

        clusters = session.exec(select(Cluster)).all()
        assert len(clusters) == 1
        assert clusters[0].estado == clustering.ESTADO_ABIERTO

        noticias = session.exec(select(Noticia)).all()
        assert all(n.cluster_id == clusters[0].id for n in noticias)

    def test_no_agrupa_noticias_distintas(self, session: Session, medios):
        crear_noticia(session, medios[0], 1, embedding=vector_con_angulo(0))
        crear_noticia(session, medios[1], 2, embedding=vector_con_angulo(80))

        stats = clustering.agrupar_pendientes(session)

        assert stats["clusters_creados"] == 0
        assert stats["sin_match"] == 2
        assert session.exec(select(Cluster)).all() == []

    def test_suma_al_cluster_existente_en_vez_de_crear_otro(self, session: Session, medios):
        crear_noticia(session, medios[0], 1, embedding=vector_con_angulo(0))
        crear_noticia(session, medios[1], 2, embedding=vector_con_angulo(5))
        clustering.agrupar_pendientes(session)

        crear_noticia(session, medios[2], 3, embedding=vector_con_angulo(10))
        stats = clustering.agrupar_pendientes(session)

        assert stats["clusters_creados"] == 0
        assert stats["sumadas_a_cluster"] == 1
        assert len(session.exec(select(Cluster)).all()) == 1

    def test_prefiere_el_cluster_aunque_una_suelta_se_parezca_mas(
        self, session: Session, medios
    ):
        """
        Un cluster por encima del umbral gana sobre cualquier suelta.

        Con el criterio de "mejor candidato global", dos noticias casi idénticas
        entre sí formaban un cluster paralelo aunque las dos pertenecieran a uno
        que ya existía. De ahí venía la fragmentación.
        """
        cluster = Cluster(titulo_evento="El hecho", estado=clustering.ESTADO_ABIERTO)
        session.add(cluster)
        session.commit()
        session.refresh(cluster)
        crear_noticia(session, medios[0], 1, vector_con_angulo(0), cluster_id=cluster.id)
        crear_noticia(session, medios[1], 2, vector_con_angulo(4), cluster_id=cluster.id)

        # Ambas están a ~0.90 del centroide (por encima del umbral) pero a 0.9998
        # entre sí: con el criterio viejo se iban juntas a un cluster nuevo.
        crear_noticia(session, medios[2], 3, vector_con_angulo(26))
        crear_noticia(session, medios[0], 4, vector_con_angulo(27))

        clustering.agrupar_pendientes(session)

        assert len(session.exec(select(Cluster)).all()) == 1
        agrupadas = session.exec(
            select(Noticia).where(Noticia.cluster_id == cluster.id)
        ).all()
        assert len(agrupadas) == 4

    def test_crea_cluster_nuevo_si_ningun_cluster_llega_al_umbral(
        self, session: Session, medios
    ):
        """Preferir el cluster no debe impedir que nazcan hechos nuevos."""
        cluster = Cluster(titulo_evento="Un hecho", estado=clustering.ESTADO_ABIERTO)
        session.add(cluster)
        session.commit()
        session.refresh(cluster)
        crear_noticia(session, medios[0], 1, vector_con_angulo(0), cluster_id=cluster.id)
        crear_noticia(session, medios[1], 2, vector_con_angulo(4), cluster_id=cluster.id)

        # Lejos del cluster (cos 80° = 0.17) pero parecidas entre sí.
        crear_noticia(session, medios[2], 3, vector_con_angulo(80))
        crear_noticia(session, medios[0], 4, vector_con_angulo(82))

        clustering.agrupar_pendientes(session)

        assert len(session.exec(select(Cluster)).all()) == 2

    def test_ignora_noticias_sin_embedding(self, session: Session, medios):
        crear_noticia(session, medios[0], 1, embedding=None)
        crear_noticia(session, medios[1], 2, embedding=None)

        stats = clustering.agrupar_pendientes(session)

        assert stats["evaluadas"] == 0
        assert session.exec(select(Cluster)).all() == []

    def test_ignora_noticias_fuera_de_la_ventana(self, session: Session, medios):
        vieja = settings.HORAS_CLUSTER_ABIERTO + 5
        crear_noticia(session, medios[0], 1, embedding=vector_con_angulo(0), horas_atras=vieja)
        crear_noticia(session, medios[1], 2, embedding=vector_con_angulo(5), horas_atras=vieja)

        stats = clustering.agrupar_pendientes(session)

        assert stats["evaluadas"] == 0
        assert session.exec(select(Cluster)).all() == []

    def test_es_idempotente(self, session: Session, medios):
        crear_noticia(session, medios[0], 1, embedding=vector_con_angulo(0))
        crear_noticia(session, medios[1], 2, embedding=vector_con_angulo(5))

        clustering.agrupar_pendientes(session)
        stats_segunda = clustering.agrupar_pendientes(session)

        assert stats_segunda["evaluadas"] == 0
        assert len(session.exec(select(Cluster)).all()) == 1

    def test_respeta_el_umbral_configurado(self, session: Session, medios, monkeypatch):
        # cos(45 grados) ~ 0.707: queda por debajo de 0.75 y por encima de 0.65.
        crear_noticia(session, medios[0], 1, embedding=vector_con_angulo(0))
        crear_noticia(session, medios[1], 2, embedding=vector_con_angulo(45))

        monkeypatch.setattr(settings, "UMBRAL_SIMILITUD", 0.75)
        assert clustering.agrupar_pendientes(session)["clusters_creados"] == 0

        monkeypatch.setattr(settings, "UMBRAL_SIMILITUD", 0.65)
        assert clustering.agrupar_pendientes(session)["clusters_creados"] == 1


class TestCerrarClustersVencidos:
    def _cluster_vencido(self, session: Session) -> Cluster:
        cluster = Cluster(
            titulo_evento="Evento viejo",
            estado=clustering.ESTADO_ABIERTO,
            fecha_creacion=datetime.utcnow()
            - timedelta(hours=settings.HORAS_CLUSTER_ABIERTO + 1),
        )
        session.add(cluster)
        session.commit()
        session.refresh(cluster)
        return cluster

    def test_procesa_el_cluster_con_medios_suficientes(self, session: Session, medios):
        cluster = self._cluster_vencido(session)
        crear_noticia(session, medios[0], 1, cluster_id=cluster.id)
        crear_noticia(session, medios[1], 2, cluster_id=cluster.id)

        stats = clustering.cerrar_clusters_vencidos(session)

        assert stats == {"evaluados": 1, "procesados": 1, "descartados": 0}
        session.refresh(cluster)
        assert cluster.estado == clustering.ESTADO_PROCESADO

    def test_descarta_el_cluster_de_un_solo_medio(self, session: Session, medios):
        cluster = self._cluster_vencido(session)
        crear_noticia(session, medios[0], 1, cluster_id=cluster.id)
        crear_noticia(session, medios[0], 2, cluster_id=cluster.id)

        stats = clustering.cerrar_clusters_vencidos(session)

        assert stats == {"evaluados": 1, "procesados": 0, "descartados": 1}
        session.refresh(cluster)
        assert cluster.estado == clustering.ESTADO_DESCARTADO

    def test_no_toca_los_clusters_vigentes(self, session: Session, medios):
        cluster = Cluster(titulo_evento="Evento nuevo", estado=clustering.ESTADO_ABIERTO)
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        stats = clustering.cerrar_clusters_vencidos(session)

        assert stats["evaluados"] == 0
        session.refresh(cluster)
        assert cluster.estado == clustering.ESTADO_ABIERTO


class TestFusionarClustersDuplicados:
    """
    La asignación codiciosa puede dejar dos clusters describiendo el mismo
    evento; estos tests cubren la pasada que los vuelve a unir.
    """

    def _cluster(
        self,
        session: Session,
        titulo: str,
        estado: str = clustering.ESTADO_ABIERTO,
        horas_atras: float = 1,
    ) -> Cluster:
        cluster = Cluster(
            titulo_evento=titulo,
            estado=estado,
            fecha_creacion=datetime.utcnow() - timedelta(hours=horas_atras),
        )
        session.add(cluster)
        session.commit()
        session.refresh(cluster)
        return cluster

    def test_fusiona_dos_clusters_del_mismo_evento(self, session: Session, medios):
        # Centroides a 2° y 8°: similitud cos(6°) = 0.995, por encima del umbral.
        viejo = self._cluster(session, "Murio Fulano", horas_atras=2)
        nuevo = self._cluster(session, "Murio Fulano a los 51", horas_atras=1)
        crear_noticia(session, medios[0], 1, vector_con_angulo(0), cluster_id=viejo.id)
        crear_noticia(session, medios[1], 2, vector_con_angulo(4), cluster_id=viejo.id)
        crear_noticia(session, medios[2], 3, vector_con_angulo(6), cluster_id=nuevo.id)
        crear_noticia(session, medios[0], 4, vector_con_angulo(10), cluster_id=nuevo.id)

        stats = clustering.fusionar_clusters_duplicados(session)

        assert stats == {"evaluados": 2, "fusionados": 1}
        assert session.get(Cluster, nuevo.id) is None
        supervivientes = session.exec(select(Cluster)).all()
        assert len(supervivientes) == 1
        noticias = session.exec(
            select(Noticia).where(Noticia.cluster_id == viejo.id)
        ).all()
        assert len(noticias) == 4

    def test_sobrevive_el_cluster_mas_viejo(self, session: Session, medios):
        """El plazo de cierre lo manda `fecha_creacion`: fusionar no debe estirarlo."""
        reciente = self._cluster(session, "El nuevo", horas_atras=1)
        antiguo = self._cluster(session, "El viejo", horas_atras=5)
        crear_noticia(session, medios[0], 1, vector_con_angulo(0), cluster_id=reciente.id)
        crear_noticia(session, medios[1], 2, vector_con_angulo(2), cluster_id=reciente.id)
        crear_noticia(session, medios[2], 3, vector_con_angulo(4), cluster_id=antiguo.id)
        crear_noticia(session, medios[0], 4, vector_con_angulo(6), cluster_id=antiguo.id)

        clustering.fusionar_clusters_duplicados(session)

        assert session.get(Cluster, reciente.id) is None
        assert session.get(Cluster, antiguo.id) is not None

    def test_no_fusiona_eventos_distintos(self, session: Session, medios):
        # Centroides a 2° y 42°: similitud cos(40°) = 0.766, por debajo del umbral.
        uno = self._cluster(session, "Un evento")
        otro = self._cluster(session, "Otro evento")
        crear_noticia(session, medios[0], 1, vector_con_angulo(0), cluster_id=uno.id)
        crear_noticia(session, medios[1], 2, vector_con_angulo(4), cluster_id=uno.id)
        crear_noticia(session, medios[2], 3, vector_con_angulo(40), cluster_id=otro.id)
        crear_noticia(session, medios[0], 4, vector_con_angulo(44), cluster_id=otro.id)

        stats = clustering.fusionar_clusters_duplicados(session)

        assert stats == {"evaluados": 2, "fusionados": 0}
        assert session.get(Cluster, uno.id) is not None
        assert session.get(Cluster, otro.id) is not None

    def test_fusiona_en_cadena_tres_clusters(self, session: Session, medios):
        clusters = [
            self._cluster(session, f"Cobertura {i}", horas_atras=3 - i) for i in range(3)
        ]
        for indice, cluster in enumerate(clusters):
            crear_noticia(
                session, medios[0], indice * 2, vector_con_angulo(indice * 5),
                cluster_id=cluster.id,
            )
            crear_noticia(
                session, medios[1], indice * 2 + 1, vector_con_angulo(indice * 5 + 2),
                cluster_id=cluster.id,
            )

        stats = clustering.fusionar_clusters_duplicados(session)

        assert stats["fusionados"] == 2
        assert len(session.exec(select(Cluster)).all()) == 1
        # La pasada tiene que dejar la base en un punto fijo, no a mitad de
        # camino: la consolidación no puede depender de cuántas corridas del
        # scheduler alcanzaron a ejecutarse antes de que el cluster cierre.
        assert clustering.fusionar_clusters_duplicados(session)["fusionados"] == 0

    def test_ignora_los_clusters_ya_cerrados(self, session: Session, medios):
        """Un cluster cerrado ya pudo haberse publicado: no se toca."""
        procesado = self._cluster(session, "Ya cerrado", estado=clustering.ESTADO_PROCESADO)
        abierto = self._cluster(session, "Todavia abierto")
        crear_noticia(session, medios[0], 1, vector_con_angulo(0), cluster_id=procesado.id)
        crear_noticia(session, medios[1], 2, vector_con_angulo(2), cluster_id=procesado.id)
        crear_noticia(session, medios[2], 3, vector_con_angulo(4), cluster_id=abierto.id)
        crear_noticia(session, medios[0], 4, vector_con_angulo(6), cluster_id=abierto.id)

        stats = clustering.fusionar_clusters_duplicados(session)

        assert stats == {"evaluados": 1, "fusionados": 0}
        assert session.get(Cluster, procesado.id) is not None
        assert session.get(Cluster, abierto.id) is not None

    def test_es_idempotente(self, session: Session, medios):
        viejo = self._cluster(session, "Evento", horas_atras=2)
        nuevo = self._cluster(session, "Mismo evento", horas_atras=1)
        crear_noticia(session, medios[0], 1, vector_con_angulo(0), cluster_id=viejo.id)
        crear_noticia(session, medios[1], 2, vector_con_angulo(2), cluster_id=viejo.id)
        crear_noticia(session, medios[2], 3, vector_con_angulo(4), cluster_id=nuevo.id)
        crear_noticia(session, medios[0], 4, vector_con_angulo(6), cluster_id=nuevo.id)

        clustering.fusionar_clusters_duplicados(session)
        segunda = clustering.fusionar_clusters_duplicados(session)

        assert segunda == {"evaluados": 1, "fusionados": 0}
        assert len(session.exec(select(Cluster)).all()) == 1

    def test_muda_las_sintesis_al_superviviente(self, session: Session, medios):
        """
        Una síntesis ya publicada no se borra: su id es la clave de idempotencia
        del webhook. Antes de esto, fusionar un cluster con síntesis fallaba con
        IntegrityError al intentar dejar `cluster_id` en NULL.
        """
        viejo = self._cluster(session, "El hecho", horas_atras=2)
        nuevo = self._cluster(session, "El mismo hecho", horas_atras=1)
        crear_noticia(session, medios[0], 1, vector_con_angulo(0), cluster_id=viejo.id)
        crear_noticia(session, medios[1], 2, vector_con_angulo(2), cluster_id=viejo.id)
        crear_noticia(session, medios[2], 3, vector_con_angulo(4), cluster_id=nuevo.id)
        crear_noticia(session, medios[0], 4, vector_con_angulo(6), cluster_id=nuevo.id)

        sintesis = Sintesis(
            cluster_id=nuevo.id,
            titulo_angulo="Un ángulo ya entregado",
            resumen_neutro="...",
            enviado_backend=True,
        )
        session.add(sintesis)
        session.commit()
        session.refresh(sintesis)
        id_original = sintesis.id

        stats = clustering.fusionar_clusters_duplicados(session)

        assert stats["fusionados"] == 1
        session.refresh(sintesis)
        assert sintesis.id == id_original          # el backend la sigue reconociendo
        assert sintesis.cluster_id == viejo.id     # mudada, no borrada
        assert sintesis.enviado_backend is True

    def test_hereda_la_marca_de_sintesis_mas_alta(self, session: Session, medios):
        """
        El cluster fusionado tiene más noticias que sus partes, así que la marca
        heredada queda por debajo del total y dispara la re-síntesis.
        """
        viejo = self._cluster(session, "El hecho", horas_atras=2)
        nuevo = self._cluster(session, "El mismo hecho", horas_atras=1)
        viejo.noticias_al_sintetizar = None
        nuevo.noticias_al_sintetizar = 2
        session.add(viejo)
        session.add(nuevo)
        session.commit()
        crear_noticia(session, medios[0], 1, vector_con_angulo(0), cluster_id=viejo.id)
        crear_noticia(session, medios[1], 2, vector_con_angulo(2), cluster_id=viejo.id)
        crear_noticia(session, medios[2], 3, vector_con_angulo(4), cluster_id=nuevo.id)
        crear_noticia(session, medios[0], 4, vector_con_angulo(6), cluster_id=nuevo.id)

        clustering.fusionar_clusters_duplicados(session)

        session.refresh(viejo)
        assert viejo.noticias_al_sintetizar == 2
        noticias_actuales = len(
            session.exec(select(Noticia).where(Noticia.cluster_id == viejo.id)).all()
        )
        assert viejo.noticias_al_sintetizar < noticias_actuales  # se re-sintetiza

    def test_respeta_el_umbral_configurado(self, session: Session, medios, monkeypatch):
        # Centroides a 2° y 42°: similitud 0.766, que solo alcanza si se baja el umbral.
        uno = self._cluster(session, "Un evento", horas_atras=2)
        otro = self._cluster(session, "Otro evento", horas_atras=1)
        crear_noticia(session, medios[0], 1, vector_con_angulo(0), cluster_id=uno.id)
        crear_noticia(session, medios[1], 2, vector_con_angulo(4), cluster_id=uno.id)
        crear_noticia(session, medios[2], 3, vector_con_angulo(40), cluster_id=otro.id)
        crear_noticia(session, medios[0], 4, vector_con_angulo(44), cluster_id=otro.id)

        monkeypatch.setattr(settings, "UMBRAL_FUSION_CLUSTERS", 0.70)
        stats = clustering.fusionar_clusters_duplicados(session)

        assert stats["fusionados"] == 1


def _vector_ortogonal(indice: int) -> list:
    """
    Vector unitario en una dimensión propia de `indice`: similitud coseno 0
    contra cualquier otro vector construido con un índice distinto.

    Los tests de no-escalamiento de abajo no evalúan la lógica de asignación
    ni de fusión -- solo cuántas queries dispara cargar los clusters. Con
    `vector_con_angulo` (un círculo de 2 dimensiones) no hay forma de separar
    más de un puñado de clusters por encima del umbral de fusión; con una
    dimensión propia por cluster, ninguno se parece a otro sin importar
    cuántos haya.
    """
    vector = [0.0] * DIMENSIONES
    vector[indice % DIMENSIONES] = 1.0
    return vector


def _cluster_abierto_con_noticias(session, medios, n_noticias, sufijo, indice, vencido=False):
    cluster = Cluster(
        titulo_evento=f"Hecho {sufijo}",
        estado=clustering.ESTADO_ABIERTO,
        fecha_creacion=datetime.utcnow() - timedelta(
            hours=settings.HORAS_CLUSTER_ABIERTO + 1 if vencido else 1
        ),
    )
    session.add(cluster)
    session.commit()
    session.refresh(cluster)
    vector = _vector_ortogonal(indice)
    for i in range(n_noticias):
        crear_noticia(
            session, medios[i % len(medios)], f"{sufijo}-{i}", vector, cluster_id=cluster.id,
        )
    return cluster


class TestCargaDeClustersAbiertosNoEscala:
    """
    `_cargar_clusters_abiertos` traía las noticias de cada cluster con una
    query aparte -- un N+1 que no se tocó en la revisión de Fase 5 (esa pasada
    solo miró `synthesis.py` y `search.py`). La usan tanto `agrupar_pendientes`
    como `fusionar_clusters_duplicados`, así que se prueban las dos.
    """

    def test_agrupar_pendientes_no_escala_con_clusters_abiertos(self, session, medios):
        for i in range(2):
            _cluster_abierto_con_noticias(session, medios, 2, f"pocos-{i}", indice=i)
        with contar_queries(session) as pocos:
            clustering.agrupar_pendientes(session)

        for i in range(15):
            _cluster_abierto_con_noticias(session, medios, 2, f"muchos-{i}", indice=100 + i)
        with contar_queries(session) as muchos:
            clustering.agrupar_pendientes(session)

        assert muchos["n"] == pocos["n"]

    def test_fusionar_no_escala_con_clusters_abiertos(self, session, medios):
        for i in range(2):
            _cluster_abierto_con_noticias(session, medios, 2, f"pocos-{i}", indice=i)
        with contar_queries(session) as pocos:
            clustering.fusionar_clusters_duplicados(session)

        for i in range(15):
            _cluster_abierto_con_noticias(session, medios, 2, f"muchos-{i}", indice=100 + i)
        with contar_queries(session) as muchos:
            clustering.fusionar_clusters_duplicados(session)

        assert muchos["n"] == pocos["n"]


class TestCerrarClustersVencidosNoEscala:
    def test_no_hace_una_query_por_cluster(self, session, medios):
        for i in range(2):
            _cluster_abierto_con_noticias(
                session, medios, 2, f"pocos-{i}", indice=i, vencido=True
            )
        with contar_queries(session) as pocos:
            clustering.cerrar_clusters_vencidos(session)

        for i in range(15):
            _cluster_abierto_con_noticias(
                session, medios, 2, f"muchos-{i}", indice=100 + i, vencido=True
            )
        with contar_queries(session) as muchos:
            clustering.cerrar_clusters_vencidos(session)

        assert muchos["n"] == pocos["n"]


class TestCrearClusterNuevoNoEscala:
    """
    Ninguno de los tests de arriba pasa por acá: todos parten de clusters ya
    existentes. Crear un cluster nuevo (`agrupar_pendientes`, rama del `else`)
    hacía `session.commit()` para conseguirle el id -- y `commit()` expira por
    defecto los atributos de TODOS los objetos que la sesión tiene cargados,
    no solo el cluster nuevo. Cada `.embedding`/`.cluster_id` que el resto del
    loop leía después (comparando la siguiente suelta contra las demás en
    `_mejor_match`) disparaba su propia recarga fila por fila. Medido en una
    corrida real contra Postgres: 8.345 queries de esas sobre 329 noticias
    sueltas y 25 clusters nuevos -- el 85% de las queries de la corrida.

    El invariante correcto acá NO es "misma cantidad de queries sin importar
    cuántos clusters se creen" -- cada cluster nuevo es una fila real, un
    INSERT genuino, y eso escala con la cantidad de clusters por diseño. El
    invariante que sí tiene que valer, y que es justo lo que rompía el bug, es
    que la cantidad de queries **no dependa de cuántas noticias sueltas más
    haya para comparar** una vez que ya se creó el primer cluster. Por eso los
    tests de abajo mantienen `clusters_creados` fijo en 2 y varían solo el
    ruido de sueltas que no matchean con nada.
    """

    def _par_suelto(self, session, medios, indice, sufijo):
        vector = _vector_ortogonal(indice)
        crear_noticia(session, medios[0], f"{sufijo}-a", vector, horas_atras=2)
        crear_noticia(session, medios[1], f"{sufijo}-b", vector, horas_atras=2)

    def _singleton_sin_match(self, session, medios, indice, sufijo):
        crear_noticia(session, medios[0], sufijo, _vector_ortogonal(indice), horas_atras=1)

    def test_no_escala_con_la_cantidad_de_sueltas_restantes(self, session, medios):
        # Un cluster abierto de fondo: sin esto, `_cargar_clusters_abiertos`
        # hace 1 query en la primera pasada (nada que cargar) y 2 en la
        # segunda (ya hay clusters), y esa diferencia de 1 no tiene nada que
        # ver con lo que se está probando acá.
        _cluster_abierto_con_noticias(session, medios, 1, "fondo", indice=999)

        for i in range(2):
            self._par_suelto(session, medios, i, f"pocas-par-{i}")
        for i in range(3):
            self._singleton_sin_match(session, medios, 50 + i, f"pocas-suelta-{i}")
        with contar_queries(session) as pocas:
            stats_pocas = clustering.agrupar_pendientes(session)
        assert stats_pocas["clusters_creados"] == 2
        assert stats_pocas["sin_match"] == 3

        for i in range(2):
            self._par_suelto(session, medios, 200 + i, f"muchas-par-{i}")
        for i in range(50):
            self._singleton_sin_match(session, medios, 300 + i, f"muchas-suelta-{i}")
        with contar_queries(session) as muchas:
            stats_muchas = clustering.agrupar_pendientes(session)
        assert stats_muchas["clusters_creados"] == 2
        # >= y no ==: las sueltas de la corrida anterior que no matchearon se
        # reevalúan de nuevo a propósito (ver el docstring de
        # `agrupar_pendientes`), así que las 3 de "pocas" se suman acá.
        assert stats_muchas["sin_match"] >= 50

        assert muchas["n"] == pocas["n"]
