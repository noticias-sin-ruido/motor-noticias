"""
Entrega de las síntesis al back-end del producto (web/mobile), por webhook.

El motor no expone las síntesis por polling: las empuja. El back-end las
persiste en su propia base junto a lo suyo (likes, comentarios, suscripciones),
así que del otro lado cada síntesis es una fila con vida propia y `Sintesis.id`
es la clave con la que se la reconoce entre entregas.

El contrato completo del payload, pensado para pasárselo al equipo de back-end,
está en `specs/webhook_contract.md`.

Tres decisiones que explican la forma de este módulo:

1. **Un request por síntesis, no un lote.** El estado de entrega vive por fila
   (`enviado_backend`), así que el status HTTP es directamente el acuse de esa
   fila. Con un lote, un 200 parcial dejaría sin saber qué marcar. El volumen no
   lo justifica: una corrida real entregó 11 publicaciones.

2. **El paso de entrega es un barrido, no un envío de lo recién creado.** Toma
   todo lo que tenga `enviado_backend=False`, sin importar de cuándo sea. Con
   eso el primer intento y el reintento de lo que falló hace horas son el mismo
   código, y no hace falta un job de reintento aparte.

3. **El intento se cuenta pase lo que pase.** Si el contador solo avanzara en
   caso de éxito, un back-end que responde 500 siempre se quedaría en cero y el
   tope de reintentos no se alcanzaría nunca.

Ver specs/change_logs.md, Fase 4, para el detalle de lo evaluado y descartado.
"""
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

import httpx
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import settings
from ..tiempo import ahora_utc
from ..models import Medio, Sintesis
from .alerts import enviar_alerta
from .clustering import ESTADO_ABIERTO

logger = logging.getLogger(__name__)

# Versión del contrato del payload. Va en el cuerpo y no en la URL para que el
# receptor pueda soportar dos versiones a la vez durante una migración, sin que
# tengamos que coordinar un cambio de endpoint con el otro equipo.
VERSION_PAYLOAD = 1

HEADER_FIRMA = "X-SinRuido-Signature"
HEADER_TIMESTAMP = "X-SinRuido-Timestamp"

# Códigos 4xx que sí conviene reintentar: no son un rechazo del contenido sino
# una condición pasajera del receptor.
CODIGOS_4XX_REINTENTABLES = {408, 425, 429}


class EntregaRechazada(Exception):
    """
    El back-end rechazó el payload con un 4xx.

    No se reintenta: el mismo cuerpo va a dar el mismo rechazo. Un 4xx acá
    significa que el contrato se rompió (un campo que cambió de forma, una firma
    que no valida), y eso se arregla con una corrección, no con insistir.
    """


def _iso(momento: Optional[datetime]) -> Optional[str]:
    """
    Fecha en ISO 8601 con la Z explícita.

    El motor guarda UTC en datetimes *naive* (ver `src/tiempo.py`): serializados
    tal cual viajan sin zona, y del otro lado se interpretan como hora local. La
    Z lo vuelve inequívoco.

    Acá va UTC y no UTC-3, a diferencia de la API: `webhook_contract.md` ya le
    prometió `Z` al back-end, y es un contrato con otro equipo. Lo que importa
    es que la zona esté declarada, no cuál sea.
    """
    if momento is None:
        return None
    return momento.replace(microsecond=0).isoformat() + "Z"


