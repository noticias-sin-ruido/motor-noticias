"""
Vectorización de noticias: convierte cada noticia en un embedding semántico
que después usa el clustering para detectar si dos artículos hablan del mismo
hecho. Ver specs/change_logs.md, Fase 3, para las decisiones de diseño.
"""
import logging
from typing import List, Optional

from sentence_transformers import SentenceTransformer
from sqlalchemy import func
from sqlmodel import Session, select

from ..config import settings
from ..models import Noticia

logger = logging.getLogger(__name__)

# Noticias que se vectorizan por lote. También acota el tamaño de cada
# transacción: con un backlog grande no conviene un único commit al final.
BATCH_SIZE = 32

_modelo: Optional[SentenceTransformer] = None


def get_modelo() -> SentenceTransformer:
    """
    Devuelve el modelo de embeddings, cargándolo la primera vez que se usa.

    Se carga de forma perezosa (y no al importar el módulo) porque ocupa varios
    cientos de MB en RAM: los tests y cualquier código que no vectorice no
    deberían pagar ese costo. Ver specs/tech_stack.md, punto 6 de Escalabilidad.
    """
    global _modelo

    if _modelo is None:
        logger.info(f"Cargando modelo de embeddings: {settings.EMBEDDING_MODEL}")
        _modelo = SentenceTransformer(settings.EMBEDDING_MODEL)

    return _modelo


def construir_texto(noticia: Noticia) -> str:
    """
    Arma el texto que se vectoriza: título + arranque del cuerpo.

    No se usa el artículo completo por dos motivos: el modelo trunca a ~256-512
    tokens (un artículo largo se cortaría en un punto arbitrario), y por la
    pirámide invertida del periodismo el qué/quién/dónde ya está al principio
    de la nota. El resto es contexto y declaraciones, que es justamente lo que
    *diferencia* editorialmente a dos notas del mismo hecho y por lo tanto
    agrega ruido a la hora de agruparlas.
    """
    cuerpo = noticia.contenido_limpio[: settings.EMBEDDING_CHARS_CUERPO]
    return f"{noticia.titulo}. {cuerpo}"


def vectorizar_textos(textos: List[str]) -> List[List[float]]:
    """
    Vectoriza una lista de textos.

    Los vectores salen normalizados a norma 1 para que la similitud coseno
    entre dos de ellos sea simplemente su producto punto, que es lo que
    aprovecha el clustering.
    """
    embeddings = get_modelo().encode(
        textos,
        normalize_embeddings=True,
        batch_size=BATCH_SIZE,
        show_progress_bar=False,
    )
    return [embedding.tolist() for embedding in embeddings]


def vectorizar_pendientes(session: Session, limite: Optional[int] = None) -> dict:
    """
    Vectoriza las noticias que todavía no tienen embedding.

    Es idempotente: una noticia ya vectorizada no se vuelve a procesar, así que
    se puede correr las veces que haga falta. `limite` sirve para procesar un
    backlog grande de a tandas.
    """
    total = session.exec(
        select(func.count()).select_from(Noticia).where(Noticia.embedding.is_(None))
    ).one()
    if limite is not None:
        total = min(total, limite)

    stats = {"pendientes": total, "vectorizadas": 0}

    if total == 0:
        logger.info("No hay noticias pendientes de vectorizar.")
        return stats

    # Cada lote se consulta de nuevo ACÁ adentro, recién antes de usarlo -- no
    # se carga todo el backlog en una lista al principio. `session.commit()`
    # expira los atributos de TODOS los objetos cargados en la sesión, no
    # solo los del lote recién commiteado: una noticia cargada en un lote
    # anterior y tocada (leída O escrita) después de ese commit dispara su
    # propia recarga fila por fila -- y afecta tanto a leer `titulo` en
    # `construir_texto` como a la asignación `noticia.embedding = ...`, que
    # también necesita el estado previo del objeto para el historial de
    # cambios. Precomputar los textos antes del loop resuelve la lectura pero
    # no la escritura; re-consultar cada lote resuelve las dos, porque cada
    # noticia se toca una única vez, dentro de su propia iteración, antes de
    # su propio commit. Medido en una corrida real: 184 queries de esas sobre
    # 216 noticias pendientes. Mismo patrón que el fix de `commit()` a
    # `flush()` en `agrupar_pendientes` -- ver specs/change_logs.md. No
    # alcanza con `flush()` acá porque el commit por lote es a propósito
    # (acota el tamaño de la transacción con un backlog grande).
    restante = total
    while restante > 0:
        tam_lote = min(BATCH_SIZE, restante)
        lote = session.exec(
            select(Noticia).where(Noticia.embedding.is_(None)).limit(tam_lote)
        ).all()
        embeddings = vectorizar_textos([construir_texto(n) for n in lote])

        for noticia, embedding in zip(lote, embeddings):
            noticia.embedding = embedding
            session.add(noticia)

        session.commit()
        stats["vectorizadas"] += len(lote)
        restante -= len(lote)
        logger.info(f"Vectorizadas {stats['vectorizadas']}/{total} noticias.")

    return stats
