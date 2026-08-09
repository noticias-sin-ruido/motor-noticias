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
from ..models import Cluster, Noticia, Sintesis
from .categorias import categoria_no_evento

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

    def absorber(self, otro: "_ClusterEnMemoria") -> None:
        """Incorpora los miembros de otro cluster tras una fusión."""
        self.embeddings.extend(otro.embeddings)
        self.medios |= otro.medios
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

    **Un cluster que supera el umbral le gana a cualquier noticia suelta, aunque
    la suelta se parezca más.** Es la lectura literal de lo que significa el
    umbral: si la nota se parece a un cluster por encima de él, pertenece a ese
    cluster. Quedarse con el mejor candidato global hacía que una suelta casi
    idéntica (0.96) le ganara a un cluster que ya era match válido (0.87) y
    naciera un cluster paralelo describiendo el mismo hecho.

    Eso era el origen de la fragmentación, no la fusión: medido sobre las 368
    noticias del día de mayor cobertura, el criterio anterior dejaba 50 clusters
    con 19 pares que después había que fusionar; prefiriendo el cluster quedan
    26 y **ninguno** que fusionar. Sin encadenamiento: el cluster más grande
    juntó 82 noticias y las 82 eran del mismo hecho. El centroide de un grupo
    grande y coherente es un atractor estable, y ningún tema ajeno le llega.

    Los embeddings están normalizados, así que el producto punto ES la
    similitud coseno.
    """
    sim_cluster = -1.0
    mejor_cluster: Optional[int] = None
    for cluster_id, cluster in clusters.items():
        sim = float(np.dot(embedding, cluster.centroide))
        if sim > sim_cluster:
            sim_cluster, mejor_cluster = sim, cluster_id

    if sim_cluster >= settings.UMBRAL_SIMILITUD:
        return sim_cluster, mejor_cluster, None

    sim_suelta = -1.0
    mejor_suelta: Optional[Noticia] = None
    for otra in sueltas:
        if otra.id == noticia_actual_id or otra.cluster_id is not None:
            continue
        sim = float(np.dot(embedding, np.array(otra.embedding, dtype=float)))
        if sim > sim_suelta:
            sim_suelta, mejor_suelta = sim, otra

    if sim_suelta > sim_cluster:
        return sim_suelta, None, mejor_suelta
    return sim_cluster, mejor_cluster, None


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
    candidatas = session.exec(
        select(Noticia)
        .where(
            Noticia.cluster_id.is_(None),
            Noticia.embedding.is_not(None),
            Noticia.fecha_publicacion >= desde,
        )
        .order_by(Noticia.fecha_publicacion)
    ).all()

    # Las notas que no reportan un hecho quedan afuera: no hay enfoques que
    # comparar. No se pierden —siguen guardadas y con su categoría— pero qué
    # hacer con ellas es del back-end. Ver `services/categorias.py`.
    sueltas = [n for n in candidatas if categoria_no_evento(n.url) is None]

    stats = {
        "evaluadas": len(sueltas),
        "de_otra_categoria": len(candidatas) - len(sueltas),
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


def _grupos_a_fusionar(clusters: Dict[int, _ClusterEnMemoria]) -> List[List[int]]:
    """
    Agrupa los ids de cluster que describen el mismo evento, por centroide.

    Todos los pares se evalúan contra la MISMA foto de centroides, sin
    recalcular después de cada unión. Recalcular reintroduce el encadenamiento
    que el centroide vino a evitar: el promedio de dos vectores distintos tiene
    norma < 1, así que al renormalizarlo la similitud contra todo lo demás se
    amplifica y cada fusión habilita la siguiente. Medido sobre datos reales,
    la versión que recalculaba terminó juntando 46 noticias en un solo cluster.
    """
    ids = sorted(clusters)
    padre = {id_cluster: id_cluster for id_cluster in ids}

    def raiz(id_cluster: int) -> int:
        while padre[id_cluster] != id_cluster:
            padre[id_cluster] = padre[padre[id_cluster]]
            id_cluster = padre[id_cluster]
        return id_cluster

    for posicion, id_a in enumerate(ids):
        for id_b in ids[posicion + 1 :]:
            sim = float(np.dot(clusters[id_a].centroide, clusters[id_b].centroide))
            if sim >= settings.UMBRAL_FUSION_CLUSTERS:
                padre[raiz(id_a)] = raiz(id_b)

    grupos: Dict[int, List[int]] = {}
    for id_cluster in ids:
        grupos.setdefault(raiz(id_cluster), []).append(id_cluster)

    return [sorted(grupo) for grupo in grupos.values() if len(grupo) > 1]


def fusionar_clusters_duplicados(session: Session) -> dict:
    """
    Une los clusters abiertos que quedaron cubriendo el mismo hecho.

    Aparecen porque la asignación es codiciosa: `_mejor_match` se queda con el
    mejor candidato global entre centroides y noticias sueltas, así que una
    suelta casi idéntica le gana a un cluster que ya era match válido y nace un
    cluster paralelo. Nada volvía a unirlos. Medido sobre datos reales: una sola
    muerte de alta cobertura dejó 20 clusters, con pares de centroides a 0.94
    entre sí, y partió un hecho de 4 medios en uno publicable y otro descartado.

    El cluster busca **cobertura, no precisión editorial**: junta todo lo que
    habla del hecho y deja que Fase 4 lo separe en ángulos leyendo los textos.
    Un cluster amplio y limpio es mejor materia prima que varios fragmentos,
    porque le da al modelo las coberturas de todos los medios juntas. Por eso
    fusionar de más no es un riesgo acá; ver specs/change_logs.md, Fase 3.

    Sobrevive el cluster más viejo para que una fusión no estire el plazo de
    cierre. Solo se tocan clusters abiertos: los cerrados ya pueden haber sido
    publicados.

    **Hoy es una red de seguridad, no el mecanismo principal.** La fragmentación
    se ataca en el origen, en `_mejor_match`, prefiriendo el cluster existente.
    Con eso, sobre el día de mayor cobertura las fusiones necesarias pasaron de
    19 a 0. Queda un hueco angosto que esto cubre: cuando una noticia se empareja
    con una suelta para crear un cluster, la suelta entra sin que se revise si
    ella misma pertenecía a un cluster ya existente. Requiere una combinación
    puntual de orden y geometría, y no se observó ninguna vez.

    Que empiece a fusionar seguido es señal de que la regla de asignación se
    rompió: sirve de canario.
    """
    ahora = datetime.utcnow()
    desde = ahora - timedelta(hours=settings.HORAS_CLUSTER_ABIERTO)

    en_memoria = _cargar_clusters_abiertos(session, desde)
    stats = {"evaluados": len(en_memoria), "fusionados": 0}

    # Se itera hasta el punto fijo. Cada fusión corre el centroide del
    # superviviente y puede acercarlo a un tercer cluster que antes no llegaba
    # al umbral, así que una sola vuelta deja el agrupamiento a mitad de camino.
    # Resolverlo acá hace que la función sea idempotente y que el pipeline
    # termine siempre en el mismo estado; si no, la consolidación se estiraría
    # a lo largo de varias corridas del scheduler y el resultado dependería de
    # cuántas alcanzaron a ejecutarse antes del cierre.
    grupos = _grupos_a_fusionar(en_memoria)
    while grupos:
        for grupo in grupos:
            miembros = sorted(
                (session.get(Cluster, id_cluster) for id_cluster in grupo),
                key=lambda c: (c.fecha_creacion, c.id),
            )
            superviviente, absorbidos = miembros[0], miembros[1:]

            for absorbido in absorbidos:
                for noticia in session.exec(
                    select(Noticia).where(Noticia.cluster_id == absorbido.id)
                ).all():
                    noticia.cluster_id = superviviente.id
                    session.add(noticia)

                # Las síntesis se mudan con su id intacto. Ese id es la clave de
                # idempotencia del webhook: borrarlas dejaría al backend con
                # ítems huérfanos, con sus likes y comentarios encima. Y sin
                # reasignarlas el borrado falla, porque SQLAlchemy intenta poner
                # `cluster_id` en NULL sobre una columna que no lo admite.
                for sintesis in session.exec(
                    select(Sintesis).where(Sintesis.cluster_id == absorbido.id)
                ).all():
                    sintesis.cluster_id = superviviente.id
                    session.add(sintesis)

                # El superviviente hereda la marca de síntesis más alta del par.
                # Como el cluster fusionado tiene más noticias que cualquiera de
                # sus partes, la marca queda por debajo del total, y eso es lo
                # que dispara la re-síntesis sobre el material ya unificado.
                marcas = [
                    c.noticias_al_sintetizar
                    for c in (superviviente, absorbido)
                    if c.noticias_al_sintetizar is not None
                ]
                if marcas:
                    superviviente.noticias_al_sintetizar = max(marcas)
                    session.add(superviviente)

                session.flush()
                session.delete(absorbido)
                en_memoria[superviviente.id].absorber(en_memoria.pop(absorbido.id))
                stats["fusionados"] += 1

            session.commit()

        grupos = _grupos_a_fusionar(en_memoria)

    logger.info(f"Fusión de clusters completada: {stats}")
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