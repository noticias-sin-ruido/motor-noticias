"""
Síntesis neutra por ángulo, con el modelo que el operador haya configurado.

La unidad que se publica NO es el cluster sino el **ángulo**: el clustering
junta el hecho y toda su cobertura buscando no perder nada, y separar ese
material en ángulos distintos (el hecho, sus consecuencias, las reacciones)
exige leer los textos. Un cluster produce varias síntesis.

El modelo recibe los cuerpos completos de las notas más representativas de cada
medio junto con la evidencia medida por `preprocessing`. Esa evidencia son
pistas a verificar, no conclusiones: el cálculo no distingue "omitió el nombre
de la denunciante" de "omitió Instagram", y esa distinción es criterio.

Ver specs/change_logs.md, Fase 4, para el detalle de las decisiones.
"""
import html
import logging
import re
import unicodedata
from datetime import timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field as PydanticField, field_validator
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import settings
from ..tiempo import ahora_utc
from ..models import (
    Cluster,
    ModeloIA,
    Noticia,
    PublicacionRedes,
    Sintesis,
    SintesisNoticia,
)
from .alerts import enviar_alerta
from .clustering import ESTADO_ABIERTO, ESTADO_PROCESADO
from .modelos import cadena_de_modelos, construir, modelo_activo
from .preprocessing import construir_evidencia
from .proveedores import (
    AdaptadorNoImplementado,
    ErrorDeProveedor,
    ProveedorNoConfigurado,
    RespuestaBloqueada,
)
from .topicos import (
    Subtopico,
    Topico,
    con_padres_completos,
    subtopico_declarado,
    topico_declarado,
)

logger = logging.getLogger(__name__)

# Marca de `Cluster.noticias_al_sintetizar` para "caducó sin intentarse nunca".
#
# Es un valor imposible como conteo, y eso es a propósito: distingue el descarte
# por caducidad de un intento real, así que subir `HORAS_MAXIMAS_SIN_SINTETIZAR`
# vuelve a poner esos clusters en carrera. Con el conteo real, la recuperación
# que recomienda la alerta no haría nada.
MARCA_CADUCADO = -1

# --- Presupuesto de un tweet ------------------------------------------------
#
# El copy de redes tiene que entrar en un posteo de X junto con los hashtags y
# la URL al back-end. Tres reglas del conteo de X que definen los números:
#
#   - El límite es de 280 "weighted characters": los codepoints 0-4351 pesan 1
#     (todo el español, tildes y ñ incluidas) y el resto pesa 2 (emoji).
#   - **Cualquier URL cuenta 23 caracteres fijos**, sin importar su largo real,
#     porque X la envuelve en t.co. La URL al back-end entra siempre por 23.
#   - Los separadores del posteo (un salto doble antes de los hashtags y uno
#     antes de la URL) pesan 3.
#
# Queda un presupuesto de 254 para repartir entre el texto y los hashtags.
# Medido sobre las 91 publicaciones reales que había al decidir esto: los
# resúmenes iban de 97 a 207 caracteres y el bloque de hashtags de 24 a 60, y
# **una se pasaba por 1 carácter**. Entraba el 98% por cómo escribe el modelo,
# no porque algo lo garantizara: el tope del schema (240) más 5 hashtags
# largos da ~336, que se pasa por 56. Ver specs/change_logs.md.
TWEET_LIMITE = 280
TWEET_PESO_URL = 23
TWEET_PESO_SEPARADORES = 3
TWEET_PRESUPUESTO = TWEET_LIMITE - TWEET_PESO_URL - TWEET_PESO_SEPARADORES

# Lo que se le pide al modelo. **No es un resumen recortado: es un gancho.**
# Un posteo no compite con la nota, invita a abrirla -- el desarrollo está a
# un click, en la URL que va en el mismo tweet. La referencia de largo es el
# ejemplo con el que se calibró esto ("La inesperada falla eléctrica durante
# el partido del equipo del Chiqui Tapia", 76 caracteres), así que 120 deja
# aire sin habilitar volver al párrafo. Muy por debajo del presupuesto: el
# recorte de `ajustar_a_tweet` pasa a ser una red que casi nunca se toca.
TWEET_OBJETIVO_RESUMEN = 120

# El contrato con el back-end promete entre 2 y 5 hashtags, así que el recorte
# no baja de 2 mientras se pueda recortar el texto en su lugar.
TWEET_MIN_HASHTAGS = 2

# U+2026, no tres puntos sueltos: ocupa menos y es lo tipográficamente
# correcto. Pesa 2 para X (cae fuera del rango 0-4351), y el recorte lo tiene
# en cuenta.
ELIPSIS = "…"


# Centinela para distinguir "no me pasaron modelo, resolvelo" de "me pasaron el
# modelo ya resuelto". Con `None` como default no se podían separar, y resolver
# de nuevo en cada cluster era una query por cluster. Ver `sintetizar_cluster`.
_RESOLVER = object()


class SintesisBloqueada(Exception):
    """
    El proveedor bloqueó la respuesta por sus filtros de contenido.

    No es un error técnico y reintentar no sirve: la misma entrada va a dar el
    mismo bloqueo. Se registra aparte porque, si pasa seguido, lo que está
    diciendo es que el producto no puede cubrir policiales — y eso es una
    decisión de producto, no un bug. Datos reales del proyecto ya incluyen
    material que puede activarlo (imputaciones por abuso sexual, muertes).
    """


class SintesisSinConfigurar(Exception):
    """
    Falta configuración para poder sintetizar. Reintentar no sirve.

    Cubre dos casos que se arreglan igual —tocando configuración, no
    esperando—: que no haya ningún modelo activo en `modelo_ia`, y que el que
    está activo no tenga credencial o pida un adaptador que no existe.
    """


class SintesisFallida(Exception):
    """
    El proveedor tuvo un problema técnico y `tenacity` ya agotó los reintentos.

    Es lo que queda de un `ErrorDeProveedor` (un 429, un JSON mal armado, un
    `base_url` que dejó de responder) después de los 3 intentos de
    `llamar_modelo` — reintentar de nuevo con la misma entrada tiene el mismo
    chance de andar que el intento siguiente del scheduler, no uno mejor.

    **Antes de que existiera esta clase, ese caso subía como un `ValueError` a
    secas.** `sintetizar_pendientes` lo atrapaba igual —un `except Exception`
    genérico no distingue—, pero `POST /clusters/{id}/synthesize` solo
    atajaba `SintesisSinConfigurar` y `SintesisBloqueada`, así que un rate
    limit del proveedor —la condición más esperable de ese endpoint— salía
    como un 500 sin manejar: exactamente lo que `main.py` documenta que un 500
    no puede significar ("se rompió algo nuestro", no "el proveedor tuvo un
    problema pasajero"). El mensaje ya viene saneado desde `llamar_modelo`, así
    que exponerlo en un 4xx no reabre ninguna fuga.
    """


# --- Lo que el modelo escapa y nosotros tenemos que desescapar --------------

# Los acentos del español en Latin-1, que es como el modelo los escapa cuando
# los escapa. **Es un mapa acotado y no `urllib.parse.unquote`, a propósito**:
# `unquote` transforma cualquier `%` seguido de dos dígitos hex, así que un
# texto con "50%ed" se convertiría en "50í". Improbable en prosa, pero es una
# puerta que no hace falta abrir. Esto dice lo que realmente hace -- arreglar
# una malformación conocida -- en vez de "decodificar URLs", que no es lo que
# está pasando.
_ESCAPES_LATIN1 = {
    "%e1": "á", "%e9": "é", "%ed": "í", "%f3": "ó", "%fa": "ú",
    "%f1": "ñ", "%fc": "ü",
    "%c1": "Á", "%c9": "É", "%cd": "Í", "%d3": "Ó", "%da": "Ú",
    "%d1": "Ñ", "%dc": "Ü",
    "%bf": "¿", "%a1": "¡", "%b0": "°", "%aa": "ª", "%ba": "º",
}

