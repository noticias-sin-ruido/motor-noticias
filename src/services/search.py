"""
Búsqueda semántica sobre las noticias ya vectorizadas.

Es el único lugar del motor donde se usa el KNN de pgvector: el clustering
compara contra centroides en memoria (unos cientos de vectores por ventana),
pero acá la consulta va contra toda la tabla, así que conviene resolverla en
la base con el operador `<=>` en vez de traer los vectores a Python.
"""
import logging
from typing import List, Optional

from sqlmodel import Session, select

from ..models import Medio, Noticia
from .vectorization import vectorizar_textos

logger = logging.getLogger(__name__)


def buscar_noticias_similares(
    session: Session,
    texto: str,
    limite: int = 10,
    solo_agrupadas: bool = False,
) -> List[dict]:
    """
    Devuelve las noticias semánticamente más parecidas al texto de consulta.

    El texto de la consulta se vectoriza con el mismo modelo que las noticias,
    para que ambos vivan en el mismo espacio vectorial. `solo_agrupadas` limita
    el resultado a noticias que ya pertenecen a un cluster.
    """
    embedding = vectorizar_textos([texto])[0]

    # `cosine_distance` genera el operador `<=>` de pgvector.
    # distancia = 1 - similitud coseno.
    distancia = Noticia.embedding.cosine_distance(embedding)

    consulta = (
        select(Noticia, Medio, distancia.label("distancia"))
        .join(Medio, Medio.id == Noticia.medio_id)
        .where(Noticia.embedding.is_not(None))
    )
    if solo_agrupadas:
        consulta = consulta.where(Noticia.cluster_id.is_not(None))

    filas = session.exec(consulta.order_by(distancia).limit(limite)).all()

    return [
        {
            "id": noticia.id,
            "titulo": noticia.titulo,
            "url": noticia.url,
            "medio": medio.nombre,
            "cluster_id": noticia.cluster_id,
            "fecha_publicacion": noticia.fecha_publicacion,
            "similitud": round(1 - float(dist), 4),
        }
        for noticia, medio, dist in filas
    ]


def listar_clusters(
    session: Session,
    estado: Optional[str] = None,
    limite: int = 20,
) -> List[dict]:
    """
    Lista los clusters con sus noticias y los medios que los cubrieron.

    Se ordena por fecha de creación descendente (lo más reciente primero), que
    es el orden en que le interesan a un consumidor del motor.
    """
    from ..models import Cluster  # import local para evitar ciclos

    consulta = select(Cluster)
    if estado:
        consulta = consulta.where(Cluster.estado == estado)

    clusters = session.exec(
        consulta.order_by(Cluster.fecha_creacion.desc()).limit(limite)
    ).all()

    resultado = []
    for cluster in clusters:
        filas = session.exec(
            select(Noticia, Medio)
            .join(Medio, Medio.id == Noticia.medio_id)
            .where(Noticia.cluster_id == cluster.id)
        ).all()

        resultado.append(
            {
                "id": cluster.id,
                "titulo_evento": cluster.titulo_evento,
                "estado": cluster.estado,
                "fecha_creacion": cluster.fecha_creacion,
                "cantidad_noticias": len(filas),
                "medios": sorted({medio.nombre for _, medio in filas}),
                "noticias": [
                    {
                        "id": noticia.id,
                        "titulo": noticia.titulo,
                        "url": noticia.url,
                        "medio": medio.nombre,
                    }
                    for noticia, medio in filas
                ],
            }
        )

    return resultado
