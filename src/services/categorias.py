"""
Clasificación de notas que no reportan un hecho.

Un horóscopo o una receta no son un evento, así que no entran al agrupamiento:
este motor compara cómo distintos medios encuadran *el mismo hecho*, y sin
hecho no hay enfoques que comparar. Sintetizarlas sería gasto sin producto.

**Dónde termina la responsabilidad de este repo.** Clasificar y dejarlas fuera
del agrupamiento, nada más. Qué se hace con ellas —si se publican como tag
suscribible, si se muestran como enlace, si se ignoran— es del back-end del
producto. El motor no las descarta ni las pierde: quedan guardadas, con su
categoría derivable de la URL y disponibles en `GET /search`.

Ver specs/change_logs.md, Fase 4.
"""
import re
from typing import Optional

from ..config import settings


def categoria_no_evento(url: str) -> Optional[str]:
    """
    Categoría de la nota si no reporta un hecho; `None` si es noticia común.

    Se busca en la **URL completa y no en el segmento de sección**, porque el
    segmento identifica el tópico y no el género: La Nación publicó "los tres
    signos con menos suerte" bajo `/tecnologia/` y Ciudad Magazine mete recetas
    en `/espectaculos/`. Una lista amplia de secciones se evaluó y se descartó
    por eso mismo.
    """
    for categoria, patron in settings.CATEGORIAS_NO_EVENTO.items():
        if re.search(patron, url, re.IGNORECASE):
            return categoria

    return None