# **El `%` no puede venir después de un dígito, y eso lo decidió un test.**
# La primera versión reemplazaba el escape en cualquier posición, y con eso
# "Subió 50%ed más que el año pasado" se convertía en "Subió 50í más" -- que es
# exactamente el falso positivo que el mapa acotado venía a evitar. Lo agarró
# `test_no_toca_lo_que_no_es_un_escape`, no una hipótesis.
#
# La regla sale de mirar qué precede a cada cosa: un porcentaje **siempre** va
# después de un dígito ("50%", "3,5%"), y un acento escapado va después de una
# letra ("respald%f3") o abre la frase ("%bfQué"). Nunca después de un número.
_PORCENTAJE_ESCAPADO = re.compile(
    "(?<![0-9])(?:" + "|".join(re.escape(e) for e in _ESCAPES_LATIN1) + ")",
    re.IGNORECASE,
)


def normalizar_escapes(texto: str) -> str:
    """
    Devuelve el texto con los acentos que el modelo escapó, desescapados.

    **El problema es del modelo, no de la ingesta, y eso está medido.** Una
    síntesis del 12/09/2026 salió publicada con "Franco Colapinto logr3 el
    noveno puesto en la clasificaci&#243;n del Gran Premio de Espa&#241;a", y
    **las nueve noticias fuente de ese cluster estaban limpias**: ninguna tenía
    una sola entidad. Lo mismo con "respald%f3 la postura" del 09/09.

    Tres esquemas distintos aparecieron en tres semanas -- entidades HTML,
    percent-encoding, y acentos directamente ausentes -- y hasta **conviviendo
    en la misma oración**: `logr3` roto al lado de `qued&#243;` y `largar&#225;`
    intactos. Esa inconsistencia es lo que descarta que sea un saneador nuestro:
    un saneador es determinista.

    Los acentos que faltan del todo (el tercer caso) **no se arreglan acá**: esa
    información ya no está, no hay nada que desescapar. Eso es trabajo del
    prompt.
    """
    original = texto
    texto = _PORCENTAJE_ESCAPADO.sub(
        lambda m: _ESCAPES_LATIN1[m.group(0).lower()], texto
    )
    # `html.unescape` cubre las tres formas de entidad -- `&#243;`, `&#xf3;` y
    # `&oacute;` -- y es idempotente sobre texto ya limpio.
    texto = html.unescape(texto)

    if texto != original:
        # **Se avisa aunque se arregle.** Corregir en silencio es cómo un
        # proveedor empeora sin que nadie se entere; el log es lo que deja
        # medir si esto crece o desaparece.
        logger.warning(
            "El modelo devolvió texto escapado y se normalizó: %r -> %r",
            original[:80],
            texto[:80],
        )
    return texto


class _TextoNormalizado(BaseModel):
    """
    Base de los modelos de la respuesta: **desescapa cada string al parsear.**

    Va acá y no en `_persistir`, y no es una preferencia de estilo -- hay dos
    cosas río abajo que leen estos textos antes de que se persistan:

    1. **`ajustar_a_tweet` cuenta caracteres.** `&#243;` pesa 6 y `ó` pesa 1, así
       que normalizar después de esa cuenta rompe la garantía de los 280: un
       tuit que entra se recortaría igual.
    2. **`_comparativa_validada` matchea nombres de medios** con `_sin_acentos`.
       Un `"La Naci&#243;n"` no matchea con `"La Nación"`, y el enfoque de ese
       medio **se descarta en silencio**.

    O sea que normalizar en el borde arregla dos cosas que no estábamos
    buscando. Ese es el motivo de que sea acá.
    """

    @field_validator("*", mode="before")
    @classmethod
    def _desescapar(cls, valor):
        if isinstance(valor, str):
            return normalizar_escapes(valor)
        if isinstance(valor, list):
            return [
                normalizar_escapes(v) if isinstance(v, str) else v for v in valor
            ]
        return valor


# --- Esquema de la respuesta del modelo -------------------------------------
# Se le pasa al proveedor como esquema estructurado para que devuelva JSON
# válido por construcción, en vez de pedírselo en prosa y parsear a la
# esperanza. Cada adaptador lo envuelve como su protocolo lo pida.


class EnfoqueMedio(_TextoNormalizado):
    medio: str
    destaco: str
    omitio: str
    cita: str = PydanticField(description="Frase del cuerpo que respalda lo anterior")


class AnguloGenerado(_TextoNormalizado):
    id_existente: Optional[int] = PydanticField(
        default=None,
        description="Id del ángulo ya publicado que este actualiza; null si es nuevo",
    )
    titulo_angulo: str
    resumen_neutro: str
    puntos_clave: List[str]
    # Listas cerradas: con texto libre convivirían "Deportes", "deportes" y
    # "Fútbol", y la navegación del producto se rompe sola.
    #
    # Tope de 2 en `topicos`: sin límite la señal se diluye y el filtro por
    # categoría deja de servir para navegar. `subtopicos` no tiene tope --
    # su padre en `SUBTOPICO_PADRE` se agrega solo a `topicos` si falta, así
    # que agregar uno de más no cuesta lo mismo que agregar una categoría de
    # más.
    topicos: List[Topico] = PydanticField(min_length=1, max_length=2)
    subtopicos: List[Subtopico] = PydanticField(default_factory=list)

    # Copy para redes sociales (Twitter/Facebook), generado solo para el
    # subconjunto de ángulos de relevancia nacional -- no todo ángulo se
    # publica ahí. No hay forma de expresar en el `response_schema`
    # estructurado "obligatorio solo si `relevancia_social` es true": esa
    # condición vive en el texto del prompt, y `_persistir` la refuerza
    # después ignorando estos dos campos si el modelo los llenó igual pese a
    # marcar `relevancia_social=false`. Ver specs/change_logs.md, "Copy para
    # redes sociales".
    relevancia_social: bool
    # El 240 es una cota de tolerancia, NO el objetivo: al modelo se le piden
    # menos de 120 en el prompt (`TWEET_OBJETIVO_RESUMEN`, un gancho y no un
    # resumen) y `ajustar_a_tweet` recorta lo que se pase del tweet. A
    # propósito no se baja este `max_length` al objetivo: es una validación de
    # Pydantic, así que un gancho de 130 no se recortaría sino que tiraría
    # `ValidationError` y voltearía la síntesis entera del cluster. El copy de
    # redes es contenido descartable -- no puede ser el motivo por el que se
    # pierde una publicación.
    resumen_redes: Optional[str] = PydanticField(default=None, max_length=240)
    hashtags: List[str] = PydanticField(default_factory=list, max_length=6)

    comparativa_enfoques: List[EnfoqueMedio]
    notas: List[int] = PydanticField(description="Números de las notas que lo respaldan")


class RespuestaSintesis(BaseModel):
    angulos: List[AnguloGenerado]


# --- Ajuste del copy al tamaño de un tweet ----------------------------------


def peso_x(texto: str) -> int:
    """
    Cuántos caracteres "pesa" el texto para X.

    No es `len()`: X cuenta codepoints 0-4351 como 1 y el resto como 2. Para
    el español la diferencia no aparece (las tildes y la ñ entran en el primer
    rango, verificado sobre las 91 publicaciones reales: cero caracteres de
    peso 2), pero un emoji en el copy sí contaría doble y la cuenta tiene que
    reflejarlo.
    """
    return sum(1 if ord(caracter) <= 4351 else 2 for caracter in texto)


def _bloque_hashtags(hashtags: Sequence[str]) -> str:
    return " ".join(f"#{hashtag}" for hashtag in hashtags)


