"""
Tests de src/services/search.py.

`buscar_noticias_similares` usa el operador `<=>` de pgvector, que SQLite no
soporta, así que no se prueba acá -- se mockea en test_api.py. `listar_clusters`
es SQL portable y se prueba directo contra la sesión SQLite de los tests.
"""
from datetime import datetime

import pytest
from sqlmodel import Session

from src.models import Cluster, Medio, Noticia
from src.services.search import listar_clusters
from tests.conftest import contar_queries


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


def _crear_cluster_con_noticias(session: Session, medios: list, sufijo: str, estado="procesado") -> Cluster:
    cluster = Cluster(titulo_evento=f"Hecho {sufijo}", estado=estado)
    session.add(cluster)
    session.commit()
    session.refresh(cluster)

    for i in range(2):
        noticia = Noticia(
            medio_id=medios[i % len(medios)].id,
            cluster_id=cluster.id,
            titulo=f"Titular {sufijo}-{i}",
            url=f"https://test.com/{sufijo}-{i}",
            guid=f"guid-{sufijo}-{i}",
            contenido_limpio="Cuerpo.",
            fecha_publicacion=datetime.utcnow(),
        )
        session.add(noticia)
    session.commit()
    return cluster


class TestListarClusters:
    def test_devuelve_noticias_y_medios_correctos(self, session: Session, medios):
        cluster = _crear_cluster_con_noticias(session, medios, "a")

        resultado = listar_clusters(session, limite=10)

        assert len(resultado) == 1
        assert resultado[0]["id"] == cluster.id
        assert resultado[0]["cantidad_noticias"] == 2
        assert resultado[0]["medios"] == sorted(m.nombre for m in medios)
        assert {n["url"] for n in resultado[0]["noticias"]} == {
            "https://test.com/a-0",
            "https://test.com/a-1",
        }

    def test_filtra_por_estado(self, session: Session, medios):
        _crear_cluster_con_noticias(session, medios, "a", estado="procesado")
        abierto = _crear_cluster_con_noticias(session, medios, "b", estado="abierto")

        resultado = listar_clusters(session, estado="abierto", limite=10)

        assert [c["id"] for c in resultado] == [abierto.id]

    def test_no_hace_una_query_por_cluster(self, session: Session, medios):
        """
        Antes: 1 query de clusters + 1 query de noticias/medio POR cluster
        (hasta 101 con limite=100). Ahora es constante, sin importar cuántos
        clusters se listen -- se verifica comparando 2 clusters contra 8.
        """
        for i in range(2):
            _crear_cluster_con_noticias(session, medios, f"pocos-{i}")
        with contar_queries(session) as pocos:
            listar_clusters(session, limite=50)

        for i in range(8):
            _crear_cluster_con_noticias(session, medios, f"muchos-{i}")
        with contar_queries(session) as muchos:
            listar_clusters(session, limite=50)

        assert muchos["n"] == pocos["n"]
