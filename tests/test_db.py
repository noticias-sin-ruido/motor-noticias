"""
Pruebas de la capa de base de datos: conexión, creación de tablas y operaciones CRUD.
"""
from datetime import datetime

import pytest
from sqlmodel import Session, select

from src.models import Medio, Noticia, Cluster, Sintesis


class TestMedio:
    """Pruebas del modelo Medio."""

    def test_crear_medio(self, session: Session):
        """Verifica que se puede crear un Medio en la BD."""
        medio = Medio(
            nombre="Clarín",
            url_base="https://www.clarin.com",
            feed_rss="https://www.clarin.com/rss",
            activo=True,
        )
        session.add(medio)
        session.commit()
        session.refresh(medio)

        assert medio.id is not None
        assert medio.nombre == "Clarín"
        assert medio.activo is True

    def test_obtener_medio(self, session: Session):
        """Verifica que se puede recuperar un Medio de la BD."""
        medio = Medio(
            nombre="La Nación",
            url_base="https://www.lanacion.com.ar",
            feed_rss="https://www.lanacion.com.ar/rss",
        )
        session.add(medio)
        session.commit()

        # Consultar desde la BD
        statement = select(Medio).where(Medio.nombre == "La Nación")
        medio_recuperado = session.exec(statement).first()

        assert medio_recuperado is not None
        assert medio_recuperado.nombre == "La Nación"
        assert medio_recuperado.url_base == "https://www.lanacion.com.ar"

    def test_listar_medios(self, session: Session):
        """Verifica que se pueden listar todos los Medios."""
        medios_datos = [
            ("Clarín", "https://clarin.com", "https://clarin.com/rss"),
            ("La Nación", "https://lanacion.com", "https://lanacion.com/rss"),
            ("Página 12", "https://pagina12.com.ar", "https://pagina12.com.ar/rss"),
        ]

        for nombre, url_base, feed_rss in medios_datos:
            medio = Medio(nombre=nombre, url_base=url_base, feed_rss=feed_rss)
            session.add(medio)

        session.commit()

        medios = session.exec(select(Medio)).all()
        assert len(medios) == 3


class TestNoticia:
    """Pruebas del modelo Noticia."""

    def test_crear_noticia(self, session: Session):
        """Verifica que se puede crear una Noticia vinculada a un Medio."""
        # Primero crear un Medio
        medio = Medio(
            nombre="Clarín",
            url_base="https://clarin.com",
            feed_rss="https://clarin.com/rss",
        )
        session.add(medio)
        session.commit()
        session.refresh(medio)

        # Ahora crear una Noticia
        noticia = Noticia(
            medio_id=medio.id,
            titulo="Noticia de prueba",
            url="https://clarin.com/noticia-1",
            guid="guid-noticia-1",
            contenido_limpio="Este es el contenido limpio de la noticia.",
            fecha_publicacion=datetime.utcnow(),
        )
        session.add(noticia)
        session.commit()
        session.refresh(noticia)

        assert noticia.id is not None
        assert noticia.medio_id == medio.id
        assert noticia.titulo == "Noticia de prueba"
        assert noticia.embedding is None  # Aún no vectorizada

    def test_noticia_con_embedding(self, session: Session):
        """Verifica que se puede almacenar un embedding en una Noticia."""
        medio = Medio(
            nombre="Test",
            url_base="https://test.com",
            feed_rss="https://test.com/rss",
        )
        session.add(medio)
        session.commit()
        session.refresh(medio)

        # Crear un embedding simulado (384 dims como en la config)
        embedding_simulado = [0.1] * 384

        noticia = Noticia(
            medio_id=medio.id,
            titulo="Noticia con embedding",
            url="https://test.com/noticia-embedding",
            guid="guid-noticia-embedding",
            contenido_limpio="Contenido con vector.",
            fecha_publicacion=datetime.utcnow(),
            embedding=embedding_simulado,
        )
        session.add(noticia)
        session.commit()
        session.refresh(noticia)

        # Recuperar y verificar
        statement = select(Noticia).where(Noticia.id == noticia.id)
        noticia_recuperada = session.exec(statement).first()

        assert noticia_recuperada is not None
        assert len(noticia_recuperada.embedding) == 384

    def test_url_unica(self, session: Session):
        """Verifica que la URL de una Noticia es única (no se permiten duplicados)."""
        medio = Medio(
            nombre="Test",
            url_base="https://test.com",
            feed_rss="https://test.com/rss",
        )
        session.add(medio)
        session.commit()
        session.refresh(medio)

        noticia1 = Noticia(
            medio_id=medio.id,
            titulo="Noticia 1",
            url="https://test.com/noticia-unica",
            guid="guid-noticia-unica-1",
            contenido_limpio="Contenido 1",
            fecha_publicacion=datetime.utcnow(),
        )
        session.add(noticia1)
        session.commit()

        # Intentar crear otra noticia con la misma URL debería fallar
        noticia2 = Noticia(
            medio_id=medio.id,
            titulo="Noticia 2",
            url="https://test.com/noticia-unica",  # URL duplicada
            guid="guid-noticia-unica-2",
            contenido_limpio="Contenido 2",
            fecha_publicacion=datetime.utcnow(),
        )
        session.add(noticia2)

        with pytest.raises(Exception):  # SQLAlchemy levantará una excepción de integridad
            session.commit()


