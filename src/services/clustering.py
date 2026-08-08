"""
Agrupación de noticias por evento: detecta que dos artículos de medios distintos
hablan del mismo hecho comparando sus embeddings.

La asignación es **incremental**, no batch: cada noticia nueva se compara contra
los clusters abiertos y contra las noticias sueltas recientes. Se descartó
DBSCAN/agglomerative en producción porque son algoritmos batch — habría que
reprocesar todo en cada ciclo de 15 minutos y los clusters se reorganizarían
entre corridas, lo que no encaja con el ciclo de vida abierto/procesado.

Ver specs/change_logs.md, Fase 3, para la calibración de los parámetros.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
from sqlmodel import Session, select

from ..config import settings
from ..models import Cluster, Noticia

logger = logging.getLogger(__name__)

ESTADO_ABIERTO = "abierto"
ESTADO_PROCESADO = "procesado"
ESTADO_DESCARTADO = "descartado"


def _normalizar(vector: np.ndarray) -> np.ndarray:
    """Lleva el vector a norma 1 para que el producto punto sea similitud coseno."""
    norma = float(np.linalg.norm(vector))
    return vector / norma if norma else vector


def calcular_centroide(embeddings: Sequence[Sequence[float]]) -> List[float]:
    """
    Centroide de un cluster: el promedio de los embeddings de sus miembros.

    Se renormaliza porque el promedio de vectores unitarios no es unitario, y
    sin eso el producto punto dejaría de ser la similitud coseno.

    Se compara contra el centroide y no contra el miembro más parecido para
    evitar encadenamiento: con "vecino más cercano", A~B y B~C arrastran a C al
    cluster de A aunque A y C no tengan nada que ver. Medido sobre datos reales,
    ese modo pegoteaba seis columnas de opinión económica distintas en un solo
    cluster de 13 noticias.
    """
    return _normalizar(np.mean(np.array(embeddings, dtype=float), axis=0)).tolist()


class _ClusterEnMemoria:
    """Estado de un cluster abierto durante una corrida de agrupamiento."""

    def __init__(self, cluster_id: int, embeddings: List[List[float]], medios: Set[int]):
        self.id = cluster_id
        self.embeddings = embeddings
        self.medios = medios
        self.centroide = np.array(calcular_centroide(embeddings), dtype=float)

    def agregar(self, embedding: List[float], medio_id: int) -> None:
        self.embeddings.append(embedding)
        self.medios.add(medio_id)
        self.centroide = np.array(calcular_centroide(self.embeddings), dtype=float)


def _cargar_clusters_abiertos(session: Session, desde: datetime) -> Dict[int, _ClusterEnMemoria]:
    """Carga los clusters abiertos vigentes junto a los embeddings de sus miembros."""
    clusters = session.exec(
        select(Cluster).where(
            Cluster.estado == ESTADO_ABIERTO,
            Cluster.fecha_creacion >= desde,
        )
    ).all()

    en_memoria: Dict[int, _ClusterEnMemoria] = {}
    for cluster in clusters:
        miembros = session.exec(
            select(Noticia).where(
                Noticia.cluster_id == cluster.id,
                Noticia.embedding.is_not(None),
            )
        ).all()
        if miembros:
            en_memoria[cluster.id] = _ClusterEnMemoria(
                cluster.id,
                [list(m.embedding) for m in miembros],
                {m.medio_id for m in miembros},
            )
    return en_memoria


def _mejor_match(
    embedding: np.ndarray,
    clusters: Dict[int, _ClusterEnMemoria],
    sueltas: List[Noticia],
    noticia_actual_id: Optional[int],
) -> Tuple[float, Optional[int], Optional[Noticia]]:
    """
    Devuelve (similitud, cluster_id, noticia_suelta) del mejor candidato.

    Los embeddings están normalizados, así que el producto punto ES la
    similitud coseno.
    """
    mejor_sim = -1.0
    mejor_cluster: Optional[int] = None
    mejor_suelta: Optional[Noticia] = None

    for cluster_id, cluster in clusters.items():
        sim = float(np.dot(embedding, cluster.centroide))
        if sim > mejor_sim:
            mejor_sim, mejor_cluster, mejor_suelta = sim, cluster_id, None

    for otra in sueltas:
        if otra.id == noticia_actual_id or otra.cluster_id is not None:
            continue
        sim = float(np.dot(embedding, np.array(otra.embedding, dtype=float)))
        if sim > mejor_sim:
            mejor_sim, mejor_cluster, mejor_suelta = sim, None, otra

    return mejor_sim, mejor_cluster, mejor_suelta


def agrupar_pendientes(session: Session) -> dict:
    """
    Agrupa las noticias vectorizadas que todavía no pertenecen a ningún cluster.

    Las noticias sueltas se reevalúan en cada corrida a propósito: una noticia
    que hoy no matcheó con nada puede matchear más tarde, cuando otro medio
    cubra el mismo hecho. Por eso el alcance es la ventana de clusters abiertos
    y no una ventana de minutos atada a la frecuencia del scheduler.
    """
    ahora = datetime.utcnow()
    desde = ahora - timedelta(hours=settings.HORAS_CLUSTER_ABIERTO)

    clusters = _cargar_clusters_abiertos(session, desde)
    sueltas = session.exec(
        select(Noticia)
        .where(
            Noticia.cluster_id.is_(None),
            Noticia.embedding.is_not(None),
            Noticia.fecha_publicacion >= desde,
        )
        .order_by(Noticia.fecha_publicacion)
    ).all()

    stats = {
        "evaluadas": len(sueltas),
        "sumadas_a_cluster": 0,
        "clusters_creados": 0,
        "sin_match": 0,
    }

    for noticia in sueltas:
        # Puede haber quedado asignada al procesar una noticia anterior.
        if noticia.cluster_id is not None:
            continue

        embedding = np.array(noticia.embedding, dtype=float)
        sim, cluster_id, suelta = _mejor_match(embedding, clusters, sueltas, noticia.id)

        if sim < settings.UMBRAL_SIMILITUD:
            stats["sin_match"] += 1
            continue

        if cluster_id is not None:
            noticia.cluster_id = cluster_id
            session.add(noticia)
            clusters[cluster_id].agregar(noticia.embedding, noticia.medio_id)
            stats["sumadas_a_cluster"] += 1
        else:
            # El cluster se crea recién con el segundo artículo: la mayoría de las
            # noticias no tiene par, y crear un cluster por cada una llenaría la
            # tabla de grupos de un solo miembro para después descartarlos.
            cluster = Cluster(titulo_evento=suelta.titulo, estado=ESTADO_ABIERTO)
            session.add(cluster)
            session.commit()
            session.refresh(cluster)

            suelta.cluster_id = cluster.id
            noticia.cluster_id = cluster.id
            session.add(suelta)
            session.add(noticia)

            clusters[cluster.id] = _ClusterEnMemoria(
                cluster.id,
                [list(suelta.embedding), list(noticia.embedding)],
                {suelta.medio_id, noticia.medio_id},
            )
            stats["clusters_creados"] += 1

    session.commit()
    logger.info(f"Agrupamiento completado: {stats}")
    return stats


def cerrar_clusters_vencidos(session: Session) -> dict:
    """
    Cierra los clusters abiertos que ya cumplieron su ventana de vida.

    El plazo corre desde `fecha_creacion` y NO se reinicia con cada artículo
    nuevo: con ventana deslizante, una historia de cobertura larga (la visita
    del Papa, por ejemplo) nunca cerraría y nunca se publicaría nada. Con plazo
    fijo, esa historia produce varios clusters sucesivos, que además es más
    correcto: no es un evento, es una serie de eventos.
    """
    limite = datetime.utcnow() - timedelta(hours=settings.HORAS_CLUSTER_ABIERTO)

    vencidos = session.exec(
        select(Cluster).where(
            Cluster.estado == ESTADO_ABIERTO,
            Cluster.fecha_creacion < limite,
        )
    ).all()

    stats = {"evaluados": len(vencidos), "procesados": 0, "descartados": 0}

    for cluster in vencidos:
        noticias = session.exec(
            select(Noticia).where(Noticia.cluster_id == cluster.id)
        ).all()
        medios_distintos = len({n.medio_id for n in noticias})

        if medios_distintos >= settings.MIN_MEDIOS_CLUSTER:
            cluster.estado = ESTADO_PROCESADO
            stats["procesados"] += 1
        else:
            cluster.estado = ESTADO_DESCARTADO
            stats["descartados"] += 1

        session.add(cluster)

    session.commit()
    logger.info(f"Cierre de clusters completado: {stats}")
    return stats