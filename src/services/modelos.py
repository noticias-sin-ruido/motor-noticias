"""
Elegir el modelo con el que se sintetiza, y comprobar uno antes de aceptarlo.

Ver specs/roadmap.md, backlog punto 2, y `models/modelo_ia.py`.
"""
import json
import logging
from typing import Optional, Tuple

from sqlmodel import Session, select

from ..models import ModeloIA, ModoEstructura
from .proveedores import (
    REGISTRO,
    AdaptadorNoImplementado,
    ErrorDeProveedor,
    RespuestaBloqueada,
)
from .proveedores.openai_compatible import TIMEOUT_SONDEO_SEGUNDOS

logger = logging.getLogger(__name__)

# Pedido del sondeo. Corto a propósito: lo que se está probando es si el
# proveedor **respeta el esquema**, no si sabe redactar.
PROMPT_SONDEO = (
    "Devolvé un ejemplo mínimo con la estructura pedida. Un solo elemento, "
    "textos de una o dos palabras. No expliques nada."
)


def modelo_activo(session: Session) -> Optional[ModeloIA]:
    """
    El modelo con el que hay que sintetizar, o `None`.

    `None` significa **usar el camino histórico de Gemini**, y es el estado de
    una base que nunca configuró nada. Es la red de seguridad del
    desacoplamiento: mientras no haya filas activas, el motor se comporta
    exactamente como antes de que esta tabla existiera.

    Con varias activas gana la de menor `prioridad`. Hoy eso solo desempata;
    el día que se arme la cadena de fallback, es el orden en que se intenta.
    """
    return session.exec(
        select(ModeloIA)
        .where(ModeloIA.activo.is_(True))
        .order_by(ModeloIA.prioridad, ModeloIA.id)
    ).first()


def construir(modelo: ModeloIA):
    """
    El adaptador de este modelo, listo para usar.

    Levanta si el adaptador todavía no existe en vez de reventar con un
    `KeyError`: `Adaptador` ya declara los tres protocolos, pero en la etapa 1
    solo está implementado el compatible con OpenAI, y quien dé de alta un
    `gemini` nativo merece leer por qué no se puede todavía.
    """
    fabrica = REGISTRO.get(modelo.adaptador)
    if fabrica is None:
        disponibles = ", ".join(sorted(a.value for a in REGISTRO))
        raise AdaptadorNoImplementado(
            f"El adaptador {modelo.adaptador.value!r} todavía no está "
            f"implementado. Disponibles: {disponibles}. "
            f"Un proveedor que no hable ninguno de estos se resuelve poniéndole "
            f"adelante un gateway que traduzca a formato OpenAI."
        )
    return fabrica(modelo)