def peso_tweet(resumen: str, hashtags: Sequence[str]) -> int:
    """Peso del posteo completo: texto + hashtags + separadores + URL."""
    bloque = _bloque_hashtags(hashtags)
    separadores = (2 if bloque else 0) + 1
    return peso_x(resumen) + peso_x(bloque) + separadores + TWEET_PESO_URL


def _recortar(texto: str, tope: int) -> str:
    """
    Recorta a `tope` de peso cortando en un borde de palabra, con puntos
    suspensivos. Cortar a mitad de palabra se ve como un error del producto.

    Ojo con el peso de los puntos suspensivos: `…` es U+2026, que cae fuera
    del rango 0-4351, así que **pesa 2 y no 1**. Reservar un solo carácter
    dejaba el resultado un punto por encima del límite -- lo agarró
    `TestAjusteATweet`, no fue una hipótesis.
    """
    if peso_x(texto) <= tope:
        return texto

    reserva = peso_x(ELIPSIS)
    if tope <= reserva:
        return ""

    acumulado = 0
    corte = 0
    for posicion, caracter in enumerate(texto):
        peso = 1 if ord(caracter) <= 4351 else 2
        if acumulado + peso > tope - reserva:
            break
        acumulado += peso
        corte = posicion + 1

    recortado = texto[:corte]
    # Si el corte cayó dentro de una palabra, retroceder hasta el espacio
    # anterior en vez de dejarla partida.
    if corte < len(texto) and not texto[corte].isspace() and " " in recortado:
        recortado = recortado[: recortado.rfind(" ")]

    return recortado.rstrip(" ,;:.") + ELIPSIS


def ajustar_a_tweet(resumen: str, hashtags: Sequence[str]) -> Tuple[str, List[str]]:
    """
    Devuelve `(resumen, hashtags)` garantizando que el posteo entra en 280.

    Es la red de seguridad en código de lo que el prompt pide: el
    `response_schema` no puede expresar "la suma de estos dos campos más una
    URL no pasa de 280", así que pedirlo en el texto del prompt no garantiza
    nada. Mismo reparto que con `relevancia_social`: el prompt pide, el código
    asegura.

    El orden del recorte no es arbitrario. **Primero se sacan hashtags y
    recién después se toca el texto**: el resumen es la información y los
    hashtags son decoración, así que perder un hashtag cuesta menos que perder
    media oración. No se baja de `TWEET_MIN_HASHTAGS` porque el contrato con
    el back-end promete entre 2 y 5 (ver specs/webhook_contract.md, punto 9);
    si con 2 todavía no entra, se recorta el texto. El caso patológico de dos
    hashtags larguísimos que no dejan lugar ni al texto recortado se resuelve
    dejándolos afuera: es preferible un posteo sin hashtags que uno cortado.

    En la práctica casi nunca hace falta: con el objetivo de
    `TWEET_OBJETIVO_RESUMEN` en el prompt, sobre los datos reales solo una de
    91 publicaciones necesitaba ajuste, y por 1 carácter.
    """
    tags = [hashtag for hashtag in hashtags if hashtag]

    while len(tags) > TWEET_MIN_HASHTAGS and peso_tweet(resumen, tags) > TWEET_LIMITE:
        tags.pop()

    if peso_tweet(resumen, tags) <= TWEET_LIMITE:
        return resumen, tags

    disponible = TWEET_PRESUPUESTO - peso_x(_bloque_hashtags(tags))
    recortado = _recortar(resumen, disponible)
    if recortado and peso_tweet(recortado, tags) <= TWEET_LIMITE:
        return recortado, tags

    # Hashtags desproporcionados: se van todos antes que devolver un texto
    # mutilado para hacerles lugar.
    return _recortar(resumen, TWEET_PRESUPUESTO), []


def hay_material_nuevo(cluster: Cluster) -> bool:
    """
    Si llegaron noticias a este cluster desde el último intento de síntesis.

    **Es una sola pregunta con una sola respuesta posible**, y por eso vive
    suelta: la usan `clusters_pendientes` —para decidir qué barre el
    scheduler— y `POST /clusters/{id}/synthesize` —para no gastar una llamada
    al proveedor repitiendo una síntesis con la misma entrada—. Preguntarlo
    por dos caminos distintos sería tener dos respuestas el día que difieran.

    Sin marca (`None`) nunca se intentó, así que hay material por definición.

    **`MARCA_CADUCADO` tampoco cuenta como intento**: un cluster que venció sin
    sintetizarse y volvió a entrar en la ventana de fecha —porque se subió
    `HORAS_MAXIMAS_SIN_SINTETIZAR`, que es lo que recomienda la propia alerta—
    tiene que poder sintetizarse. Si contara, la recuperación que promete esa
    alerta sería mentira.
    """
    marca = cluster.noticias_al_sintetizar
    if marca is None or marca == MARCA_CADUCADO:
        return True
    return len(cluster.noticias) > marca


def clusters_pendientes(session: Session) -> List[Cluster]:
    """
    Clusters con material nuevo suficiente para publicar al menos un ángulo.

    Dos condiciones, y hacen falta las dos:

    1. **Llegaron noticias desde el último intento** (`noticias_al_sintetizar`).
       Es la guarda contra el reintento infinito: si ningún ángulo alcanzó el
       mínimo de medios no se creó ninguna fila de `Sintesis`, y sin la marca el
       cluster sería indistinguible de uno nunca intentado.
    2. **Las noticias todavía sin ángulo cubren `MIN_MEDIOS_CLUSTER` medios.**
       Este es el disparador real, y por eso no alcanza con contar medios del
       cluster: si TN y La Nación ya estaban y los dos publican después sobre
       los homenajes de la AFA, eso es un ángulo nuevo y publicable aunque no
       haya entrado ningún medio nuevo.

    Se incluyen los `procesado` recién cerrados porque un cluster puede alcanzar
    el mínimo de medios en los últimos minutos de su ventana y cerrarse antes de
    la corrida siguiente; sin esto perdería su publicación en silencio.

    El recorte por fecha evita revivir noticias viejas al arrancar el sistema.
    Lo que cae del otro lado no se pierde callado: `descartar_vencidos_sin_sintetizar`
    lo cuenta y avisa.
    """
    limite = ahora_utc() - timedelta(hours=settings.HORAS_MAXIMAS_SIN_SINTETIZAR)

    # `selectinload` trae las noticias de TODOS los candidatos en una sola
    # consulta adicional (WHERE cluster_id IN (...)), no una por cluster --
    # antes era N+1 acá abajo.
    candidatos = session.exec(
        select(Cluster)
        .options(selectinload(Cluster.noticias))
        .where(
            Cluster.estado.in_([ESTADO_ABIERTO, ESTADO_PROCESADO]),
            Cluster.fecha_creacion >= limite,
        )
    ).all()

    if not candidatos:
        return []

    # Acotado a las noticias realmente en juego, no la tabla `SintesisNoticia`
    # entera -- esa crecía sin techo con el historial del producto, no con el
    # tamaño de esta corrida.
    ids_noticias = [n.id for cluster in candidatos for n in cluster.noticias]
    ya_con_angulo = set(
        session.exec(
            select(SintesisNoticia.noticia_id).where(
                SintesisNoticia.noticia_id.in_(ids_noticias)
            )
        ).all()
    )

    pendientes: List[Cluster] = []
    for cluster in candidatos:
        noticias = cluster.noticias

        # Ver `hay_material_nuevo`, que es la misma pregunta que se hace el
        # endpoint por cluster antes de gastar una llamada al proveedor.
        if not hay_material_nuevo(cluster):
            continue

        sin_angulo = [n for n in noticias if n.id not in ya_con_angulo]
        if len({n.medio_id for n in sin_angulo}) >= settings.MIN_MEDIOS_CLUSTER:
            pendientes.append(cluster)

    return pendientes


