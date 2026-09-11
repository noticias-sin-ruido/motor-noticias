"""
Composición de los clusters por medio: con quién se junta cada uno, y cuándo no.

Responde una pregunta de producto, no de operación: **qué medio conviene
sumar**. Un cluster necesita `MIN_MEDIOS_CLUSTER` medios distintos para
sintetizarse (`services/clustering.py`), así que el material que un medio
produce solo no llega a publicarse nunca. Saber cuánto de eso hay, y de qué
tema es, es lo que convierte "sumemos medios" en "sumemos este medio".

**De dónde sale el tópico, y por qué no del lugar obvio.** Los `topicos` viven
en `Sintesis` y no en `Cluster`, así que justo los clusters que interesan acá
—los de un solo medio— **no tienen tópico**: nunca se sintetizaron. El dato se
deriva entonces de la sección que el medio declara en su URL, con
`topicos.topico_declarado`, que ya existe y no cuesta una llamada al modelo.

Ese módulo documenta que el método es flojo **porque los medios se contradicen
entre sí** (la muerte de Jorge Messi: TN la pone en deportes, Paparazzi en teve).
**Esa debilidad no aplica acá**: en un cluster de un medio solo no hay con quién
contradecirse. Es el único lugar del motor donde la sección declarada se usa
como respuesta y no como pista, y es legítimo por esa razón y nada más.

Medido el 11/09/2026 contra la base real: 688 clusters, 130 solos, 398 con dos
medios, 160 con más de dos, sobre ocho medios cargados. De los 130 solos, 111
tienen tópico derivable y 19 no.
"""

from collections import Counter
from typing import Dict, List, Optional, Set, Tuple

from sqlmodel import Session, select

from ..config import settings
from ..models.medio import Medio
from ..models.noticia import Noticia
from .topicos import topico_declarado


def _composicion(
    session: Session,
) -> Tuple[Dict[int, Set[int]], Dict[int, List[str]]]:
    """
    Qué medios toca cada cluster, y con qué URLs.

    Una sola consulta para las dos cosas: recorrer las noticias agrupadas es
    barato (5.390 filas en la base real) y hacerlo dos veces no compra nada.
    """
    medios_por_cluster: Dict[int, Set[int]] = {}
    urls_por_cluster: Dict[int, List[str]] = {}

    filas = session.exec(
        select(Noticia.cluster_id, Noticia.medio_id, Noticia.url).where(
            Noticia.cluster_id.is_not(None)
        )
    ).all()

    for cluster_id, medio_id, url in filas:
        medios_por_cluster.setdefault(cluster_id, set()).add(medio_id)
        urls_por_cluster.setdefault(cluster_id, []).append(url)

    return medios_por_cluster, urls_por_cluster


def _topico_del_cluster(urls: List[str]) -> Optional[str]:
    """
    El tópico que predomina entre las URLs de un cluster, o `None`.

    `None` no es un error: hay secciones que la taxonomía no mapea y notas
    publicadas en la raíz del sitio. Se informa aparte en vez de repartirlas en
    una categoría "otros", que las escondería adentro de un número.
    """
    cuenta = Counter()
    for url in urls:
        declarado = topico_declarado(url)
        if declarado:
            cuenta[declarado.value] += 1
    if not cuenta:
        return None
    return cuenta.most_common(1)[0][0]


def panel_de_medios(session: Session) -> dict:
    """
    El panel completo: totales, y una fila por medio.

    **Los medios deshabilitados van igual.** Su material sigue en la base y
    sigue contando la historia de qué aportaban — un medio se apaga, entre otras
    cosas, justamente después de mirar este panel.
    """
    minimo = settings.MIN_MEDIOS_CLUSTER
    medios_por_cluster, urls_por_cluster = _composicion(session)

    totales = Counter()
    for medios in medios_por_cluster.values():
        totales[_balde(len(medios), minimo)] += 1

    filas = []
    for medio in session.exec(select(Medio).order_by(Medio.nombre)).all():
        por_balde = Counter()
        topicos = Counter()
        sin_topico = 0

        for cluster_id, medios in medios_por_cluster.items():
            if medio.id not in medios:
                continue
            balde = _balde(len(medios), minimo)
            por_balde[balde] += 1
            if balde != "solo":
                continue
            topico = _topico_del_cluster(urls_por_cluster[cluster_id])
            if topico:
                topicos[topico] += 1
            else:
                sin_topico += 1

        filas.append(
            {
                "medio_id": medio.id,
                "nombre": medio.nombre,
                "activo": medio.activo,
                "clusters": sum(por_balde.values()),
                "solo": por_balde["solo"],
                "con_el_minimo": por_balde["con_el_minimo"],
                "sobre_el_minimo": por_balde["sobre_el_minimo"],
                # Ordenado por cantidad: es una lista para leer de arriba hacia
                # abajo, no un diccionario para buscar una clave.
                "topicos_cuando_esta_solo": [
                    {"topico": t, "clusters": n} for t, n in topicos.most_common()
                ],
                "sin_topico_cuando_esta_solo": sin_topico,
            }
        )

    return {
        # **Se informa el mínimo, y no se asume que sea 2.** La app dice "esto no
        # se puede sintetizar" sobre el balde `solo`, y esa frase es cierta
        # porque el balde se calcula contra este número. Si algún día
        # `MIN_MEDIOS_CLUSTER` cambia, la etiqueta sigue siendo verdad sin tocar
        # la interfaz.
        "minimo_para_sintetizar": minimo,
        "clusters": {
            "total": len(medios_por_cluster),
            "solo": totales["solo"],
            "con_el_minimo": totales["con_el_minimo"],
            "sobre_el_minimo": totales["sobre_el_minimo"],
        },
        "medios": filas,
    }


def _balde(cantidad_de_medios: int, minimo: int) -> str:
    """
    En cuál de los tres grupos cae un cluster.

    Los nombres son relativos al mínimo y no al número 2 **a propósito**: el
    grupo que importa es "no alcanza para sintetizar", y eso lo define
    `MIN_MEDIOS_CLUSTER`, no una constante repetida acá.
    """
    if cantidad_de_medios < minimo:
        return "solo"
    if cantidad_de_medios == minimo:
        return "con_el_minimo"
    return "sobre_el_minimo"
