"""
Pruebas de la capa de base de datos: conexión, creación de tablas y operaciones CRUD.
"""
from datetime import datetime

import pytest
from sqlmodel import Session, select

from src.models import Medio, Noticia, Cluster, Sintesis, SintesisNoticia


class TestMedio:
    """Pruebas del modelo Medio."""

    def test_crear_medio(self, session: Session):
        """Verifica que se puede crear un Medio en la BD."""
        medio = Medio(
            nombre="Clarín",
            url_base="https://www.clarin.com",
            feeds_rss=["https://www.clarin.com/rss"],
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
            feeds_rss=["https://www.lanacion.com.ar/rss"],
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
            medio = Medio(nombre=nombre, url_base=url_base, feeds_rss=[feed_rss])
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
            feeds_rss=["https://clarin.com/rss"],
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
            feeds_rss=["https://test.com/rss"],
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
            feeds_rss=["https://test.com/rss"],
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
            feeds_rss=["https://test.com/rss"],
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
            titulo_angulo="El hecho central",
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

    def test_arranca_sin_entregar_al_backend(self, session: Session):
        cluster = Cluster(titulo_evento="Evento")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        sintesis = Sintesis(
            cluster_id=cluster.id, titulo_angulo="Un ángulo", resumen_neutro="Resumen."
        )
        session.add(sintesis)
        session.commit()
        session.refresh(sintesis)

        assert sintesis.enviado_backend is False
        assert sintesis.fecha_envio is None
        assert sintesis.intentos_envio == 0

    def test_un_cluster_produce_varios_angulos(self, session: Session):
        """La unidad que se publica es el ángulo, no el cluster."""
        cluster = Cluster(titulo_evento="Murió una figura pública")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        for titulo in ["La muerte", "Las reacciones", "El velatorio"]:
            session.add(
                Sintesis(cluster_id=cluster.id, titulo_angulo=titulo, resumen_neutro="...")
            )
        session.commit()
        session.refresh(cluster)

        assert len(cluster.sintesis) == 3
        assert {s.titulo_angulo for s in cluster.sintesis} == {
            "La muerte", "Las reacciones", "El velatorio",
        }


class TestMarcaDeSintesis:
    """
    `Cluster.noticias_al_sintetizar` es la guarda contra el reintento infinito.

    Cuenta noticias y no medios porque el conteo de medios no distingue "no pasó
    nada nuevo" de "llegó material nuevo de los mismos medios", y ese segundo
    caso sí puede dar un ángulo publicable.
    """

    def _cluster(self, session: Session, marca=None) -> Cluster:
        cluster = Cluster(titulo_evento="Un hecho", noticias_al_sintetizar=marca)
        session.add(cluster)
        session.commit()
        session.refresh(cluster)
        return cluster

    def test_arranca_sin_marca(self, session: Session):
        assert self._cluster(session).noticias_al_sintetizar is None

    def test_sin_sintesis_pero_con_marca_es_un_intento_sin_resultado(
        self, session: Session
    ):
        """
        No alcanza con mirar si el cluster tiene síntesis: si ningún ángulo llegó
        al mínimo de medios no se crea ninguna fila, y sin la marca el cluster
        sería indistinguible de uno nunca intentado — se reintentaría siempre.
        """
        cluster = self._cluster(session, marca=4)

        assert cluster.sintesis == []
        assert cluster.noticias_al_sintetizar == 4

    def test_el_conteo_de_medios_no_habria_detectado_material_nuevo(
        self, session: Session
    ):
        """
        El caso que motivó contar noticias: TN y La Nación ya estaban, los dos
        publican sobre un ángulo nuevo. Los medios siguen siendo 2, pero las
        noticias pasaron de 2 a 4 y hay material publicable.
        """
        cluster = self._cluster(session, marca=2)
        medios_antes = medios_ahora = 2
        noticias_ahora = 4

        assert medios_ahora == medios_antes  # no lo habría detectado
        assert cluster.noticias_al_sintetizar < noticias_ahora  # sí lo detecta


class TestSintesisNoticia:
    """La relación entre un ángulo y las noticias que lo respaldan."""

    def _armar(self, session: Session, cantidad_medios: int):
        medios = []
        for i in range(cantidad_medios):
            medio = Medio(
                nombre=f"Medio {i}",
                url_base=f"https://m{i}.com",
                feeds_rss=[f"https://m{i}.com/rss"],
            )
            session.add(medio)
            medios.append(medio)
        cluster = Cluster(titulo_evento="Un hecho")
        session.add(cluster)
        session.commit()
        for m in medios:
            session.refresh(m)
        session.refresh(cluster)
        return medios, cluster

    def _noticia(self, session: Session, medio: Medio, cluster: Cluster, n: int) -> Noticia:
        noticia = Noticia(
            medio_id=medio.id,
            cluster_id=cluster.id,
            titulo=f"Nota {n}",
            url=f"https://m.com/{n}",
            guid=f"guid-{n}",
            contenido_limpio="Cuerpo.",
            fecha_publicacion=datetime.utcnow(),
        )
        session.add(noticia)
        session.commit()
        session.refresh(noticia)
        return noticia

    def test_asocia_las_noticias_que_respaldan_el_angulo(self, session: Session):
        medios, cluster = self._armar(session, 2)
        notas = [self._noticia(session, m, cluster, i) for i, m in enumerate(medios)]

        sintesis = Sintesis(
            cluster_id=cluster.id, titulo_angulo="El hecho", resumen_neutro="...",
            noticias=notas,
        )
        session.add(sintesis)
        session.commit()
        session.refresh(sintesis)

        assert len(sintesis.noticias) == 2
        assert session.exec(select(SintesisNoticia)).all().__len__() == 2

    def test_una_noticia_puede_respaldar_varios_angulos(self, session: Session):
        """Un minuto a minuto cubre el hecho y sus reacciones a la vez."""
        medios, cluster = self._armar(session, 1)
        nota = self._noticia(session, medios[0], cluster, 1)

        for titulo in ["El hecho", "Las reacciones"]:
            session.add(
                Sintesis(
                    cluster_id=cluster.id, titulo_angulo=titulo,
                    resumen_neutro="...", noticias=[nota],
                )
            )
        session.commit()
        session.refresh(nota)

        assert len(nota.sintesis) == 2

    def test_permite_contar_medios_distintos_del_angulo(self, session: Session):
        """
        La regla que decide qué se publica: un ángulo necesita 2 medios
        distintos. Se resuelve contando sobre esta relación, no en memoria.
        """
        medios, cluster = self._armar(session, 3)
        # Dos notas del mismo medio y una de otro: son 2 medios, no 3.
        notas = [
            self._noticia(session, medios[0], cluster, 1),
            self._noticia(session, medios[0], cluster, 2),
            self._noticia(session, medios[1], cluster, 3),
        ]
        sintesis = Sintesis(
            cluster_id=cluster.id, titulo_angulo="El hecho",
            resumen_neutro="...", noticias=notas,
        )
        session.add(sintesis)
        session.commit()
        session.refresh(sintesis)

        assert len({n.medio_id for n in sintesis.noticias}) == 2
