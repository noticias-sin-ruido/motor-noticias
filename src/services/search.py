"""
Las lecturas que la API expone: búsqueda semántica, clusters y síntesis.

Empezó siendo solo la búsqueda —y sigue siendo **el único lugar del motor
donde se usa el KNN de pgvector**: el clustering compara contra centroides en
memoria (unos cientos de vectores por ventana), pero acá la consulta va contra
toda la tabla, así que conviene resolverla en la base con el operador `<=>` en
vez de traer los vectores a Python.

Los listados de `Cluster` y `Sintesis` viven acá por lo que tienen en común
con ella y no con sus servicios: **son lecturas para afuera**, así que eligen
qué campos salen en vez de devolver la fila entera.
"""
import base64
import binascii
import logging
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from ..models import Medio, Noticia, Sintesis
from ..tiempo import iso_local
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
            # En hora argentina y con el offset a la vista. En la base está en
            # UTC; ver `src/tiempo.py` para por qué se guarda una y se muestra
            # la otra.
            "fecha_publicacion": iso_local(noticia.fecha_publicacion),
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

    # `selectinload` encadenado trae, en dos consultas adicionales (no una por
    # cluster), todas las noticias de todos los clusters y el medio de todas
    # esas noticias -- antes esto era 1 + N consultas, hasta 101 con
    # `limite=100`.
    consulta = select(Cluster).options(
        selectinload(Cluster.noticias).selectinload(Noticia.medio)
    )
    if estado:
        consulta = consulta.where(Cluster.estado == estado)

    clusters = session.exec(
        consulta.order_by(Cluster.fecha_creacion.desc()).limit(limite)
    ).all()

    # **Cuántas síntesis tiene cada cluster**, en una sola consulta agrupada
    # sobre los ids que ya están en la mano -- el mismo criterio que el
    # `selectinload` de arriba, por el que esto no es 1 + N.
    #
    # El campo existe porque sin él quien consume no puede saber si un cluster
    # ya está resuelto sin pedir `GET /sintesis` entera. **Medido el
    # 07/09/2026** desde la app: 438 síntesis eran 5 páginas y 201 ms antes de
    # dibujar una sola fila, creciendo a ~28 síntesis por día activo, para
    # responder una pregunta sobre los veinte clusters que se muestran. Acá el
    # costo es constante.
    ids = [c.id for c in clusters]
    conteo: dict = {}
    if ids:
        conteo = {
            cluster_id: cantidad
            for cluster_id, cantidad in session.exec(
                select(Sintesis.cluster_id, func.count(Sintesis.id))
                .where(Sintesis.cluster_id.in_(ids))
                .group_by(Sintesis.cluster_id)
            ).all()
        }

    resultado = []
    for cluster in clusters:
        noticias = cluster.noticias
        resultado.append(
            {
                "id": cluster.id,
                "titulo_evento": cluster.titulo_evento,
                "estado": cluster.estado,
                "fecha_creacion": iso_local(cluster.fecha_creacion),
                "cantidad_noticias": len(noticias),
                "cantidad_sintesis": conteo.get(cluster.id, 0),
                "medios": sorted({n.medio.nombre for n in noticias}),
                "noticias": [
                    {
                        "id": n.id,
                        "titulo": n.titulo,
                        "url": n.url,
                        "medio": n.medio.nombre,
                    }
                    for n in noticias
                ],
            }
        )

    return resultado


# --- Lectura de síntesis (backlog punto 14) ---------------------------------
#
# **El motor no sabía devolver lo que produce.** De sus cuatro `GET`, ninguno
# daba un ángulo: las síntesis salían solo empujadas por el webhook. Eso deja
# sin construir el modo paso a paso de la app —elegir, sintetizar y ver qué
# salió— así que estas dos funciones son su prerrequisito.
#
# Van dos y no una **a propósito**: una síntesis completa trae la comparativa
# entera, que son varios párrafos por medio, y en una lista de veinte eso es
# demasiado para lo que la lista necesita decidir. Es la misma distinción que
# la pantalla hace — una grilla para elegir, un panel para leer.


class CursorInvalido(ValueError):
    """El cursor que llegó no se puede decodificar. Lo traduce el endpoint."""


def _codificar_cursor(sintesis: Sintesis) -> str:
    """
    La posición de una síntesis en el orden, como texto opaco.

    **Opaco a propósito**: quien consume no tiene que poder construirlo a mano,
    porque eso congelaría el criterio de orden como si fuera parte del
    contrato. Hoy es `fecha|id` en base64; si mañana el orden cambia, cambia
    acá y nadie más se entera.
    """
    crudo = f"{sintesis.fecha_generacion.isoformat()}|{sintesis.id}"
    return base64.urlsafe_b64encode(crudo.encode()).decode()