def descartar_vencidos_sin_sintetizar(session: Session) -> int:
    """
    Marca y denuncia los clusters que caducaron sin haberse intentado nunca.

    Que un cluster viejo deje de ser candidato está bien —una noticia de hace
    tres días no es noticia— pero hasta acá eso pasaba **en silencio**. Y ese
    silencio contradice la contingencia sobre la que está armado el pipeline:
    "todo paso es idempotente, la corrida siguiente retoma sola". Para la
    síntesis eso era falso pasado el plazo, y nada lo decía.

    Medido sobre datos reales: 30 clusters publicables con 85 notas adentro
    murieron así, todos con la marca en `None` — o sea, sin que el paso los
    mirara una sola vez.

    Solo se cuentan los que **podrían haber publicado** (alcanzaron el mínimo de
    medios). Un cluster que caduca con un solo medio no perdió nada: no tenía
    con qué comparar.

    Se los marca con `MARCA_CADUCADO` para que el aviso no se repita en cada
    corrida —una alerta que se repite sin novedad es una alerta que se deja de
    leer— pero **sin cerrarles la puerta**: esa marca no cuenta como intento, así
    que subir el plazo los devuelve a la carrera. Con el conteo real de noticias
    quedaban descartados para siempre y la recomendación de la alerta era
    mentira.

    El aviso ignora el cooldown a propósito: el descarte es terminal y no se va
    a volver a informar, así que si el cooldown se lo traga esa información se
    pierde. El emisor garantiza no repetir, que es la condición para usarlo.
    """
    limite = ahora_utc() - timedelta(hours=settings.HORAS_MAXIMAS_SIN_SINTETIZAR)

    # `selectinload` precarga `c.noticias` de todos los vencidos en una sola
    # consulta -- sin esto, el acceso lazy en el comprehension de abajo
    # disparaba una query por cluster.
    vencidos = session.exec(
        select(Cluster)
        .options(selectinload(Cluster.noticias))
        .where(
            Cluster.estado.in_([ESTADO_ABIERTO, ESTADO_PROCESADO]),
            Cluster.fecha_creacion < limite,
            Cluster.noticias_al_sintetizar.is_(None),
        )
    ).all()

    perdidos = [
        c for c in vencidos
        if len({n.medio_id for n in c.noticias}) >= settings.MIN_MEDIOS_CLUSTER
    ]

    # Capturado ANTES del commit: `session.commit()` expira los atributos de
    # los objetos por defecto, y volver a leer `c.id`/`c.noticias` después
    # dispararía una query de recarga por cluster -- el mismo N+1 que
    # `selectinload` acababa de evitar, solo que corriendo después en vez de
    # antes.
    ids_perdidos = [c.id for c in perdidos]
    notas_perdidas = sum(len(c.noticias) for c in perdidos)

    for cluster in vencidos:
        cluster.noticias_al_sintetizar = MARCA_CADUCADO
        session.add(cluster)
    session.commit()

    if perdidos:
        logger.error(
            f"{len(perdidos)} clusters publicables caducaron sin sintetizarse "
            f"({notas_perdidas} noticias): {ids_perdidos}"
        )
        enviar_alerta(
            asunto=f"[Sin Ruido] {len(perdidos)} clusters publicables caducaron sin publicar",
            cuerpo=(
                f"Alcanzaron {settings.MIN_MEDIOS_CLUSTER} medios pero pasaron "
                f"{settings.HORAS_MAXIMAS_SIN_SINTETIZAR} h sin que la síntesis los "
                f"mirara, así que ya no son candidatos.\n\n"
                f"Clusters: {ids_perdidos}\n"
                f"Noticias involucradas: {notas_perdidas}\n\n"
                "Si esto aparece sin que haya habido una caída, el plazo de "
                "HORAS_MAXIMAS_SIN_SINTETIZAR quedó corto: subirlo los vuelve a "
                "poner en carrera en la corrida siguiente."
            ),
            clave="sintesis:vencidos",
            # Terminal y sin repetición: si el cooldown se lo traga, esta
            # información no aparece nunca más.
            ignorar_cooldown=True,
        )

    return len(perdidos)


def construir_prompt(
    evidencia: dict,
    noticias: Sequence[Noticia],
    medios: Dict[int, str],
    angulos_existentes: Sequence[Sintesis],
) -> str:
    """
    Arma el prompt: evidencia medida + cuerpos completos + ángulos ya publicados.

    Se manda el **cuerpo completo** y no un extracto porque es lo que le permite
    al modelo verificar cada pista: la evidencia sin el texto es una afirmación
    que hay que creer, con el texto es una hipótesis contrastable. Sale barato
    porque `preprocessing` ya acotó a las notas más representativas por medio.
    """
    nucleo = evidencia["nucleo_comun"]

    bloques = []
    for numero, noticia in enumerate(noticias, start=1):
        medio = medios[noticia.medio_id]
        datos = evidencia["por_medio"].get(medio, {})
        seccion = topico_declarado(noticia.url)
        subseccion = subtopico_declarado(noticia.url)
        bloques.append(
            f"--- NOTA {numero} | {medio}\n"
            f"TITULAR: {noticia.titulo}\n"
            f"Sección en la que la publicó el medio: "
            f"{seccion.value if seccion else '(no la declara)'}"
            f"{f' / {subseccion.value}' if subseccion else ''}\n"
            f"Vocabulario propio del medio: "
            f"{', '.join(datos.get('terminos_propios', [])) or '(sin rasgo)'}\n"
            f"Menciona en exclusiva: "
            f"{', '.join(datos.get('entidades_exclusivas', [])) or '(nada)'}\n"
            f"No menciona, y otros sí: "
            f"{', '.join(datos.get('entidades_omitidas', [])) or '(nada)'}\n"
            f"CUERPO:\n{noticia.contenido_limpio}\n"
        )

    if angulos_existentes:
        publicados = "\n".join(
            f"  id={s.id}: {s.titulo_angulo}" for s in angulos_existentes
        )
        instruccion_angulos = (
            f"Este hecho YA TIENE ángulos publicados:\n{publicados}\n\n"
            "Devolvé cada uno de ellos con su `id_existente`, actualizando su "
            "contenido con el material nuevo. NO los renombres, NO los partas y "
            "NO los combines: del otro lado ya tienen lectores encima. Si el "
            "material nuevo no entra en ninguno, agregá un ángulo con "
            "`id_existente` en null."
        )
    else:
        instruccion_angulos = (
            "Separá la cobertura en los ÁNGULOS distintos que encuentres (el "
            "hecho central, sus consecuencias, las reacciones). Dejá "
            "`id_existente` en null en todos: es la primera síntesis."
        )

    return f"""Sos un editor que redacta síntesis neutras comparando cómo distintos medios
cubrieron un mismo hecho.

Actores que mencionan TODOS los medios: {', '.join(nucleo['entidades']) or '(ninguno en común)'}
Vocabulario que repiten todos: {', '.join(nucleo['terminos']) or '(sin núcleo)'}

Abajo va la cobertura de {len(evidencia['medios'])} medios en {len(noticias)} notas. Cada una trae
señales medidas automáticamente sobre su texto. Son PISTAS A VERIFICAR, no
conclusiones: parte de lo detectado son artefactos (un posteo incrustado, una
errata en un nombre) y no decisiones editoriales. Contrastá cada pista contra el
cuerpo y descartá las que no sean significativas.

{chr(10).join(bloques)}

{instruccion_angulos}

Para cada ángulo:
- `resumen_neutro`: sin adjetivos valorativos, solo hechos que sostenga más de
  un medio.
- `topicos`: de qué tema(s) es, de la lista cerrada. Uno o dos -- la mayoría de
  las coberturas tiene uno solo. Dos solo si pertenece con el mismo derecho a
  dos categorías (la muerte de un futbolista es deportes Y también
  espectáculos, no una principal y otra secundaria). La sección que declara
  cada medio es una pista y no la respuesta: los medios discrepan seguido y esa
  discrepancia es editorial, no un error a promediar.
- `subtopicos`: recortes más finos DENTRO de los tópicos elegidos, si alguno
  aplica (puede quedar vacío). Ejemplo: si `topicos` incluye `deportes` y la
  cobertura es específicamente de fútbol, agregá `futbol`. Elegí subtópicos
  solo de categorías que ya pusiste en `topicos` -- si el subtópico que más
  encaja pertenece a una categoría que no elegiste, agregá esa categoría a
  `topicos` en vez de forzar un subtópico huérfano.
- `relevancia_social`: `true` solo si el hecho nombra una persona con
  reconocimiento público (figura política, del deporte, del espectáculo,
  empresarial) o una institución pública o privada de renombre nacional. Ante
  la duda, `false` -- no es un tema más amplio, es un filtro más angosto.
- `resumen_redes` y `hashtags`: completalos SOLO si `relevancia_social` es
  `true`. Si no, dejá `resumen_redes` en `null` y `hashtags` en una lista
  vacía.
  `resumen_redes` **no es un resumen: es un gancho corto para un posteo**,
  de menos de 120 caracteres. No cuenta el hecho entero ni repite
  `resumen_neutro` -- el posteo lleva el link a la nota completa, así que tu
  trabajo es que den ganas de abrirla, no reemplazarla. Una sola idea, la más
  distintiva del hecho.
  El gancho se logra **nombrando lo concreto y reconocible** (la persona, el
  club, el lugar, la cifra), NO con adjetivos que valoren ni con signos de
  exclamación, misterio o clickbait: nada de "increíble", "escándalo",
  "mirá lo que pasó" ni preguntas retóricas. Sigue siendo neutro; lo
  atractivo tiene que salir del hecho, no del énfasis.
  Ejemplo del tono buscado, para un apagón durante un partido de Barracas
  Central: `La inesperada falla eléctrica durante el partido del equipo del
  Chiqui Tapia`. Fijate que no adjetiva el hecho ni exagera, pero elige el
  detalle que engancha y nombra a alguien reconocible.
  `hashtags` son entre 2 y 5, en minúscula y sin el símbolo `#` (lo agrega
  quien publique), basados en los temas y actores del hecho -- no asumas que
  están en tendencia hoy, esa decisión es de quien los publique. Preferí
  hashtags cortos: entre todos no deberían pasar de 60 caracteres.
- `comparativa_enfoques`: **una entrada por cada medio que aportó notas a ese
  ángulo**, sin saltearte ninguno, con qué destacó, qué omitió y una `cita`
  textual del cuerpo que lo respalde. Omití las diferencias que no sean
  editorialmente significativas, pero no omitas al medio.
- `notas`: los números de las notas que respaldan ese ángulo.
"""


