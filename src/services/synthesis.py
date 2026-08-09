"""
Síntesis neutra por ángulo, con Gemini.

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
import json
import logging
import unicodedata
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import settings
from ..models import Cluster, Medio, Noticia, Sintesis, SintesisNoticia
from .clustering import ESTADO_ABIERTO, ESTADO_PROCESADO
from .preprocessing import construir_evidencia

logger = logging.getLogger(__name__)


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
    """Falta la API key. Reintentar no sirve."""


# --- Esquema de la respuesta del modelo -------------------------------------
# Se le pasa a Gemini como `response_schema` para que devuelva JSON válido por
# construcción, en vez de pedírselo en prosa y after parsear a la esperanza.


class EnfoqueMedio(BaseModel):
    medio: str
    destaco: str
    omitio: str
    cita: str = PydanticField(description="Frase del cuerpo que respalda lo anterior")


class AnguloGenerado(BaseModel):
    id_existente: Optional[int] = PydanticField(
        default=None,
        description="Id del ángulo ya publicado que este actualiza; null si es nuevo",
    )
    titulo_angulo: str
    resumen_neutro: str
    puntos_clave: List[str]
    comparativa_enfoques: List[EnfoqueMedio]
    notas: List[int] = PydanticField(description="Números de las notas que lo respaldan")


class RespuestaSintesis(BaseModel):
    angulos: List[AnguloGenerado]


_cliente = None


def get_cliente():
    """Cliente de Gemini, creado la primera vez que se usa."""
    global _cliente

    if _cliente is None:
        if not settings.GEMINI_API_KEY or settings.GEMINI_API_KEY.startswith("tu_"):
            raise SintesisSinConfigurar(
                "GEMINI_API_KEY no está configurada en el .env"
            )
        from google import genai

        _cliente = genai.Client(api_key=settings.GEMINI_API_KEY)

    return _cliente


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
    la corrida siguiente; sin esto perdería su publicación en silencio. El
    recorte por fecha evita revivir noticias viejas al arrancar el sistema.
    """
    limite = datetime.utcnow() - timedelta(hours=settings.HORAS_CLUSTER_ABIERTO * 2)

    candidatos = session.exec(
        select(Cluster).where(
            Cluster.estado.in_([ESTADO_ABIERTO, ESTADO_PROCESADO]),
            Cluster.fecha_creacion >= limite,
        )
    ).all()

    ya_con_angulo = set(session.exec(select(SintesisNoticia.noticia_id)).all())

    pendientes: List[Cluster] = []
    for cluster in candidatos:
        noticias = session.exec(
            select(Noticia).where(Noticia.cluster_id == cluster.id)
        ).all()

        if (
            cluster.noticias_al_sintetizar is not None
            and len(noticias) <= cluster.noticias_al_sintetizar
        ):
            continue

        sin_angulo = [n for n in noticias if n.id not in ya_con_angulo]
        if len({n.medio_id for n in sin_angulo}) >= settings.MIN_MEDIOS_CLUSTER:
            pendientes.append(cluster)

    return pendientes


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
        bloques.append(
            f"--- NOTA {numero} | {medio}\n"
            f"TITULAR: {noticia.titulo}\n"
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
def llamar_modelo(prompt: str) -> RespuestaSintesis:
    """
    Le pide la síntesis al modelo y valida la respuesta contra el esquema.

    Con `response_schema` la salida es JSON válido por construcción, así que casi
    toda la familia de fallos de formato desaparece de raíz. Los reintentos con
    espera creciente cubren el rate limit, que es esperable: en una corrida se
    sintetizan todos los clusters publicables de una (medido: 21) y la capa
    gratuita limita por minuto.

    El bloqueo por filtros de contenido no se reintenta: la misma entrada da el
    mismo bloqueo.
    """
    respuesta = get_cliente().models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": RespuestaSintesis,
            "temperature": settings.GEMINI_TEMPERATURA,
            # Los tokens de razonamiento se facturan como salida, que es la
            # parte cara de esta fase. Ver GEMINI_THINKING_LEVEL.
            "thinking_config": {"thinking_level": settings.GEMINI_THINKING_LEVEL},
        },
    )

    candidatos = getattr(respuesta, "candidates", None) or []
    if candidatos:
        motivo = str(getattr(candidatos[0], "finish_reason", "") or "")
        if "SAFETY" in motivo.upper() or "BLOCK" in motivo.upper():
            raise SintesisBloqueada(f"El proveedor bloqueó la respuesta ({motivo})")

    if not respuesta.text:
        raise ValueError("El modelo devolvió una respuesta vacía")

    uso = getattr(respuesta, "usage_metadata", None)
    if uso is not None:
        logger.info(
            f"Tokens: entrada={uso.prompt_token_count} "
            f"salida={uso.candidates_token_count} "
            f"razonamiento={getattr(uso, 'thoughts_token_count', None) or 0}"
        )

    return RespuestaSintesis.model_validate(json.loads(respuesta.text))


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
) -> dict:
    """
    Guarda los ángulos válidos. Los inválidos se descartan sin tocar el cluster.

    Un ángulo nuevo se publica solo si sus noticias cubren `MIN_MEDIOS_CLUSTER`
    medios distintos: sin dos voces no hay enfoques que comparar. El filtro va
    acá, sobre el ángulo, y no sobre el cluster — un cluster de 5 medios puede
    contener un ángulo que cubrió uno solo.

    A los ángulos que ya existen no se les aplica ese filtro ni se les quitan
    noticias: ya se publicaron, y del otro lado tienen lectores encima.
    """
    por_numero = {numero: n for numero, n in enumerate(enviadas, start=1)}
    nombres_del_cluster = sorted({medios[n.medio_id] for n in enviadas})
    existentes = {s.id: s for s in cluster.sintesis}
    stats = {"creados": 0, "actualizados": 0, "descartados": 0}

    for angulo in respuesta.angulos:
        notas = [por_numero[n] for n in angulo.notas if n in por_numero]
        comparativa = _comparativa_validada(
            angulo.comparativa_enfoques, nombres_del_cluster
        )

        # Un `id_existente` que no corresponde a este cluster es alucinación:
        # se trata como ángulo nuevo.
        sintesis = existentes.get(angulo.id_existente) if angulo.id_existente else None

        if sintesis is None:
            if len({n.medio_id for n in notas}) < settings.MIN_MEDIOS_CLUSTER:
                stats["descartados"] += 1
                continue
            sintesis = Sintesis(cluster_id=cluster.id, titulo_angulo=angulo.titulo_angulo)
            sintesis.noticias = notas
            stats["creados"] += 1
        else:
            # El título no se toca: es lo que el backend ya publicó.
            faltantes = [n for n in notas if n not in sintesis.noticias]
            sintesis.noticias = list(sintesis.noticias) + faltantes
            sintesis.enviado_backend = False  # hay contenido nuevo que entregar
            stats["actualizados"] += 1

        sintesis.resumen_neutro = angulo.resumen_neutro
        sintesis.puntos_clave = angulo.puntos_clave
        sintesis.comparativa_enfoques = comparativa
        sintesis.fecha_generacion = datetime.utcnow()
        session.add(sintesis)

    return stats


