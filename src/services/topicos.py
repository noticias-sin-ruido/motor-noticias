"""
Tópico y subtópico de una publicación: de qué tema es, con qué detalle.

Es lo que le permite al back-end armar secciones y filtros, así que las dos
listas son **cerradas**: si el valor pudiera ser texto libre, terminaríamos con
"Deportes", "deportes" y "Fútbol" conviviendo, y la navegación del producto se
rompe sola.

Quién decide el tópico es el modelo, no este módulo. Acá vive la taxonomía y la
sección que cada medio declara en su URL, que entra al prompt **como pista**.
Es el mismo reparto que ya usamos con TF-IDF y NER: el cálculo señala, el
modelo juzga.

El motivo de no resolverlo por conteo de URLs está medido. Sobre las 11
publicaciones reales de la primera medición, normalizar las secciones sube el
acuerdo entre medios de 3/11 a 8/11 — pero los 3 desacuerdos que quedan no son
ruido:

    Muerte de Jorge Messi   TN -> deportes  |  Paparazzi -> teve
    Galperin contra CAME    La Nación -> política  |  El Cronista -> negocios

Los dos tienen razón, y que un medio lo trate como deporte y otro como
espectáculo **es encuadre editorial** — exactamente lo que el motor existe para
mostrar. Una votación por mayoría promediaría justo la señal del producto.

Ver specs/change_logs.md, Fase 4 y el rediseño de Fase 5.
"""
from enum import Enum
from typing import Dict, List, Optional
from urllib.parse import urlparse


class Topico(str, Enum):
    """
    Taxonomía cerrada de 10 categorías. Salud y educación entran en `sociedad`,
    y cultura en `espectaculos`: con 6 medios no juntan volumen propio (cultura
    apareció 3 veces en 1.296 notas).

    No hay `opinion` ni `columnistas` a propósito. Eso es **género**, no tema:
    una columna sobre inflación es economía. Es la misma distinción que ya
    aplicamos con el horóscopo, sólo que al revés.
    """

    POLITICA = "politica"
    ECONOMIA = "economia"
    SOCIEDAD = "sociedad"
    POLICIALES = "policiales"
    INTERNACIONAL = "internacional"
    DEPORTES = "deportes"
    ESPECTACULOS = "espectaculos"
    TECNOLOGIA = "tecnologia"
    CIENCIA = "ciencia"
    LIFESTYLE = "lifestyle"


class Subtopico(str, Enum):
    """
    Recorte más fino DENTRO de un tópico -- fútbol es un subtópico de deportes,
    no un tópico en sí. Enum plano y único (no uno por categoría) a propósito:
    Gemini estructurado no puede acotar un enum según el valor de otro campo,
    así que separarlo por categoría no evitaría la validación en código y sólo
    complicaría el prompt. La jerarquía real vive en `SUBTOPICO_PADRE`.

    Lista cerrada, mayormente medida y no inventada: cada entrada tiene volumen
    real verificado contra la base (ver specs/change_logs.md, Fase 5 --
    rediseño de tópicos), salvo `SALUD` y `EDUCACION`, sumadas por decisión
    editorial explícita pese a volumen bajo o nulo en el corpus medido: son
    categorías que alguien busca específicamente, y no tenerlas de entrada
    habría dejado a esas búsquedas sin filtro fino desde el día uno. Mismo
    criterio ya aplicado a los deportes minoritarios.

    Categorías sin una sección de URL que se distinga con fuerza de la
    categoría misma (política, policiales, tecnología, ciencia) se quedan sin
    subtópicos por ahora: es preferible que el modelo no elija nada a que
    elija de una lista sin respaldo.
    """

    # -- deportes --
    FUTBOL = "futbol"
    RUGBY = "rugby"
    HOCKEY = "hockey"
    TENIS = "tenis"
    AUTOMOVILISMO = "automovilismo"
    BASQUETBOL = "basquetbol"
    # -- espectaculos --
    TEVE = "teve"
    MUSICA = "musica"
    CINE = "cine"
    CHIMENTOS = "chimentos"
    # -- economia --
    NEGOCIOS = "negocios"
    CAMPO = "campo"
    # -- internacional --
    ESTADOS_UNIDOS = "estados_unidos"
    # -- sociedad --
    SALUD = "salud"
    EDUCACION = "educacion"
    # -- lifestyle --
    PROPIEDADES = "propiedades"
    AUTOS = "autos"
    COCINA = "cocina"