@retry(
    retry=retry_if_not_exception_type((SintesisBloqueada, SintesisSinConfigurar)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def llamar_modelo(prompt: str, modelo: ModeloIA) -> RespuestaSintesis:
    """
    Le pide la síntesis al proveedor configurado.

    **`modelo` es obligatorio desde la etapa 4 del punto 2.** Antes, `None`
    significaba "usá el camino histórico de Gemini", una rama que hablaba con el
    proveedor directo leyendo `settings.GEMINI_*`. Esa rama se borró: el motor ya
    no tiene un proveedor preferido escondido en el código, que era justamente lo
    que este punto del backlog venía a sacar.

    Quien resuelve qué modelo es, y avisa si no hay ninguno, es
    `sintetizar_pendientes`. Acá llega decidido.

    La única traducción que hace falta en la frontera es la excepción: los
    adaptadores hablan `RespuestaBloqueada` para no depender de este módulo, y de
    este lado se convierte a `SintesisBloqueada`, que es lo que ya entienden el
    `retry` de acá arriba y `sintetizar_pendientes`.
    """
    try:
        return construir(modelo).generar(prompt, RespuestaSintesis)
    except RespuestaBloqueada as error:
        raise SintesisBloqueada(str(error)) from error
    except ProveedorNoConfigurado as error:
        # **No se reintenta**, por lo mismo que el adaptador de abajo: una
        # credencial que falta no se arregla sola en el intento siguiente. Sin
        # esta rama eran tres intentos con espera creciente por cluster — con 37
        # clusters, entre 2 y 4 minutos de sleeps puros por corrida, más un
        # traceback por cada uno, y todo contado como "fallido" en vez de "mal
        # configurado".
        #
        # **El mensaje original se queda en el log y NO viaja.** Nombra la
        # variable de entorno (`MODELO_API_KEY_GROQ`), y ese dato es la mitad de
        # una cadena de exfiltración: quien sabe qué variable nombrar puede dar
        # de alta un modelo con `base_url` propio y `api_key_env` apuntando ahí,
        # y el motor le entrega la credencial en el sondeo. Es exactamente lo
        # que `_vista_publica` filtra de `GET /modelos` desde la tanda 2, y esta
        # excepción lo estaba sacando por la puerta de al lado: termina en el
        # `detalle` de un 422 y en el campo `agotados` de un 200.
        #
        # Mismo criterio que `modelos.sondear` con el cuerpo del proveedor: al
        # log lo que sirve para diagnosticar, a la respuesta lo que se puede
        # decir sin abrir una puerta.
        logger.error(f"{modelo.nombre} no puede autenticarse: {error}")
        raise SintesisSinConfigurar(
            f"{modelo.nombre} no tiene su credencial configurada. Cuál es la "
            f"variable que falta queda en el log del motor, no en esta "
            f"respuesta."
        ) from error
    except AdaptadorNoImplementado as error:
        # Éste sí viaja entero, y no es una excepción a la regla de arriba: su
        # mensaje dice qué adaptador falta, cuáles hay y qué usar en su lugar.
        # No nombra variables del entorno ni la configuración del operador.
        raise SintesisSinConfigurar(f"{modelo.nombre}: {error}") from error
    except ErrorDeProveedor as error:
        # `SintesisFallida` no está en `retry_if_not_exception_type` de arriba,
        # así que esto SIGUE reintentándose igual que antes —el cambio no toca
        # el comportamiento de `tenacity`, solo lo que queda cuando los 3
        # intentos se agotan—: un 429 o un JSON mal armado se arreglan solos
        # en el intento siguiente, y recién si los tres fallan esto sube.
        raise SintesisFallida(f"{modelo.nombre}: {error}") from error


def _sin_acentos(texto: str) -> str:
    return (
        unicodedata.normalize("NFKD", texto)
        .encode("ascii", "ignore")
        .decode("ascii")
        .strip()
        .lower()
    )


def _comparativa_validada(
    enfoques: Sequence[EnfoqueMedio], nombres_del_cluster: Sequence[str]
) -> Dict[str, dict]:
    """
    Deja solo los enfoques de medios que están de verdad en el cluster, y con
    el nombre tal cual figura en la base.

    Las dos cosas hacen falta. El modelo escribe los nombres como le salen —en
    la primera corrida real devolvió "La Nacion" sin tilde, que no matchea con
    "La Nación" y habría dejado la comparativa sin forma de vincularla al medio.
    Y puede citar un medio que no participó del hecho, que es alucinación pura.
    """
    canonicos = {_sin_acentos(nombre): nombre for nombre in nombres_del_cluster}
    validada: Dict[str, dict] = {}

    for enfoque in enfoques:
        nombre = canonicos.get(_sin_acentos(enfoque.medio))
        if nombre is None:
            logger.warning(f"Se descarta el enfoque de un medio ajeno: {enfoque.medio}")
            continue
        validada[nombre] = {
            "destaco": enfoque.destaco,
            "omitio": enfoque.omitio,
            "cita": enfoque.cita,
        }

    return validada


def _persistir(
    session: Session,
    cluster: Cluster,
    respuesta: RespuestaSintesis,
    enviadas: Sequence[Noticia],
    medios: Dict[int, str],
    modelo_usado: Optional[str] = None,
) -> dict:
    """
    Guarda los ángulos válidos. Los inválidos se descartan sin tocar el cluster.

    Un ángulo nuevo se publica solo si cubre `MIN_MEDIOS_CLUSTER` medios, y eso
    se exige **dos veces**: en las noticias que lo respaldan y en la comparativa
    escrita. Con una sola de las dos no alcanza. Medido en una corrida real: dos
    ángulos tenían notas de La Nación y El Cronista —así que pasaban el filtro
    de noticias— pero el modelo escribió una sola entrada de comparativa en cada
    uno. Se publicaban como comparativa mostrando una sola voz, que es
    precisamente lo que el producto promete no hacer.

    El filtro va acá, sobre el ángulo, y no sobre el cluster: un cluster de 5
    medios puede contener un ángulo que cubrió uno solo.

    A los ángulos que ya existen no se les aplica ese filtro ni se les quitan
    noticias: ya se publicaron, y del otro lado tienen lectores encima.
    """
    por_numero = {numero: n for numero, n in enumerate(enviadas, start=1)}
    existentes = {s.id: s for s in cluster.sintesis}
    stats = {"creados": 0, "actualizados": 0, "descartados": 0}

    for angulo in respuesta.angulos:
        notas = [por_numero[n] for n in angulo.notas if n in por_numero]

        # Un `id_existente` que no corresponde a este cluster es alucinación:
        # se trata como ángulo nuevo.
        sintesis = existentes.get(angulo.id_existente) if angulo.id_existente else None

        # La comparativa se valida contra los medios que aportaron notas a
        # ESTE ángulo, no contra los del cluster entero. Con el alcance amplio,
        # un ángulo con notas de TN y La Nación podía publicarse describiendo a
        # TN y El Cronista: pasaba el filtro de dos entradas, pero El Cronista
        # no aparecía en sus `fuentes` y del otro lado quedaba un enfoque sin
        # una sola nota que lo respalde.
        #
        # En una actualización el alcance incluye también los medios que el
        # ángulo ya tenía: sus noticias siguen ahí, así que sus enfoques son
        # legítimos aunque el modelo no haya vuelto a mandar notas de ellos.
        ids_del_angulo = {n.medio_id for n in notas}
        if sintesis is not None:
            ids_del_angulo |= {n.medio_id for n in sintesis.noticias}

        comparativa = _comparativa_validada(
            angulo.comparativa_enfoques,
            sorted({medios[mid] for mid in ids_del_angulo if mid in medios}),
        )

        if sintesis is None:
            medios_con_notas = len({n.medio_id for n in notas})
            if (
                medios_con_notas < settings.MIN_MEDIOS_CLUSTER
                or len(comparativa) < settings.MIN_MEDIOS_CLUSTER
            ):
                stats["descartados"] += 1
                logger.info(
                    f"Ángulo descartado ({medios_con_notas} medios con notas, "
                    f"{len(comparativa)} en la comparativa): {angulo.titulo_angulo}"
                )
                continue
            sintesis = Sintesis(
                cluster_id=cluster.id,
                titulo_angulo=angulo.titulo_angulo,
                modelo_usado=modelo_usado,
            )
            sintesis.noticias = notas
            topicos_completos = con_padres_completos(angulo.topicos, angulo.subtopicos)
            sintesis.topicos = [t.value for t in topicos_completos]
            sintesis.subtopicos = [s.value for s in angulo.subtopicos]
            stats["creados"] += 1
        else:
            # Ni el título ni el tópico se tocan: son lo que el backend ya
            # publicó. Mover una publicación de Deportes a Espectáculos entre
            # una entrega y la siguiente es el mismo problema que renombrarla —
            # del otro lado ya está en una sección, con lectores encima.
            #
            # La excepción son las síntesis anteriores a que el campo existiera:
            # ahí no hay nada que preservar, solo un hueco que llenar.
            if not sintesis.topicos:
                topicos_completos = con_padres_completos(angulo.topicos, angulo.subtopicos)
                sintesis.topicos = [t.value for t in topicos_completos]
                sintesis.subtopicos = [s.value for s in angulo.subtopicos]

            # La atribución **sí** se actualiza, a diferencia del título y el
            # tópico. Es la diferencia entre los dos tipos de campo que hay acá:
            # aquéllos son identidad que el backend ya publicó, y éste describe
            # quién produjo el contenido que se está reescribiendo justo abajo.
            # Si se cambió de modelo entre una corrida y otra, dejar la etiqueta
            # vieja haría que la serie histórica afirme que un texto lo produjo
            # un modelo que no lo produjo.
            sintesis.modelo_usado = modelo_usado

            faltantes = [n for n in notas if n not in sintesis.noticias]
            sintesis.noticias = list(sintesis.noticias) + faltantes

            # La comparativa se FUSIONA, no se pisa: la entrada nueva de un
            # medio reemplaza a la vieja, pero un medio que ya estaba no
            # desaparece porque el modelo no lo haya vuelto a mencionar.
            #
            # Sin esto una re-síntesis puede degradar un ángulo publicado de dos
            # voces a una, que es peor que no haberlo publicado. Y además
            # incumple lo que specs/webhook_contract.md ya le promete al
            # back-end: la comparativa suma medios, no los quita.
            comparativa = {**sintesis.comparativa_enfoques, **comparativa}

            # Hay contenido nuevo que entregar. El contador de intentos vuelve a
            # cero porque el cuerpo cambió: si el backend venía rechazando esta
            # síntesis, este payload distinto merece su propia oportunidad.
            sintesis.enviado_backend = False
            sintesis.intentos_envio = 0
            stats["actualizados"] += 1

        sintesis.resumen_neutro = angulo.resumen_neutro
        sintesis.puntos_clave = angulo.puntos_clave
        sintesis.comparativa_enfoques = comparativa
        sintesis.fecha_generacion = ahora_utc()

        # No se congela como el título/tópicos: es contenido de marketing,
        # descartable, así que una resíntesis lo puede reemplazar sin romper
        # nada del lado del back-end. Si `relevancia_social` da `false` -acá
        # o en una resíntesis posterior- se deja lo que ya hubiera: no se
        # retracta un copy que puede estar publicado en redes, mismo criterio
        # que "el motor nunca retracta una publicación entregada" (ver
        # specs/webhook_contract.md, punto 9).
        resumen_redes = (angulo.resumen_redes or "").strip()
        if angulo.relevancia_social and resumen_redes:
            # Se guarda ya ajustado a los 280 de un tweet, no crudo: si el
            # recorte quedara del lado del back-end, ellos tendrían que cortar
            # sin saber qué parte del texto es prescindible -- y cortarían a
            # mitad de palabra. Acá sabemos que los hashtags son lo primero
            # que sobra. Ver `ajustar_a_tweet`.
            resumen_redes, hashtags_redes = ajustar_a_tweet(
                resumen_redes, angulo.hashtags
            )
            if resumen_redes != angulo.resumen_redes.strip():
                logger.info(
                    f"Copy de redes recortado para entrar en un tweet: "
                    f"{angulo.titulo_angulo}"
                )

            if sintesis.publicacion_redes is None:
                sintesis.publicacion_redes = PublicacionRedes(
                    resumen_redes=resumen_redes, hashtags=hashtags_redes
                )
            else:
                sintesis.publicacion_redes.resumen_redes = resumen_redes
                sintesis.publicacion_redes.hashtags = hashtags_redes
                sintesis.publicacion_redes.fecha_generacion = ahora_utc()
        elif angulo.relevancia_social:
            logger.warning(
                f"relevancia_social=true sin resumen_redes, se ignora: {angulo.titulo_angulo}"
            )

        session.add(sintesis)

    return stats


def _etiqueta_del_modelo(modelo: ModeloIA) -> str:
    """
    Con qué nombre queda registrada una síntesis en `Sintesis.modelo_usado`.

    Se guarda **el nombre que le puso el operador y no el id del modelo**. Es la
    diferencia entre poder comparar y no: `ModeloIA` permite dos filas del mismo
    modelo con distinta temperatura o distinta cuenta, y guardando `gpt-4o` en
    las dos quedan indistinguibles en la serie histórica — justo la comparación
    que esta columna existe para habilitar.

    Desde la etapa 4 **siempre hay fila**, así que toda síntesis nueva queda
    atribuida. `None` sigue significando lo que significaba: las síntesis
    anteriores a que existiera la columna, cuya procedencia no se infiere ni se
    rellena hacia atrás.
    """
    return modelo.nombre


def sintetizar_cluster(session: Session, cluster: Cluster, modelo=_RESOLVER) -> dict:
    """
    Genera (o actualiza) los ángulos de un cluster y deja la marca puesta.

    `modelo` acepta un `ModeloIA` ya resuelto o el centinela, que significa
    "resolvelo vos". El centinela existe para que `sintetizar_pendientes` pueda
    resolverlo **una sola vez** y pasarlo: sin él, cada cluster volvería a
    consultarlo, que es el patrón de N+1 que este repo viene sacando.

    Sin ningún modelo activo levanta `SintesisSinConfigurar`. Antes ese caso
    significaba "usá Gemini" y no era un error; desde la etapa 4 del punto 2 el
    motor no tiene proveedor preferido, así que no elegir ninguno es un estado
    de configuración incompleta y se dice.
    """
    if modelo is _RESOLVER:
        modelo = modelo_activo(session)
    if modelo is None:
        raise SintesisSinConfigurar(
            "No hay ningún modelo activo en `modelo_ia`. Dale de alta uno con "
            "POST /modelos o prendé alguno con PATCH /modelos/{id}?activo=true."
        )

    evidencia = construir_evidencia(session, cluster)
    enviadas = evidencia["noticias"]
    if not enviadas:
        return {"creados": 0, "actualizados": 0, "descartados": 0}

    # `construir_evidencia` ya consultó los medios y las noticias del cluster
    # -- reusar eso en vez de volver a pedirlo evita duplicar dos queries por
    # cada cluster que se sintetiza.
    medios = evidencia["medios_por_id"]
    prompt = construir_prompt(evidencia, enviadas, medios, cluster.sintesis)

    respuesta = llamar_modelo(prompt, modelo)
    stats = _persistir(
        session, cluster, respuesta, enviadas, medios,
        modelo_usado=_etiqueta_del_modelo(modelo),
    )

    # La marca se pone aunque no se haya publicado nada: es lo que evita que un
    # cluster sin ángulos válidos se reintente en cada corrida para siempre.
    cluster.noticias_al_sintetizar = evidencia["total_noticias"]
    session.add(cluster)
    session.commit()

    return stats


# Fallos seguidos del mismo modelo que lo sacan de la cadena por el resto de la
# corrida.
#
# **El cortocircuito no es opcional, y el número sale de una cuenta.** Con la
# cuota del titular agotada y sin esto, CADA cluster paga los 3 reintentos de
# `tenacity` con espera creciente hasta 30 s antes de caer al suplente: con 26
# clusters son minutos de sleeps puros por corrida. Es el mismo modo de falla
# que ya se corrigió una vez, cuando `SintesisSinConfigurar` dejó de
# reintentarse.
#
# **Dos y no uno**: un fallo suelto puede ser un JSON mal armado de ese cluster
# puntual, y sacar al titular por eso cambiaría de proveedor —y con él lo que
# queda escrito en `modelo_usado`— por una casualidad. Dos seguidos del mismo
# modelo ya son un patrón. El costo del segundo intento está acotado: ~60 s en
# el peor caso, sobre un ciclo de 15 minutos.
#
# `SintesisSinConfigurar` **no** cuenta acá: agota al modelo de una, porque una
# credencial que falta o un adaptador que no existe no se arreglan entre un
# cluster y el siguiente.
FALLOS_SEGUIDOS_PARA_AGOTAR = 2

# Por qué un modelo se cayó de la cadena, en el campo `agotados` de las stats.
#
# **Son categorías y no el mensaje del error, y eso es la defensa.** `agotados`
# viaja en el cuerpo de un 200 de `POST /synthesize`, así que todo lo que entre
# acá es público. La primera versión metía el mensaje entero, y como el de
# `ProveedorNoConfigurado` nombra la variable de entorno, ese 200 publicaba el
# dato que `_vista_publica` filtra de `GET /modelos` desde la tanda 2 — sin
# necesidad siquiera de provocar un error.
#
# Un valor cerrado no puede volver a filtrar por descuido, y de paso es lo que
# una interfaz necesita para mostrar el motivo sin parsear prosa. El detalle
# completo queda en el log.
AGOTADO_SIN_CONFIGURAR = "sin_configurar"
AGOTADO_FALLOS_SEGUIDOS = "fallos_seguidos"


def _intentar_con_la_cadena(
    session: Session,
    cluster: Cluster,
    disponibles: List[ModeloIA],
    agotados: Dict[str, str],
    fallos_seguidos: Dict[str, int],
) -> Tuple[Optional[dict], Optional[ModeloIA], Optional[str]]:
    """
    Intenta un cluster con cada modelo de la cadena hasta que alguno lo saque.

    Devuelve `(resultado, modelo, motivo)`. Con `motivo` en `None` salió bien;
    `"bloqueado"` es que el proveedor rechazó el contenido y `"sin_salida"` es
    que ninguno de la cadena pudo.

    **Qué cae al siguiente y qué no**, que es la decisión de fondo de esta
    función:

    - `SintesisBloqueada` **no cae**. El proveedor rechazó el contenido por sus
      filtros; buscar otro que sí lo acepte es rodear una negativa de seguridad.
      Y además destruiría la señal: que esto pase seguido informa que el
      producto no puede cubrir cierto material, y ésa es una decisión de
      producto, no un fallo técnico a sortear.
    - `SintesisSinConfigurar` **cae, y agota al modelo de una**. Hasta
      multimodelo cortaba la corrida entera; con cadena significa "usá el
      siguiente".
    - Cualquier otro fallo **cae**, y suma al contador del cortocircuito.

    `agotados` y `fallos_seguidos` los mantiene el llamador entre clusters: son
    el estado de la corrida, no de este cluster.
    """
    for modelo in disponibles:
        try:
            resultado = sintetizar_cluster(session, cluster, modelo)
        except SintesisBloqueada as error:
            session.rollback()
            logger.warning(
                f"Cluster {cluster.id} bloqueado por {modelo.nombre}: {error}. "
                f"No se prueba con otro proveedor: es una negativa de contenido, "
                f"no una falla tecnica."
            )
            return None, None, "bloqueado"
        except SintesisSinConfigurar as error:
            session.rollback()
            agotados[modelo.nombre] = AGOTADO_SIN_CONFIGURAR
            logger.error(
                f"{modelo.nombre} sale de la cadena por lo que queda de la "
                f"corrida: {error}"
            )
            continue
        except Exception as error:
            session.rollback()
            seguidos = fallos_seguidos.get(modelo.nombre, 0) + 1
            fallos_seguidos[modelo.nombre] = seguidos
            logger.exception(
                f"Fallo la sintesis del cluster {cluster.id} con "
                f"{modelo.nombre} ({seguidos} seguido/s): {error}"
            )
            if seguidos >= FALLOS_SEGUIDOS_PARA_AGOTAR:
                agotados[modelo.nombre] = AGOTADO_FALLOS_SEGUIDOS
                logger.error(
                    f"{modelo.nombre} sale de la cadena por lo que queda de la "
                    f"corrida: {seguidos} fallos seguidos."
                )
            continue

        # Salio bien: se corta la racha. Sin esto, dos fallos separados por
        # veinte clusters exitosos agotarian al modelo como si fueran seguidos.
        fallos_seguidos[modelo.nombre] = 0
        return resultado, modelo, None

    return None, None, "sin_salida"


def sintetizar_pendientes(
    session: Session, modelo: Optional[ModeloIA] = None
) -> dict:
    """
    Sintetiza todos los clusters con material nuevo suficiente.

    Un cluster que falla no arrastra a los demás: se registra y se sigue. La
    corrida siguiente lo reintenta sola, porque la marca solo se escribe cuando
    la síntesis llegó a persistirse.

    **Sin `modelo` arma la cadena de fallback** (`cadena_de_modelos`): el activo
    al frente y detrás los suplentes con credencial propia. Es lo que corre el
    scheduler, que no recibe ningún parámetro.

    **Con `modelo` usa ése y solo ése, sin cadena.** Si alguien eligió un modelo
    para esta corrida, caer en silencio a otro contradice la elección — y
    dejaría en `modelo_usado` una serie histórica que dice que se usó uno que
    nadie pidió, que es justamente la comparación que esa columna existe para
    habilitar. Falla y lo dice.
    """
    # **El modelo se resuelve ANTES de barrer los caducados**, y el orden
    # importa. El barrido marca como vencido lo que pasó
    # `HORAS_MAXIMAS_SIN_SINTETIZAR` sin sintetizarse y avisa recomendando subir
    # ese plazo. Con el motor sin modelo configurado —una instalación recién
    # migrada, o alguien que apagó el último—, esa recomendación es falsa: no se
    # sintetizó porque no había con qué, y subir el plazo no cambia nada.
    #
    # Se corta antes de barrer, así que nada caduca por una causa que el motor
    # ya sabe cuál es. Los clusters quedan intactos y entran en carrera solos en
    # cuanto haya un modelo prendido.
    cadena = [modelo] if modelo is not None else cadena_de_modelos(session)

    stats = {
        "vencidos_sin_publicar": 0,
        "pendientes": 0,
        "sintetizados": 0,
        "creados": 0,
        "actualizados": 0,
        "descartados": 0,
        "bloqueados": 0,
        "fallidos": 0,
        # Las dos formas de "el motor no está configurado para sintetizar", que
        # se distinguen porque se arreglan distinto: `sin_modelo` es que nadie
        # eligió proveedor, `sin_credencial` es que el elegido no tiene con qué
        # autenticarse. Ninguna de las dos cuenta como cluster fallido.
        "sin_modelo": False,
        "sin_credencial": False,
        # Con quién se sintetizó cada cosa, y quién se cayó de la cadena. Sin
        # esto una corrida que terminó en el suplente se ve idéntica a una
        # normal, y el operador se entera de que su titular está agotado recién
        # cuando mira `modelo_usado` fila por fila.
        "por_modelo": {},
        "agotados": {},
    }

    # **Se corta acá y no cluster por cluster.** Sin modelo no hay síntesis
    # posible, y dejar que cada cluster lo descubra por su cuenta daba 26
    # excepciones con 26 tracebacks para una sola causa, todas contadas como
    # "fallidas" — que sugiere un problema con los clusters cuando el problema
    # es que falta configurar el motor.
    #
    # Antes esta rama no existía porque `None` significaba "usá Gemini". Ahora
    # significa "nadie eligió proveedor", que es un estado de configuración y
    # tiene que decirse como tal.
    if not cadena:
        stats["sin_modelo"] = True
        logger.error(
            "No hay ningún modelo activo en `modelo_ia`, así que no se sintetiza "
            "nada. Dale de alta uno con POST /modelos o prendé alguno de los que "
            "ya están con PATCH /modelos/{id}?activo=true."
        )
        return stats

    # `expunge` una sola vez para toda la corrida, no una por cluster.
    #
    # **No sobra**: `expire_on_commit` está en `True` (el default) y
    # `sintetizar_cluster` commitea al final de cada cluster, lo que expira el
    # `ModeloIA`; el primer acceso a un atributo suyo en el cluster siguiente
    # dispara un SELECT de recarga. Medido: **una query por cluster**, o sea
    # exactamente el N+1 que se quería evitar. Desprendido de la sesión, el
    # objeto conserva sus valores y ningún commit lo toca.
    #
    # **Y va acá, antes de cualquier cosa que commitee.** Expulsar un objeto ya
    # expirado lo deja desprendido *y sin valores*, así que el primer acceso
    # tira `DetachedInstanceError` en vez de recargar. El barrido de caducados
    # de abajo commitea, así que ponerlo antes rompía la corrida entera.
    #
    # Es la misma trampa que ya está documentada más arriba, en
    # `descartar_vencidos_sin_sintetizar`.
    #
    # Con cadena vale para todos sus miembros y no solo para el titular: el
    # suplente se toca recién cuando el titular falla, o sea despues de varios
    # commits, y para entonces ya estaria expirado.
    for miembro in cadena:
        session.expunge(miembro)

    titular, *suplentes = cadena
    if suplentes:
        logger.info(
            f"Sintetizando con {titular.nombre} ({titular.modelo}); "
            f"de suplentes: {', '.join(m.nombre for m in suplentes)}"
        )
    else:
        logger.info(f"Sintetizando con {titular.nombre} ({titular.modelo})")

    # Recién con un modelo prendido se cierra la cuenta de lo que caducó: si no,
    # lo que quedó fuera de plazo desaparece sin que nadie se entere.
    stats["vencidos_sin_publicar"] = descartar_vencidos_sin_sintetizar(session)

    pendientes = clusters_pendientes(session)
    stats["pendientes"] = len(pendientes)

    # Estado de la corrida, no de un cluster: quién se cayó de la cadena y por
    # qué, y cuántos fallos seguidos lleva cada uno. Ver
    # `FALLOS_SEGUIDOS_PARA_AGOTAR`.
    agotados: Dict[str, str] = stats["agotados"]
    fallos_seguidos: Dict[str, int] = {}

    for cluster in pendientes:
        disponibles = [m for m in cadena if m.nombre not in agotados]

        resultado, usado, motivo = _intentar_con_la_cadena(
            session, cluster, disponibles, agotados, fallos_seguidos
        )

        if motivo == "bloqueado":
            stats["bloqueados"] += 1
            continue

        if motivo == "sin_salida":
            # **Que la cadena se haya vaciado NO es un cluster fallido.** Si
            # todos quedaron agotados, lo que pasó es que el motor se quedó sin
            # proveedores, y contarlo como fallo del cluster apunta a un
            # problema con los clusters cuando el problema es la configuración —
            # el mismo diagnóstico engañoso que ya se corrigió dos veces, para
            # "no hay ninguna fila activa" y para "la fila está pero no tiene
            # con qué autenticarse".
            #
            # Se corta acá y no al principio de la vuelta siguiente para que el
            # cluster que destapó el agotamiento tampoco quede contado como
            # fallido. Los clusters pendientes quedan sin marca, así que entran
            # en carrera solos en la corrida siguiente.
            if all(m.nombre in agotados for m in cadena):
                stats["sin_credencial"] = True
                logger.error(
                    f"Se corta la síntesis: no queda ningún modelo en pie "
                    f"({agotados}). Los clusters pendientes quedan intactos y "
                    f"se reintentan solos cuando la configuración esté."
                )
                break

            # Quedan proveedores en pie y este cluster igual no salió: ése sí es
            # un fallo del cluster, y la corrida sigue.
            stats["fallidos"] += 1
            continue

        stats["sintetizados"] += 1
        stats["por_modelo"][usado.nombre] = stats["por_modelo"].get(usado.nombre, 0) + 1
        for clave in ("creados", "actualizados", "descartados"):
            stats[clave] += resultado[clave]

    logger.info(f"Síntesis completada: {stats}")
    return stats
