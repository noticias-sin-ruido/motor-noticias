"""
Tests del servicio de vectorización.

El modelo real de sentence-transformers nunca se carga acá: pesa cientos de MB
y tarda en descargarse. Se mockea `vectorizar_textos`, que es la frontera con
la librería externa; lo que se prueba es la lógica propia (armado del texto,
selección de pendientes, idempotencia, procesamiento por lotes).
"""
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from src.config import settings
from src.models import Medio, Noticia
from src.services import vectorization

DIMENSIONES = 384


@pytest.fixture
def medio(session: Session) -> Medio:
    m = Medio(nombre="Medio Test", url_base="https://test.com", feeds_rss=["https://test.com/rss"])
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


def crear_noticia(session: Session, medio: Medio, n: int, embedding=None) -> Noticia:
    noticia = Noticia(
        medio_id=medio.id,
        titulo=f"Titulo {n}",
        url=f"https://test.com/noticia-{n}",
        guid=f"guid-{n}",
        contenido_limpio=f"Cuerpo de la noticia {n}.",
        fecha_publicacion=datetime.utcnow(),
        embedding=embedding,
    )
    session.add(noticia)
    session.commit()
    session.refresh(noticia)
    return noticia


def embeddings_falsos(textos):
    """Reemplaza al modelo: devuelve un vector distinto por texto, del tamaño real."""
    return [[float(i)] * DIMENSIONES for i, _ in enumerate(textos)]


class TestConstruirTexto:
    def test_combina_titulo_y_cuerpo(self, session: Session, medio: Medio):
        noticia = crear_noticia(session, medio, 1)
        assert vectorization.construir_texto(noticia) == "Titulo 1. Cuerpo de la noticia 1."

    def test_trunca_el_cuerpo_al_limite_configurado(self, session: Session, medio: Medio):
        noticia = crear_noticia(session, medio, 2)
        noticia.contenido_limpio = "x" * (settings.EMBEDDING_CHARS_CUERPO + 500)

        texto = vectorization.construir_texto(noticia)

        cuerpo = texto.split(". ", 1)[1]
        assert len(cuerpo) == settings.EMBEDDING_CHARS_CUERPO

    def test_no_falla_con_cuerpo_mas_corto_que_el_limite(self, session: Session, medio: Medio):
        noticia = crear_noticia(session, medio, 3)
        noticia.contenido_limpio = "corto"

        assert vectorization.construir_texto(noticia) == "Titulo 3. corto"


class TestVectorizarPendientes:
    def test_asigna_embedding_a_las_noticias_sin_vectorizar(self, session: Session, medio: Medio):
        crear_noticia(session, medio, 1)
        crear_noticia(session, medio, 2)

        with patch.object(vectorization, "vectorizar_textos", side_effect=embeddings_falsos):
            stats = vectorization.vectorizar_pendientes(session)

        assert stats == {"pendientes": 2, "vectorizadas": 2}

        noticias = session.exec(select(Noticia)).all()
        assert all(n.embedding is not None for n in noticias)
        assert all(len(n.embedding) == DIMENSIONES for n in noticias)

    def test_ignora_las_noticias_ya_vectorizadas(self, session: Session, medio: Medio):
        crear_noticia(session, medio, 1, embedding=[0.5] * DIMENSIONES)
        crear_noticia(session, medio, 2)

        with patch.object(vectorization, "vectorizar_textos", side_effect=embeddings_falsos) as mock:
            stats = vectorization.vectorizar_pendientes(session)

        assert stats == {"pendientes": 1, "vectorizadas": 1}
        # Solo se le pasó al modelo la noticia que faltaba.
        assert mock.call_args[0][0] == ["Titulo 2. Cuerpo de la noticia 2."]

    def test_es_idempotente(self, session: Session, medio: Medio):
        crear_noticia(session, medio, 1)

        with patch.object(vectorization, "vectorizar_textos", side_effect=embeddings_falsos):
            vectorization.vectorizar_pendientes(session)
            stats_segunda_corrida = vectorization.vectorizar_pendientes(session)

        assert stats_segunda_corrida == {"pendientes": 0, "vectorizadas": 0}

    def test_sin_pendientes_no_llama_al_modelo(self, session: Session):
        with patch.object(vectorization, "vectorizar_textos") as mock:
            stats = vectorization.vectorizar_pendientes(session)

        assert stats == {"pendientes": 0, "vectorizadas": 0}
        mock.assert_not_called()

    def test_respeta_el_limite(self, session: Session, medio: Medio):
        for i in range(5):
            crear_noticia(session, medio, i)

        with patch.object(vectorization, "vectorizar_textos", side_effect=embeddings_falsos):
            stats = vectorization.vectorizar_pendientes(session, limite=2)

        assert stats == {"pendientes": 2, "vectorizadas": 2}

    def test_procesa_en_lotes_cuando_supera_el_batch_size(self, session: Session, medio: Medio):
        total = vectorization.BATCH_SIZE + 5
        for i in range(total):
            crear_noticia(session, medio, i)

        with patch.object(vectorization, "vectorizar_textos", side_effect=embeddings_falsos) as mock:
            stats = vectorization.vectorizar_pendientes(session)

        assert stats == {"pendientes": total, "vectorizadas": total}
        assert mock.call_count == 2  # un lote completo + el resto