def sintetizar_cluster(session: Session, cluster: Cluster) -> dict:
    """Genera (o actualiza) los ángulos de un cluster y deja la marca puesta."""
    evidencia = construir_evidencia(session, cluster)
    enviadas = evidencia["noticias"]
    if not enviadas:
        return {"creados": 0, "actualizados": 0, "descartados": 0}

    medios = {m.id: m.nombre for m in session.exec(select(Medio)).all()}
    prompt = construir_prompt(evidencia, enviadas, medios, cluster.sintesis)

    respuesta = llamar_modelo(prompt)
    stats = _persistir(session, cluster, respuesta, enviadas, medios)

    # La marca se pone aunque no se haya publicado nada: es lo que evita que un
    # cluster sin ángulos válidos se reintente en cada corrida para siempre.
    cluster.noticias_al_sintetizar = len(
        session.exec(select(Noticia).where(Noticia.cluster_id == cluster.id)).all()
    )
    session.add(cluster)
    session.commit()

    return stats


def sintetizar_pendientes(session: Session) -> dict:
    """
    Sintetiza todos los clusters con material nuevo suficiente.

    Un cluster que falla no arrastra a los demás: se registra y se sigue. La
    corrida siguiente lo reintenta sola, porque la marca solo se escribe cuando
    la síntesis llegó a persistirse.
    """
    pendientes = clusters_pendientes(session)
    stats = {
        "pendientes": len(pendientes),
        "sintetizados": 0,
        "creados": 0,
        "actualizados": 0,
        "descartados": 0,
        "bloqueados": 0,
        "fallidos": 0,
    }

    for cluster in pendientes:
        try:
            resultado = sintetizar_cluster(session, cluster)
        except SintesisBloqueada as error:
            session.rollback()
            stats["bloqueados"] += 1
            logger.warning(f"Cluster {cluster.id} bloqueado por el proveedor: {error}")
            continue
        except Exception as error:
            session.rollback()
            stats["fallidos"] += 1
            logger.exception(f"Falló la síntesis del cluster {cluster.id}: {error}")
            continue

        stats["sintetizados"] += 1
        for clave in ("creados", "actualizados", "descartados"):
            stats[clave] += resultado[clave]

    logger.info(f"Síntesis completada: {stats}")
    return stats
