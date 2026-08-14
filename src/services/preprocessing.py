"""
Evidencia medida sobre un cluster, para guiar la síntesis de Fase 4.

Produce **señales, no conclusiones**. El cálculo aporta recall (señala dónde
mirar: qué nombró un medio que los otros callaron, qué vocabulario usó solo él)
pero no distingue una decisión editorial de un artefacto: "omitió el nombre de
la denunciante" y "omitió Instagram" salen iguales de acá. Esa distinción es
criterio, y le corresponde al modelo de síntesis, que además tiene el cuerpo
del artículo delante para verificar cada pista.

Es el mismo reparto que en Fase 3: el cálculo agrupa, el modelo juzga. Ver
specs/change_logs.md, Fase 4.
"""
import logging
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import spacy
from sklearn.feature_extraction.text import TfidfVectorizer
from spacy.language import Language
from sqlalchemy import func
from sqlmodel import Session, select

from ..config import settings
from ..models import Cluster, Medio, Noticia

logger = logging.getLogger(__name__)

# Etiquetas de spaCy que corresponden a actores del hecho. Se descarta MISC,
# que en el modelo español mezcla obras, eventos y ruido de etiquetado.
ETIQUETAS_UTILES = {"PER", "ORG", "LOC"}

# Menciones que no reflejan una decisión editorial: aparecen porque el medio
# incrustó un posteo o porque quedaron en el pie de la nota.
PLATAFORMAS = {
    "instagram", "whatsapp", "twitter", "facebook", "tiktok", "youtube",
    "telegram", "google", "spotify", "netflix", "linkedin", "threads",
}

# Palabras que el NER en español confunde con nombres propios cuando abren
# oración, sobre todo los imperativos de los llamados a la acción ("Mirá el
# video"). La lista es observacional y crece con lo que aparezca en los datos.
MAL_ETIQUETADAS = {
    "mirá", "mira", "mirén", "leé", "lee", "mirar", "video", "foto", "fotos",
    "todo", "además", "según", "ahora", "también", "aunque", "entonces",
}

# Veces que una entidad tiene que aparecer para tomarla en serio. Con una sola
# mención al pasar, la mayoría de lo que queda son errores de etiquetado.
MIN_MENCIONES = 2

# Debajo de este tamaño de corpus, los filtros de frecuencia del TF-IDF no se
# pueden satisfacer y sklearn falla. Ver `get_vectorizador`.
CORPUS_MINIMO_PARA_FILTRAR = 20

_nlp: Optional[Language] = None
_vectorizador: Optional[TfidfVectorizer] = None
_corpus_entrenado: int = 0


def get_nlp() -> Language:
    """
    Devuelve el modelo de spaCy, cargándolo la primera vez que se usa.

    Perezoso por el mismo motivo que el de embeddings: son cientos de MB que no
    tiene por qué pagar quien no sintetiza.
    """
    global _nlp

    if _nlp is None:
        logger.info(f"Cargando modelo de spaCy: {settings.SPACY_MODEL}")
        # Solo se usa NER: apagar el resto del pipeline lo acelera bastante.
        _nlp = spacy.load(settings.SPACY_MODEL, disable=["lemmatizer", "textcat"])

    return _nlp


def get_vectorizador(session: Session) -> TfidfVectorizer:
    """
    Devuelve el TF-IDF ajustado sobre todo el corpus de noticias.

    El IDF **tiene que salir del corpus completo, no del cluster**: con tres o
    cuatro documentos no hay forma de saber qué palabra es rara, y el resultado
    son stopwords. Medido sobre un cluster real, ajustar el IDF adentro del
    cluster devolvía "no, le, pero, estaba" como términos distintivos; con el
    IDF del corpus, "matanza, virrey, juvenil, hermana, familiar".

    Se cachea y se reajusta recién cuando el corpus creció lo suficiente como
    para que los pesos cambien de verdad, porque reajustar es lo caro de acá.

    El tamaño del corpus se pide con `count()` y no trayendo los ids a Python:
    esta función se llama una vez por cluster sintetizado, así que traer del
    orden de miles de filas solo para un `len()` es un costo que se paga en
    cada síntesis aunque no haga falta reajustar nada.
    """
    global _vectorizador, _corpus_entrenado

    total = session.exec(select(func.count()).select_from(Noticia)).one()

    if _vectorizador is None or total > _corpus_entrenado * (1 + settings.TFIDF_REFIT_RATIO):
        logger.info(f"Ajustando TF-IDF sobre {total} noticias")
        corpus = [
            f"{n.titulo}. {n.contenido_limpio}"
            for n in session.exec(select(Noticia)).all()
        ]
        # Con un corpus chico los filtros de frecuencia se vuelven imposibles de
        # satisfacer (`min_df=3` y `max_df=0.4` requieren al menos 8 documentos)
        # y sklearn levanta ValueError. Pasa en una base recién sembrada, así que
        # se relajan: con tan pocas noticias el IDF vale poco igual.
        acotado = total >= CORPUS_MINIMO_PARA_FILTRAR
        _vectorizador = TfidfVectorizer(
            stop_words=list(spacy.blank("es").Defaults.stop_words),
            ngram_range=(1, 2),
            sublinear_tf=True,
            # Descarta lo que aparece en casi todas las noticias (sin valor
            # distintivo) y lo que aparece en muy pocas (ruido, erratas).
            min_df=3 if acotado else 1,
            max_df=0.4 if acotado else 1.0,
        )
        _vectorizador.fit(corpus)
        _corpus_entrenado = total

    return _vectorizador


