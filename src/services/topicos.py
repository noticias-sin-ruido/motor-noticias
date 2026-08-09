"""
Tópico de una publicación: de qué tema es.

Es lo que le permite al back-end armar secciones y filtros, así que la lista es
**cerrada**: si el valor pudiera ser texto libre, terminaríamos con "Deportes",
"deportes" y "Fútbol" conviviendo, y la navegación del producto se rompe sola.

Quién decide el tópico es el modelo, no este módulo. Acá vive la taxonomía y la
sección que cada medio declara en su URL, que entra al prompt **como pista**.
Es el mismo reparto que ya usamos con TF-IDF y NER: el cálculo señala, el
modelo juzga.

El motivo de no resolverlo por conteo de URLs está medido. Sobre las 11
publicaciones reales, normalizar las secciones sube el acuerdo entre medios de
3/11 a 8/11 — pero los 3 desacuerdos que quedan no son ruido:

    Muerte de Jorge Messi   TN -> deportes  |  Paparazzi -> teve
    Galperin contra CAME    La Nación -> política  |  El Cronista -> negocios

Los dos tienen razón, y que un medio lo trate como deporte y otro como
espectáculo **es encuadre editorial** — exactamente lo que el motor existe para
mostrar. Una votación por mayoría promediaría justo la señal del producto.

Ver specs/change_logs.md, Fase 4.
"""
from enum import Enum
from typing import Dict, Optional
from urllib.parse import urlparse


class Topico(str, Enum):
    """
    Taxonomía cerrada. Salud y educación entran en `sociedad`, y cultura en
    `espectaculos`: con 6 medios no juntan volumen propio (cultura apareció 3
    veces en 1.296 notas).

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


# Valor con el que el modelo dice "no hay un segundo tema".
NINGUNO = "ninguno"

# Los mismos valores más `ninguno`, para el tópico secundario.
#
# Se deriva de `Topico` en vez de escribirse a mano para que no se desincronicen:
# agregar una categoría arriba tiene que alcanzar. Y es un enum aparte, en vez de
# un campo opcional, porque el esquema de respuesta del modelo trata mucho mejor
# un enum obligatorio que uno nulable — con "ninguno" explícito no hay forma de
# que devuelva algo fuera de la lista.
TopicoSecundario = Enum(
    "TopicoSecundario",
    {**{t.name: t.value for t in Topico}, "NINGUNO": NINGUNO},
    type=str,
)


# Sección declarada por el medio en la URL -> tópico canónico.
#
# Cada medio nombra lo mismo distinto (`el-mundo` en La Nación es
# `internacional` en TN, `economia-politica` en El Cronista es `economia`), así
# que sin esta normalización la pista no sirve para nada.
#
# Cubre el 93,6% de las 1.296 notas medidas. Lo que queda afuera queda afuera a
# propósito: `actualidad` es el cajón de sastre de las revistas de espectáculos
# y `opinion` / `columnistas` son género. En esos casos el modelo decide sin
# pista, que es mejor que decidir con una pista equivocada.
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
    partes = [p for p in urlparse(url).path.split("/") if p]
    if not partes:
        return None
    return SECCIONES.get(partes[0].lower())
