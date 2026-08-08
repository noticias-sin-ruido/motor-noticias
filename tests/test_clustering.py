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
from src.models import Cluster, Medio, Noticia
from src.services import clustering

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
            feed_rss=f"https://{nombre.replace(' ', '').lower()}.com/rss",
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