def construir_payload(session: Session, sintesis: Sintesis) -> dict:
    """
    Arma el JSON que ve el back-end.

    Dos diferencias con cómo está guardado en la base, ambas a propósito:

    - **La comparativa viaja como lista, con el id del medio**, y no como el
      diccionario indexado por nombre que devuelve el modelo. Un nombre para
      mostrar es un mal identificador: cambia con un rebranding o al corregirle
      una tilde, y del otro lado eso dejaría filas huérfanas.
    - **El hecho (el cluster) viaja anidado**, para que el back-end pueda
      agrupar los varios ángulos de una misma historia sin inferirlo del texto.
    """
    medios = session.exec(select(Medio)).all()
    nombre_por_id: Dict[int, str] = {m.id: m.nombre for m in medios}
    id_por_nombre: Dict[str, int] = {m.nombre: m.id for m in medios}

    comparativa = []
    for nombre in sorted(sintesis.comparativa_enfoques):
        enfoque = sintesis.comparativa_enfoques[nombre] or {}
        medio_id = id_por_nombre.get(nombre)
        if medio_id is None:
            # No debería pasar: `synthesis._comparativa_validada` ya normaliza
            # los nombres contra la base antes de guardar.
            logger.warning(
                f"Síntesis {sintesis.id}: se omite la comparativa de un medio "
                f"desconocido ({nombre})"
            )
            continue
        comparativa.append(
            {
                "medio": {"id": medio_id, "nombre": nombre},
                "destaco": enfoque.get("destaco", ""),
                "omitio": enfoque.get("omitio", ""),
                "cita": enfoque.get("cita", ""),
            }
        )

    fuentes = [
        {
            "medio": {"id": n.medio_id, "nombre": nombre_por_id.get(n.medio_id, "")},
            "titulo": n.titulo,
            "url": n.url,
            "fecha_publicacion": _iso(n.fecha_publicacion),
        }
        for n in sorted(sintesis.noticias, key=lambda n: (n.fecha_publicacion, n.id))
    ]

    cluster = sintesis.cluster

    return {
        "version": VERSION_PAYLOAD,
        # Es informativo: el receptor hace upsert por `sintesis.id` en los dos
        # casos. Sirve para decidir cosas suyas, como notificar a los
        # suscriptores solo cuando la publicación es nueva.
        "evento": "sintesis.creada" if sintesis.fecha_envio is None else "sintesis.actualizada",
        "sintesis": {
            "id": sintesis.id,
            "titulo": sintesis.titulo_angulo,
            "resumen": sintesis.resumen_neutro,
            "puntos_clave": list(sintesis.puntos_clave or []),
            # De las listas cerradas de `services/topicos.py`. `topicos` es una
            # o dos categorías pares (no una principal y otra secundaria);
            # `subtopicos` es un recorte más fino dentro de esas categorías y
            # puede venir vacío. Ninguna de las dos cambia entre entregas.
            "topicos": list(sintesis.topicos or []),
            "subtopicos": list(sintesis.subtopicos or []),
            # Permite descartar una entrega que llegó tarde: si dos requests de
            # la misma síntesis se cruzan, gana la de fecha más nueva.
            "fecha_generacion": _iso(sintesis.fecha_generacion),
            # Null salvo que Gemini haya marcado este ángulo de relevancia
            # nacional (`AnguloGenerado.relevancia_social`) -- no todo ángulo
            # tiene copy para redes. No se retracta si una resíntesis
            # posterior deja de considerarlo relevante: ver
            # specs/change_logs.md, "Copy para redes sociales".
            "publicacion_redes": (
                {
                    "resumen": sintesis.publicacion_redes.resumen_redes,
                    "hashtags": list(sintesis.publicacion_redes.hashtags or []),
                }
                if sintesis.publicacion_redes is not None
                else None
            ),
        },
        "hecho": {
            # Solo el id: es lo que necesita el back-end para agrupar los
            # distintos ángulos de una misma historia.
            #
            # NO se manda `Cluster.titulo_evento` a propósito. Ese campo es el
            # titular de la primera nota que formó el cluster, o sea el encuadre
            # de un medio puntual — mandarlo sería entregar como nombre neutro
            # del hecho justo lo que el producto se propone no hacer. Si más
            # adelante hace falta una etiqueta para agrupar en pantalla, hay que
            # generarla neutra, no reciclar un titular.
            "id": sintesis.cluster_id,
            # Un hecho abierto todavía puede sumar ángulos o actualizar los que
            # ya tiene. Alcanza para mostrarlo como "en desarrollo".
            "abierto": bool(cluster and cluster.estado == ESTADO_ABIERTO),
        },
        "comparativa": comparativa,
        "fuentes": fuentes,
    }


def serializar(payload: dict) -> bytes:
    """
    Los bytes exactos que viajan, que son los mismos que se firman.

    Se serializa una sola vez y se manda con `content=`, no con `json=`: si el
    cliente HTTP volviera a serializar el diccionario por su cuenta, cualquier
    diferencia de espaciado o de escapes rompería la firma del otro lado.

    `ensure_ascii=False` porque el contenido es en español y escapar cada tilde
    a `\\uXXXX` infla el cuerpo sin ganar nada.
    """
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def firmar(cuerpo: bytes, timestamp: str) -> str:
    """
    Firma HMAC-SHA256 del cuerpo, en el formato `sha256=<hex>`.

    El timestamp entra *dentro* del mensaje firmado, no solo en un header
    aparte: si viajara suelto, cualquiera podría reenviar un request capturado
    cambiándole la fecha y la firma seguiría validando. Atado a la firma, el
    receptor puede rechazar lo viejo y con eso acota la ventana de replay.
    """
    mensaje = timestamp.encode("utf-8") + b"." + cuerpo
    firma = hmac.new(
        (settings.WEBHOOK_SECRET or "").encode("utf-8"), mensaje, hashlib.sha256
    ).hexdigest()
    return f"sha256={firma}"


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _postear(cuerpo: bytes, headers: Dict[str, str]) -> int:
    """
    Manda el request. Reintenta lo pasajero y no lo definitivo.

    `EntregaRechazada` no hereda de `httpx.HTTPError`, así que `tenacity` la deja
    pasar sin reintentar — que es exactamente lo que se busca para un 4xx.
    """
    respuesta = httpx.post(
        settings.WEBHOOK_URL,
        content=cuerpo,
        headers=headers,
        timeout=settings.WEBHOOK_TIMEOUT,
    )

    if 400 <= respuesta.status_code < 500 and respuesta.status_code not in CODIGOS_4XX_REINTENTABLES:
        # Se recorta el cuerpo: un error del receptor puede venir con una página
        # HTML entera, y eso en el log no aporta nada.
        raise EntregaRechazada(f"HTTP {respuesta.status_code}: {respuesta.text[:200]}")

    respuesta.raise_for_status()
    return respuesta.status_code