def _es_ruido(texto: str, nombres_medios: Set[str]) -> bool:
    """Descarta plataformas, autorreferencias del medio y etiquetados dudosos."""
    limpio = texto.strip().lower()

    if len(limpio) <= 3 or limpio in PLATAFORMAS or limpio in MAL_ETIQUETADAS:
        return True
    # Una entidad real lleva mayúscula inicial; "negó" o "tenía" no.
    if not texto.strip()[0].isupper():
        return True
    return any(nombre in limpio for nombre in nombres_medios)


def _tokens_significativos(texto: str) -> Set[str]:
    return {t for t in re.findall(r"\w+", texto.lower()) if len(t) > 3}


def mapa_canonico(menciones: Counter) -> Dict[str, str]:
    """
    Mapea cada variante de una entidad a una forma canónica.

    NER devuelve a la misma persona escrita de varias formas ("Lara Agustina
    Ledesma", "Ledesma", y a veces mal leída como "Iara Agustina Ledesma"), y
    tratarlas como entidades distintas inventa omisiones que no existen: haría
    figurar que un medio calló un nombre que en realidad publicó con una errata.

    Se unifica por subcadena en cualquier dirección o por compartir dos tokens
    largos. Dos alcanzan para juntar "Iara Agustina Ledesma" con "Lara Agustina
    Ledesma" sin fusionar "Jorge Messi" con "Lionel Messi", que solo comparten
    el apellido.

    **El mapa se arma sobre las menciones de todos los medios juntos.** Hacerlo
    por medio no sirve: lo que hay que poder comparar es si dos medios nombran
    a la misma persona, y eso exige un vocabulario común.

    La forma canónica es la más mencionada, no la más larga: los subtítulos y
    epígrafes dejan variantes basura ("THIAGO MEDINA Y EL") que ganarían por
    longitud y quedarían representando a la entidad.
    """
    mapa: Dict[str, str] = {}
    tokens_canonicos: Dict[str, Set[str]] = {}

    for texto, _ in sorted(menciones.items(), key=lambda x: (-x[1], -len(x[0]))):
        tokens = _tokens_significativos(texto)
        destino = None

        for canonica, tokens_canonica in tokens_canonicos.items():
            comparte_dos = len(tokens & tokens_canonica) >= 2
            es_variante = (
                texto.lower() in canonica.lower() or canonica.lower() in texto.lower()
            )
            if comparte_dos or es_variante:
                destino = canonica
                break

        if destino is None:
            tokens_canonicos[texto] = tokens
            mapa[texto] = texto
        else:
            mapa[texto] = destino

    return mapa


def _menciones_crudas(
    noticias: Sequence[Noticia], nombres_medios: Set[str]
) -> Counter:
    """Entidades detectadas en un conjunto de notas, sin unificar todavía."""
    nlp = get_nlp()
    menciones: Counter = Counter()

    for noticia in noticias:
        texto = f"{noticia.titulo}. {noticia.contenido_limpio[:settings.NER_CHARS_CUERPO]}"
        for entidad in nlp(texto).ents:
            if entidad.label_ not in ETIQUETAS_UTILES:
                continue
            crudo = entidad.text.strip()
            # Los epígrafes y volantas vienen en mayúsculas: pasarlos a formato
            # de título los deja unificables con la mención normal del cuerpo.
            if crudo.isupper() and len(crudo.split()) > 1:
                crudo = crudo.title()
            if not _es_ruido(crudo, nombres_medios):
                menciones[crudo] += 1

    return menciones