class TestCluster:
    """Pruebas del modelo Cluster."""

    def test_crear_cluster(self, session: Session):
        """Verifica que se puede crear un Cluster."""
        cluster = Cluster(
            titulo_evento="Evento noticioso importante",
            estado="abierto",
        )
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        assert cluster.id is not None
        assert cluster.titulo_evento == "Evento noticioso importante"
        assert cluster.estado == "abierto"
        assert cluster.fecha_creacion is not None

    def test_cluster_con_noticias(self, session: Session):
        """Verifica que se pueden vincular noticias a un cluster."""
        # Crear medio
        medio = Medio(
            nombre="Test",
            url_base="https://test.com",
            feed_rss="https://test.com/rss",
        )
        session.add(medio)
        session.commit()
        session.refresh(medio)

        # Crear cluster
        cluster = Cluster(
            titulo_evento="Evento con múltiples noticias",
            estado="abierto",
        )
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        # Crear noticias vinculadas al cluster
        for i in range(3):
            noticia = Noticia(
                medio_id=medio.id,
                cluster_id=cluster.id,
                titulo=f"Noticia {i+1}",
                url=f"https://test.com/noticia-{i+1}",
                guid=f"guid-noticia-{i+1}",
                contenido_limpio=f"Contenido {i+1}",
                fecha_publicacion=datetime.utcnow(),
            )
            session.add(noticia)

        session.commit()

        # Verificar que el cluster tiene las 3 noticias
        statement = select(Cluster).where(Cluster.id == cluster.id)
        cluster_recuperado = session.exec(statement).first()

        assert cluster_recuperado is not None
        assert len(cluster_recuperado.noticias) == 3


class TestSintesis:
    """Pruebas del modelo Sintesis."""

    def test_crear_sintesis(self, session: Session):
        """Verifica que se puede crear una Síntesis."""
        cluster = Cluster(
            titulo_evento="Evento para sintetizar",
            estado="abierto",
        )
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        sintesis = Sintesis(
            cluster_id=cluster.id,
            resumen_neutro="Este es un resumen neutro del evento.",
            puntos_clave=["Punto 1", "Punto 2", "Punto 3"],
            comparativa_enfoques={
                "Clarín": "Enfoque desde Clarín",
                "La Nación": "Enfoque desde La Nación",
            },
        )
        session.add(sintesis)
        session.commit()
        session.refresh(sintesis)

        assert sintesis.id is not None
        assert sintesis.cluster_id == cluster.id
        assert len(sintesis.puntos_clave) == 3
        assert "Clarín" in sintesis.comparativa_enfoques
        assert sintesis.fecha_generacion is not None