def entregar_sintesis(session: Session, sintesis: Sintesis) -> int:
    """
    Entrega una síntesis y deja el intento contado, haya salido bien o mal.

    El `finally` es la parte que importa: si el contador solo avanzara con el
    éxito, un back-end permanentemente caído nunca alcanzaría
    `WEBHOOK_MAX_INTENTOS` y el barrido lo reintentaría cada 15 minutos para
    siempre, sin que nadie se entere.
    """
    payload = construir_payload(session, sintesis)
    cuerpo = serializar(payload)

    # `time.time()` y NO `datetime.utcnow().timestamp()`. El segundo devuelve un
    # datetime *naive*, y `.timestamp()` sobre un naive lo interpreta como hora
    # local: desde Argentina el timestamp sale corrido 3 horas y cualquier
    # receptor que valide la ventana anti-replay —como pide nuestro propio
    # contrato— rechaza todo con un 401. Encontrado probando contra un receptor
    # real; con el cliente HTTP mockeado no se ve.
    timestamp = str(int(time.time()))

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        HEADER_TIMESTAMP: timestamp,
        HEADER_FIRMA: firmar(cuerpo, timestamp),
    }

    sintesis.intentos_envio += 1
    try:
        codigo = _postear(cuerpo, headers)
        sintesis.enviado_backend = True
        sintesis.fecha_envio = ahora_utc()
        return codigo
    finally:
        session.add(sintesis)
        session.commit()


def sintesis_pendientes(session: Session, forzar: bool = False) -> List[Sintesis]:
    """
    Síntesis sin entregar que todavía tienen reintentos disponibles.

    Van en orden de id, que es el orden en que se generaron: si el receptor
    muestra las publicaciones por orden de llegada, así las ve como pasaron.

    `forzar` incluye también las que agotaron los intentos. Es la salida para el
    caso operativo real: el back-end estuvo rechazando por un bug suyo, lo
    arreglan, y hay que reenviarles lo que quedó trabado sin esperar a que una
    re-síntesis resetee el contador.
    """
    condiciones = [Sintesis.enviado_backend == False]  # noqa: E712 -- lo exige SQLAlchemy
    if not forzar:
        condiciones.append(Sintesis.intentos_envio < settings.WEBHOOK_MAX_INTENTOS)

    return list(
        session.exec(
            select(Sintesis)
            .options(
                selectinload(Sintesis.publicacion_redes),
                # `construir_payload` lee `.noticias` y `.cluster` de cada
                # síntesis. Sin precargarlas acá, cada una dispara su propia
                # consulta lazy dentro del loop de `entregar_pendientes` --
                # medido en una corrida real con 192 pendientes: 192 + 210
                # queries que una sola consulta bulk por relación evita.
                selectinload(Sintesis.noticias),
                selectinload(Sintesis.cluster),
            )
            .where(*condiciones)
            .order_by(Sintesis.id)
        ).all()
    )