def _entidades_por_medio(
    noticias_por_medio: Dict[str, List[Noticia]], nombres_medios: Set[str]
) -> Dict[str, Dict[str, int]]:
    """Entidades que menciona cada medio, unificadas con un vocabulario común."""
    crudas = {
        medio: _menciones_crudas(noticias, nombres_medios)
        for medio, noticias in noticias_por_medio.items()
    }

    total: Counter = Counter()
    for menciones in crudas.values():
        total.update(menciones)
    mapa = mapa_canonico(total)

    resultado: Dict[str, Dict[str, int]] = {}
    for medio, menciones in crudas.items():
        unificadas: Counter = Counter()
        for texto, veces in menciones.items():
            unificadas[mapa[texto]] += veces
        resultado[medio] = {
            texto: veces for texto, veces in unificadas.items() if veces >= MIN_MENCIONES
        }

    return resultado


def _terminos_propios(
    textos_por_medio: Dict[str, str],
    vectorizador: TfidfVectorizer,
    cuantos: int,
    nombres_medios: Set[str],
) -> Tuple[Dict[str, List[str]], np.ndarray, np.ndarray]:
    """
    Términos que cada medio usa y los demás no, por diferencia de pesos TF-IDF.

    Se descartan los términos que nombran al propio medio: el pie de página
    ("seguinos en el canal de WhatsApp de Ciudad Magazine") es constante en
    todas sus notas y sale como su rasgo más distintivo, cuando no dice nada
    sobre cómo cubrió el hecho.

    Devuelve también la matriz y el vocabulario porque el núcleo común sale de
    los mismos pesos y no tiene sentido recalcularlos.
    """
    descartables = {
        palabra for nombre in nombres_medios for palabra in nombre.split() if len(palabra) > 3
    } | PLATAFORMAS

    def es_autorreferencia(termino: str) -> bool:
        return any(palabra in termino for palabra in descartables)

    medios = list(textos_por_medio)
    matriz = vectorizador.transform([textos_por_medio[m] for m in medios]).toarray()
    vocabulario = vectorizador.get_feature_names_out()

    propios: Dict[str, List[str]] = {}
    for indice, medio in enumerate(medios):
        otros = np.delete(matriz, indice, axis=0).mean(axis=0)
        diferencia = matriz[indice] - otros
        ordenados = sorted(zip(vocabulario, diferencia), key=lambda x: -x[1])
        propios[medio] = [
            termino
            for termino, peso in ordenados
            if peso > 0 and not es_autorreferencia(termino)
        ][:cuantos]

    return propios, matriz, vocabulario


def seleccionar_representativas(
    noticias: Sequence[Noticia], piso_por_medio: int, maximo: int
) -> List[Noticia]:
    """
    Notas que se le mandan al modelo: un piso por medio y un techo global.

    Primero entran las `piso_por_medio` notas más cercanas al centroide de cada
    medio — que **ningún medio quede afuera** es lo único que no se puede
    resignar, porque sin su voz no hay comparativa. Después se completa hasta
    `maximo` por rondas entre los medios, tomando de cada uno su siguiente nota
    más representativa.

    El reparto por rondas y no por cercanía global evita que un medio prolífico
    se coma todo el cupo restante: con 46 notas y 5 medios, quedarse con las 20
    mejores en bruto podía dejar el material sesgado hacia el que más publicó.

    Si el piso ya supera el techo (muchos medios), **gana el piso**: perder un
    medio cuesta más que unos tokens de más.

    Mandar de menos no es gratis. Medido sobre un cluster de 14 notas: con 6 el
    modelo encontró 1 ángulo, con 9 encontró 1, y con las 14 encontró 3
    publicables. El recorte no solo ahorraba costo, recortaba producto.
    """
    con_embedding = [n for n in noticias if n.embedding is not None]
    if not con_embedding:
        return list(noticias)[:maximo]

    centroide = np.array(
        [n.embedding for n in con_embedding], dtype=float
    ).mean(axis=0)
    norma = np.linalg.norm(centroide)
    if norma:
        centroide = centroide / norma

    agrupadas: Dict[int, List[Noticia]] = defaultdict(list)
    for noticia in con_embedding:
        agrupadas[noticia.medio_id].append(noticia)
    for grupo in agrupadas.values():
        grupo.sort(
            key=lambda n: -float(np.dot(np.array(n.embedding, dtype=float), centroide))
        )

    elegidas: List[Noticia] = []
    for grupo in agrupadas.values():
        elegidas.extend(grupo[:piso_por_medio])

    posicion = piso_por_medio
    while len(elegidas) < maximo:
        sumo_alguna = False
        for grupo in agrupadas.values():
            if len(elegidas) >= maximo:
                break
            if len(grupo) > posicion:
                elegidas.append(grupo[posicion])
                sumo_alguna = True
        if not sumo_alguna:
            break
        posicion += 1

    return elegidas