# A qué tópico pertenece cada subtópico. Es la jerarquía que el modelo NO
# controla: después de que responde, el código se fija si el tópico padre de
# cada subtópico elegido está entre los tópicos de la publicación, y si no
# está, lo agrega. Así nunca puede quedar un subtópico "huérfano" -- ver
# `con_padres_completos` más abajo.
SUBTOPICO_PADRE: Dict[Subtopico, Topico] = {
    Subtopico.FUTBOL: Topico.DEPORTES,
    Subtopico.RUGBY: Topico.DEPORTES,
    Subtopico.HOCKEY: Topico.DEPORTES,
    Subtopico.TENIS: Topico.DEPORTES,
    Subtopico.AUTOMOVILISMO: Topico.DEPORTES,
    Subtopico.BASQUETBOL: Topico.DEPORTES,
    Subtopico.TEVE: Topico.ESPECTACULOS,
    Subtopico.MUSICA: Topico.ESPECTACULOS,
    Subtopico.CINE: Topico.ESPECTACULOS,
    Subtopico.CHIMENTOS: Topico.ESPECTACULOS,
    Subtopico.NEGOCIOS: Topico.ECONOMIA,
    Subtopico.CAMPO: Topico.ECONOMIA,
    Subtopico.ESTADOS_UNIDOS: Topico.INTERNACIONAL,
    Subtopico.SALUD: Topico.SOCIEDAD,
    Subtopico.EDUCACION: Topico.SOCIEDAD,
    Subtopico.PROPIEDADES: Topico.LIFESTYLE,
    Subtopico.AUTOS: Topico.LIFESTYLE,
    Subtopico.COCINA: Topico.LIFESTYLE,
}


def con_padres_completos(
    topicos: List[Topico], subtopicos: List[Subtopico]
) -> List[Topico]:
    """
    Devuelve `topicos` con el tópico padre de cada subtópico agregado si
    faltaba. Es la garantía mecánica de que nunca se publica un subtópico sin
    su categoría: no depende de que el modelo lo haya hecho bien, lo asegura
    el código después. Puede devolver más de 2 tópicos en el caso límite de
    que el modelo haya llenado el tope de 2 sin incluir el padre de un
    subtópico elegido -- se prioriza la consistencia sobre el tope, que es una
    guía de prompt y no una regla dura.
    """
    resultado = list(topicos)
    for subtopico in subtopicos:
        padre = SUBTOPICO_PADRE[subtopico]
        if padre not in resultado:
            resultado.append(padre)
    return resultado


# Sección declarada por el medio en la URL -> tópico canónico.
#
# Cada medio nombra lo mismo distinto (`el-mundo` en La Nación es
# `internacional` en TN, `economia-politica` en El Cronista es `economia`), así
# que sin esta normalización la pista no sirve para nada.
#
# Cubre el 93,6% de las 1.296 notas medidas originalmente. Lo que queda afuera
# queda afuera a propósito: `actualidad` es el cajón de sastre de las revistas
# de espectáculos y `opinion` / `columnistas` son género. En esos casos el
# modelo decide sin pista, que es mejor que decidir con una pista equivocada.
SECCIONES: Dict[str, Topico] = {
    # Política
    "politica": Topico.POLITICA,
    # Economía
    "economia": Topico.ECONOMIA,
    "economia-politica": Topico.ECONOMIA,
    "negocios": Topico.ECONOMIA,
    "finanzas": Topico.ECONOMIA,
    "dinero": Topico.ECONOMIA,
    "financial-times": Topico.ECONOMIA,
    "transport-cargo": Topico.ECONOMIA,
    "campo": Topico.ECONOMIA,
    # Sociedad
    "sociedad": Topico.SOCIEDAD,
    "salud": Topico.SOCIEDAD,
    "educacion": Topico.SOCIEDAD,
    "comunidad": Topico.SOCIEDAD,
    "clima": Topico.SOCIEDAD,
    "feriados": Topico.SOCIEDAD,
    # Policiales
    "policiales": Topico.POLICIALES,
    "seguridad": Topico.POLICIALES,
    # Internacional
    "internacional": Topico.INTERNACIONAL,
    "el-mundo": Topico.INTERNACIONAL,
    "estados-unidos": Topico.INTERNACIONAL,
    "usa": Topico.INTERNACIONAL,
    # Deportes
    "deportes": Topico.DEPORTES,
    "polideportivo": Topico.DEPORTES,
    "futbol": Topico.DEPORTES,
    "running": Topico.DEPORTES,
    # Espectáculos
    "espectaculos": Topico.ESPECTACULOS,
    "show": Topico.ESPECTACULOS,
    "teve": Topico.ESPECTACULOS,
    "tvshow": Topico.ESPECTACULOS,
    "romances": Topico.ESPECTACULOS,
    "famosos": Topico.ESPECTACULOS,
    "entretenimiento": Topico.ESPECTACULOS,
    "cine-y-series": Topico.ESPECTACULOS,
    "musica": Topico.ESPECTACULOS,
    "galerias": Topico.ESPECTACULOS,
    "cultura": Topico.ESPECTACULOS,
    # Tecnología
    "tecno": Topico.TECNOLOGIA,
    "tecnologia": Topico.TECNOLOGIA,
    "infotechnology": Topico.TECNOLOGIA,
    # Ciencia
    "ciencia": Topico.CIENCIA,
    # Lifestyle
    "lifestyle": Topico.LIFESTYLE,
    "estilo": Topico.LIFESTYLE,
    "bienestar": Topico.LIFESTYLE,
    "moda-y-belleza": Topico.LIFESTYLE,
    "autos": Topico.LIFESTYLE,
    "autos-y-motos": Topico.LIFESTYLE,
    "propiedades": Topico.LIFESTYLE,
    "cocina": Topico.LIFESTYLE,
    "recetas": Topico.LIFESTYLE,
    "turismo": Topico.LIFESTYLE,
    "viajes": Topico.LIFESTYLE,
    "revista-lugares": Topico.LIFESTYLE,
    "clase": Topico.LIFESTYLE,
}