def entregar_pendientes(session: Session, forzar: bool = False) -> dict:
    """
    Barre las síntesis sin entregar y las empuja al back-end.

    Una que falla no arrastra a las demás. Sin configuración no hace nada y lo
    registra, en vez de fallar: durante el desarrollo el webhook todavía no
    existe, y hacer fallar el paso convertiría en ruido la alerta del pipeline,
    que es justo la que tiene que seguir significando algo.
    """
    if not settings.WEBHOOK_URL or not settings.WEBHOOK_SECRET:
        logger.error(
            "Webhook sin configurar (WEBHOOK_URL / WEBHOOK_SECRET): las síntesis "
            "quedan pendientes de entrega en la base"
        )
        return {"estado": "sin configurar", "pendientes": 0, "entregadas": 0}

    pendientes = sintesis_pendientes(session, forzar=forzar)
    stats = {
        "estado": "ok",
        "pendientes": len(pendientes),
        "entregadas": 0,
        "rechazadas": 0,
        "fallidas": 0,
        # Las que cruzaron el tope EN ESTA CORRIDA: son las que disparan aviso.
        "agotadas": 0,
        # Cuántas hay trabadas en total. Es visibilidad operativa y no genera
        # aviso: si lo hiciera, una sola síntesis trabada mandaría un mail por
        # hora para siempre y el aviso que importa quedaría enterrado.
        "agotadas_total": 0,
    }

    # Cuáles llegaban con reintentos disponibles: solo esas pueden "cruzar" el
    # tope en esta corrida, y solo de esas hay que avisar.
    con_margen = {
        s.id for s in pendientes if s.intentos_envio < settings.WEBHOOK_MAX_INTENTOS
    }

    # `entregar_sintesis` comitea por síntesis a propósito (ver su docstring:
    # el intento tiene que quedar contado aunque el proceso se caiga a mitad
    # del barrido). El problema es que `session.commit()` expira por defecto
    # los atributos de TODOS los objetos que la sesión tiene cargados, no solo
    # el que se acaba de commitear -- así que la síntesis 2 del loop, ya
    # cargada arriba junto con sus relaciones, queda expirada por el commit de
    # la síntesis 1, y leer o escribir cualquier atributo suyo (acá mismo, y
    # también en `agotadas` más abajo) dispara su propia recarga -- fila
    # completa más relaciones -- antes de poder usarla. Medido en una corrida
    # real con 192 pendientes: ~780 recargas de esas, más de un tercio de
    # todas las queries del barrido. Desactivar la expiración automática
    # mantiene el commit por ítem (la durabilidad que se busca) sin pagar la
    # recarga: los datos ya están en memoria desde el `selectinload` de
    # `sintesis_pendientes`, y las mutaciones del loop (`enviado_backend`,
    # `intentos_envio`) quedan visibles en los mismos objetos sin releer nada.
    session.expire_on_commit = False
    try:
        for sintesis in pendientes:
            sintesis_id = sintesis.id
            try:
                entregar_sintesis(session, sintesis)
            except EntregaRechazada as error:
                stats["rechazadas"] += 1
                logger.error(f"El back-end rechazó la síntesis {sintesis_id}: {error}")
                enviar_alerta(
                    asunto="[Sin Ruido] El back-end está rechazando las síntesis",
                    cuerpo=(
                        f"Síntesis {sintesis_id}: {error}\n\n"
                        "Un 4xx es un problema de contrato (payload o firma), no una "
                        "caída: no se reintenta hasta corregirlo."
                    ),
                    clave="webhook:rechazo",
                )
            except Exception as error:
                stats["fallidas"] += 1
                logger.warning(f"No se pudo entregar la síntesis {sintesis_id}: {error}")
            else:
                stats["entregadas"] += 1

        stats["agotadas_total"] = len(
            session.exec(
                select(Sintesis.id).where(
                    Sintesis.enviado_backend == False,  # noqa: E712
                    Sintesis.intentos_envio >= settings.WEBHOOK_MAX_INTENTOS,
                )
            ).all()
        )

        # El aviso sale solo por las que agotaron los intentos EN ESTA CORRIDA.
        # Antes se avisaba por todas las trabadas, así que una sola disparaba
        # un mail por hora para siempre, sin novedad — y el aviso que hay que
        # leer termina perdido entre los que no.
        agotadas = [
            s for s in pendientes
            if s.id in con_margen
            and not s.enviado_backend
            and s.intentos_envio >= settings.WEBHOOK_MAX_INTENTOS
        ]

        if agotadas:
            stats["agotadas"] = len(agotadas)
            enviar_alerta(
                asunto=f"[Sin Ruido] {len(agotadas)} síntesis sin entregar al back-end",
                cuerpo=(
                    f"Agotaron los {settings.WEBHOOK_MAX_INTENTOS} intentos y dejaron "
                    f"de reintentarse: {', '.join(str(s.id) for s in agotadas)}\n\n"
                    "Resuelto el problema, se reenvían con POST /deliver?forzar=true"
                ),
                clave="webhook:agotadas",
                # Terminal: estas síntesis salen del barrido y no se vuelven a
                # informar. Si el cooldown se traga el aviso, se pierde.
                ignorar_cooldown=True,
            )
    finally:
        session.expire_on_commit = True

    logger.info(f"Entrega al back-end completada: {stats}")
    return stats