def sondear(modelo: ModeloIA) -> Tuple[ModoEstructura, str]:
    """
    Comprueba que el modelo sirva, y **descubre cómo pedirle estructura**.

    Devuelve el modo que funcionó y un resumen para mostrarle al operador.

    No alcanza con preguntar "¿responde?", y esto no es cautela abstracta: la
    capa de compatibilidad de Anthropic **ignora `response_format` en silencio**
    (documentado por ellos mismos, verificado el 20/08/2026). Responde 200,
    devuelve texto correcto y descarta el esquema. Un alta que solo comprobara
    conectividad habría aceptado ese modelo, y el fallo habría aparecido recién
    en la síntesis — cada 15 minutos, en el paso más caro del pipeline.

    Por eso el sondeo pregunta **"¿me devolvés la forma que te pedí?"** y, si el
    primer mecanismo no la da, prueba el otro antes de rechazar.
    """
    from .synthesis import RespuestaSintesis

    # **Se sondea sobre una copia**, no sobre el objeto que nos pasaron. El
    # bucle tiene que ir cambiando `modo_estructura` para probar cada mecanismo,
    # y si ese objeto estuviera adjunto a una sesión —lo natural el día que se
    # re-sondee una fila ya guardada— la mutación quedaría pendiente y se
    # commitearía con el próximo commit **aunque el sondeo hubiera fallado**.
    candidato = modelo.model_copy()

    # **Se arma una sola vez y FUERA del bucle.** Adentro, un adaptador que no
    # existe o un `base_url` inválido se contarían como "este modo no dio la
    # forma" y terminarían reportados como "el proveedor ignora el esquema" —
    # una causa inventada que manda a quien lea por el camino equivocado. El
    # adaptador lee `modo_estructura` al armar cada pedido, así que alcanza con
    # mutar la copia entre vueltas.
    proveedor = construir(candidato)

    # **Solo los modos que este adaptador dice soportar.** El nativo de Gemini
    # tiene un único mecanismo (`response_schema`), así que probarle `TOOLS`
    # daría un segundo fallo idéntico al primero y el mensaje final le diría al
    # operador que su proveedor no respeta el esquema *por ninguno de los dos
    # mecanismos* — una conclusión falsa sobre uno que nunca se intentó.
    modos = getattr(proveedor, "MODOS_SOPORTADOS", None) or (
        ModoEstructura.RESPONSE_FORMAT,
        ModoEstructura.TOOLS,
    )

    intentos = []
    for modo in modos:
        candidato.modo_estructura = modo
        try:
            crudo = proveedor.probar(
                PROMPT_SONDEO, RespuestaSintesis, timeout=TIMEOUT_SONDEO_SEGUNDOS
            )
        except RespuestaBloqueada as error:
            # Que los filtros bloqueen un pedido tan inocuo dice algo del
            # proveedor, pero no que ignore el esquema. No se sigue probando.
            raise ErrorDeProveedor(
                f"El proveedor bloqueó hasta el pedido de prueba: {error}"
            ) from error
        except ErrorDeProveedor as error:
            # Acá sí corresponde seguir: un 4xx por `response_format` no
            # soportado es justo el caso que el bucle existe para atrapar.
            intentos.append(f"{modo.value} → {error}")
            continue

        if _valida_la_forma(crudo):
            logger.info(f"Sondeo de {candidato.nombre}: sirve vía {modo.value}")
            return modo, f"responde y respeta el esquema vía `{modo.value}`"

        intentos.append(f"{modo.value} → respondió sin la forma pedida: {crudo[:80]!r}")

    # El mensaje **no afirma la causa**: puede ser que el proveedor ignore el
    # esquema (el caso Anthropic), pero también un modelo inexistente o una key
    # inválida, y desde acá no se distinguen. Lo accionable es lo que dijo el
    # proveedor, así que va primero y completo.
    raise ErrorDeProveedor(
        f"No se pudo obtener la estructura que el motor necesita, por ninguno "
        f"de los {len(modos)} mecanismos que soporta este adaptador. Lo que "
        f"respondió el proveedor: "
        + " | ".join(intentos)
        + ". Si el modelo y la credencial son correctos y aun así no respeta el "
        "esquema, usá el adaptador nativo del proveedor cuando exista, o poné "
        "adelante un gateway que traduzca a formato OpenAI."
    )


def _valida_la_forma(crudo: str) -> bool:
    """
    Si lo devuelto tiene la forma que pide el esquema.

    **Se mira la forma y no las reglas de negocio.** `RespuestaSintesis` tiene
    validadores que expresan cosas que un JSON Schema no puede —de eso se trata
    media `synthesis.py`— y un ejemplo de juguete no tiene por qué cumplirlas.
    Exigir `model_validate` completo daría falsos negativos: rechazaríamos
    proveedores que sí respetan el esquema, por no haber inventado una síntesis
    coherente en un pedido que les pidió justamente algo mínimo.

    Pero **el tipo sí se mira**, y no es un detalle: la primera versión aceptaba
    cualquier objeto con la clave `angulos`, así que `{"angulos": null}` y
    `{"angulos": "no entendí"}` pasaban como "respetó el esquema". Ese es
    exactamente el escenario más probable de un proveedor que ignora el
    `response_format` pero igual devuelve *algún* JSON — o sea, justo lo que
    este sondeo existe para detectar.
    """
    try:
        datos = json.loads(crudo)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(datos, dict) and isinstance(datos.get("angulos"), list)