# Sección declarada (en cualquier segmento de la URL, no sólo el primero) ->
# subtópico canónico. A diferencia de `SECCIONES`, acá interesa el segundo
# segmento tanto como el primero: medido sobre la base real, el 87% de las
# notas de deportes con un segundo segmento útil lo tienen en
# `/deportes/futbol/...`, no en `/futbol/...`. Ver `subtopico_declarado`.
SUBSECCIONES: Dict[str, Subtopico] = {
    "futbol": Subtopico.FUTBOL,
    "futbol-internacional": Subtopico.FUTBOL,
    "rugby": Subtopico.RUGBY,
    "hockey": Subtopico.HOCKEY,
    "tenis": Subtopico.TENIS,
    "automovilismo": Subtopico.AUTOMOVILISMO,
    "basquetbol": Subtopico.BASQUETBOL,
    "teve": Subtopico.TEVE,
    "tvshow": Subtopico.TEVE,
    "musica": Subtopico.MUSICA,
    "cine": Subtopico.CINE,
    "cine-y-series": Subtopico.CINE,
    "romances": Subtopico.CHIMENTOS,
    "personajes": Subtopico.CHIMENTOS,
    "famosos": Subtopico.CHIMENTOS,
    "negocios": Subtopico.NEGOCIOS,
    "campo": Subtopico.CAMPO,
    "estados-unidos": Subtopico.ESTADOS_UNIDOS,
    "usa": Subtopico.ESTADOS_UNIDOS,
    "salud": Subtopico.SALUD,
    "educacion": Subtopico.EDUCACION,
    "propiedades": Subtopico.PROPIEDADES,
    "autos": Subtopico.AUTOS,
    "autos-y-motos": Subtopico.AUTOS,
    "cocina": Subtopico.COCINA,
    "recetas": Subtopico.COCINA,
}


def _primeros_segmentos(url: str, cantidad: int) -> List[str]:
    partes = [p.lower() for p in urlparse(url).path.split("/") if p]
    return partes[:cantidad]


def topico_declarado(url: str) -> Optional[Topico]:
    """
    En qué sección publicó el medio esta nota, traducida a la taxonomía común.

    `None` cuando la sección no está mapeada o la URL no tiene ruta. Devolver
    `None` es una respuesta válida, no una falla: es preferible que el modelo
    decida sin pista antes que darle una equivocada.

    A diferencia de `categorias.categoria_no_evento`, que busca en la URL
    entera, acá se mira **solo el primer segmento de la ruta**. Es la diferencia
    entre las dos preguntas: el género (un horóscopo) puede aparecer en
    cualquier sección, pero el tema es justamente lo que la sección declara.
    """
    segmentos = _primeros_segmentos(url, 1)
    if not segmentos:
        return None
    return SECCIONES.get(segmentos[0])


def subtopico_declarado(url: str) -> Optional[Subtopico]:
    """
    Igual que `topico_declarado`, pero para el subtópico y mirando los dos
    primeros segmentos de la ruta, no sólo el primero: el detalle suele venir
    un nivel más adentro (`/deportes/futbol/...`), aunque algún medio podría
    algún día publicarlo directo en el primero (`/futbol/...`).

    `None` es una respuesta válida: la mayoría de las notas no tiene un
    segundo segmento reconocido, y es mejor que el modelo decida sin pista.
    """
    for segmento in _primeros_segmentos(url, 2):
        subtopico = SUBSECCIONES.get(segmento)
        if subtopico is not None:
            return subtopico
    return None