def construir_evidencia(session: Session, cluster: Cluster) -> dict:
    """
    Señales medidas sobre un cluster, listas para inyectar en el prompt.

    Devuelve el núcleo compartido (lo que todos los medios sostienen) y, por
    cada medio, sus titulares, su vocabulario propio, lo que menciona en
    exclusiva y lo que omite habiéndolo dicho otro.

    Nada de esto es una conclusión: son candidatos a verificar contra el cuerpo
    del artículo. Ver el encabezado del módulo.

    De paso trae `medios_por_id` y `total_noticias`: `sintetizar_cluster` los
    necesita después (para el prompt y para la marca del cluster) y, como esta
    función ya hizo esas dos consultas, reusarlas evita pedirlas de nuevo por
    cada cluster que se sintetiza.
    """
    noticias = session.exec(
        select(Noticia).where(Noticia.cluster_id == cluster.id)
    ).all()
    medios = {m.id: m.nombre for m in session.exec(select(Medio)).all()}
    if not noticias:
        return {
            "cluster_id": cluster.id,
            "medios": [],
            "nucleo_comun": {},
            "por_medio": {},
            "medios_por_id": medios,
            "total_noticias": 0,
        }

    nombres_medios = {nombre.lower() for nombre in medios.values()}

    # El medio es la unidad de comparación, no el artículo: un medio puede haber
    # publicado varias notas del mismo hecho y todas expresan su mismo enfoque.
    seleccionadas = seleccionar_representativas(
        noticias, settings.SINTESIS_NOTAS_POR_MEDIO, settings.SINTESIS_MAX_NOTAS
    )
    por_medio: Dict[str, List[Noticia]] = defaultdict(list)
    for noticia in seleccionadas:
        por_medio[medios[noticia.medio_id]].append(noticia)

    entidades = _entidades_por_medio(por_medio, nombres_medios)
    textos = {
        medio: " ".join(f"{n.titulo}. {n.contenido_limpio}" for n in notas)
        for medio, notas in por_medio.items()
    }

    if len(textos) > 1:
        propios, matriz, vocabulario = _terminos_propios(
            textos,
            get_vectorizador(session),
            settings.TFIDF_TERMINOS_POR_MEDIO,
            nombres_medios,
        )
        compartidos = [
            termino
            for termino, peso in sorted(
                zip(vocabulario, matriz.min(axis=0)), key=lambda x: -x[1]
            )[: settings.TFIDF_TERMINOS_POR_MEDIO]
            if peso > 0
        ]
    else:
        propios, compartidos = {medio: [] for medio in textos}, []

    conjuntos = {medio: set(ents) for medio, ents in entidades.items()}
    todas = set().union(*conjuntos.values()) if conjuntos else set()
    comunes = set.intersection(*conjuntos.values()) if conjuntos else set()

    detalle = {}
    for medio, notas in por_medio.items():
        ajenas = set().union(*(c for m, c in conjuntos.items() if m != medio)) if len(conjuntos) > 1 else set()
        detalle[medio] = {
            "articulos": len(notas),
            "titulares": [n.titulo for n in notas],
            "terminos_propios": propios.get(medio, []),
            "entidades_exclusivas": sorted(conjuntos[medio] - ajenas),
            "entidades_omitidas": sorted(todas - conjuntos[medio]),
        }

    return {
        "cluster_id": cluster.id,
        "medios": sorted(por_medio),
        "nucleo_comun": {
            "entidades": sorted(comunes),
            "terminos": compartidos,
        },
        "por_medio": detalle,
        "noticias": seleccionadas,
        "medios_por_id": medios,
        "total_noticias": len(noticias),
    }