def _decodificar_cursor(cursor: str) -> Tuple[datetime, int]:
    try:
        crudo = base64.urlsafe_b64decode(cursor.encode()).decode()
        fecha, id_ = crudo.split("|")
        return datetime.fromisoformat(fecha), int(id_)
    except (ValueError, TypeError, binascii.Error) as error:
        raise CursorInvalido(
            "El cursor no es válido. Se obtiene del campo `siguiente` de una "
            "respuesta anterior, no se arma a mano."
        ) from error


def listar_sintesis(
    session: Session,
    limite: int = 20,
    cursor: Optional[str] = None,
    cluster_id: Optional[int] = None,
    entregado: Optional[bool] = None,
) -> dict:
    """
    Las síntesis producidas, de la más reciente a la más vieja, paginadas.

    **Paginación por cursor y no por `offset`**, y el motivo es este caso de
    uso puntual: el scheduler corre cada 15 minutos e inserta síntesis
    **arriba**, así que con `offset` la página 2 repetiría lo que la página 1
    ya mostró. Con cursor la posición es un ítem y no un número, así que lo que
    se inserta arriba no corre nada.

    La comparación se escribe desarmada —`fecha < f OR (fecha = f AND id < i)`—
    y no como tupla, porque la suite corre sobre SQLite y la producción sobre
    Postgres: la forma con tupla no se comporta igual en los dos.

    Devuelve `{"sintesis": [...], "siguiente": cursor | None}`. `siguiente` en
    `None` significa que no hay más, y es lo que la app usa para dejar de pedir.
    """
    consulta = select(Sintesis).options(
        selectinload(Sintesis.noticias).selectinload(Noticia.medio)
    )

    if cluster_id is not None:
        consulta = consulta.where(Sintesis.cluster_id == cluster_id)
    if entregado is not None:
        consulta = consulta.where(Sintesis.enviado_backend == entregado)

    if cursor:
        fecha, id_ = _decodificar_cursor(cursor)
        consulta = consulta.where(
            or_(
                Sintesis.fecha_generacion < fecha,
                and_(Sintesis.fecha_generacion == fecha, Sintesis.id < id_),
            )
        )

    # Se pide uno de más para saber si hay página siguiente sin contar la tabla
    # entera. El de más no se devuelve: solo responde "¿hay más?".
    filas = session.exec(
        consulta.order_by(Sintesis.fecha_generacion.desc(), Sintesis.id.desc())
        .limit(limite + 1)
    ).all()

    hay_mas = len(filas) > limite
    filas = filas[:limite]

    return {
        "sintesis": [_resumen_de_sintesis(s) for s in filas],
        "siguiente": _codificar_cursor(filas[-1]) if hay_mas and filas else None,
    }


def _resumen_de_sintesis(sintesis: Sintesis) -> dict:
    """
    Lo que la lista necesita para que alguien elija, y nada más.

    **Sin `resumen_neutro`, `puntos_clave` ni `comparativa_enfoques`**: son el
    contenido, y el contenido se lee en el detalle. Meterlos acá haría que
    traer veinte ítems cueste lo mismo que leerlos todos.
    """
    return {
        "id": sintesis.id,
        "cluster_id": sintesis.cluster_id,
        "titulo_angulo": sintesis.titulo_angulo,
        "topicos": sintesis.topicos,
        "subtopicos": sintesis.subtopicos,
        "medios": sorted({n.medio.nombre for n in sintesis.noticias if n.medio}),
        "cantidad_notas": len(sintesis.noticias),
        "fecha_generacion": iso_local(sintesis.fecha_generacion),
        "modelo_usado": sintesis.modelo_usado,
        "enviado_backend": sintesis.enviado_backend,
    }


def detalle_de_sintesis(session: Session, sintesis_id: int) -> Optional[dict]:
    """
    Una síntesis entera, con su comparativa y sus fuentes. `None` si no existe.

    Es lo que el panel de lectura muestra, y lo único que justifica traer la
    comparativa completa: acá se está leyendo una, no eligiendo entre veinte.
    """
    sintesis = session.get(Sintesis, sintesis_id)
    if sintesis is None:
        return None

    return {
        **_resumen_de_sintesis(sintesis),
        "titulo_evento": sintesis.cluster.titulo_evento if sintesis.cluster else None,
        "resumen_neutro": sintesis.resumen_neutro,
        "puntos_clave": sintesis.puntos_clave,
        "comparativa_enfoques": sintesis.comparativa_enfoques,
        "fuentes": [
            {
                "id": n.id,
                "medio": n.medio.nombre if n.medio else None,
                "titulo": n.titulo,
                "url": n.url,
                "fecha_publicacion": iso_local(n.fecha_publicacion),
            }
            for n in sintesis.noticias
        ],
    }
