"""
Tests de src/services/search.py.

`buscar_noticias_similares` usa el operador `<=>` de pgvector, que SQLite no
soporta, así que no se prueba acá -- se mockea en test_api.py. `listar_clusters`
es SQL portable y se prueba directo contra la sesión SQLite de los tests.
"""
from datetime import datetime

import pytest
from sqlmodel import Session

from src.models import Cluster, Medio, Noticia, Sintesis
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


def _crear_sintesis(session: Session, cluster: Cluster, cuantas: int) -> None:
    for i in range(cuantas):
        session.add(
            Sintesis(
                cluster_id=cluster.id,
                titulo_angulo=f"Angulo {cluster.id}-{i}",
                resumen_neutro="Resumen.",
            )
        )
    session.commit()


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

    def test_cuenta_las_sintesis_de_cada_cluster(self, session: Session, medios):
        """
        `cantidad_sintesis` es lo que separa un cluster ya resuelto de uno
        pendiente. Sin el campo, quien consume tenía que paginar
        `GET /sintesis` entera para saberlo -- medido desde la app el
        07/09/2026: 438 síntesis eran 5 páginas y 201 ms antes de dibujar una
        fila, y crece con el histórico.
        """
        con_dos = _crear_cluster_con_noticias(session, medios, "a")
        con_una = _crear_cluster_con_noticias(session, medios, "b")
        sin_ninguna = _crear_cluster_con_noticias(session, medios, "c")
        _crear_sintesis(session, con_dos, 2)
        _crear_sintesis(session, con_una, 1)

        por_id = {c["id"]: c["cantidad_sintesis"] for c in listar_clusters(session, limite=10)}

        assert por_id[con_dos.id] == 2
        assert por_id[con_una.id] == 1
        # **Cero y no ausente**: la ventana distingue "no tiene" de "no sé", y
        # un campo faltante la obligaría a tratar los dos casos igual.
        assert por_id[sin_ninguna.id] == 0

    def test_el_conteo_no_agrega_una_query_por_cluster(self, session: Session, medios):
        """
        El conteo es **una** consulta agrupada, no una por cluster. El guardián
        de arriba compara 2 contra 8 clusters sin síntesis; este los compara
        con síntesis cargadas, que es donde un `count` mal puesto se dispara.
        """
        for i in range(2):
            _crear_sintesis(session, _crear_cluster_con_noticias(session, medios, f"pocos-{i}"), 3)
        with contar_queries(session) as pocos:
            listar_clusters(session, limite=50)

        for i in range(8):
            _crear_sintesis(session, _crear_cluster_con_noticias(session, medios, f"muchos-{i}"), 3)
        with contar_queries(session) as muchos:
            listar_clusters(session, limite=50)

        assert muchos["n"] == pocos["n"]
