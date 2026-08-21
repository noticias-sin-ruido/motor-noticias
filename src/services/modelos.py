"""
Elegir el modelo con el que se sintetiza, y comprobar uno antes de aceptarlo.

Ver specs/roadmap.md, backlog punto 2, y `models/modelo_ia.py`.
"""
import json
import logging
from typing import Optional, Tuple

from sqlmodel import Session, select

from ..models import Adaptador, ModeloIA, ModoEstructura
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

    `None` significa **que nadie eligió proveedor**, y desde la etapa 4 del
    punto 2 eso no es un default sino configuración incompleta: la síntesis no
    corre y `sintetizar_pendientes` lo dice. Hasta la etapa 3 significaba "usá
    el camino histórico de Gemini", que era un proveedor privilegiado escondido
    en el código; se retiró.

    El `order_by` es defensa en profundidad, no la regla: prender un modelo
    apaga a los demás (`main._apagar_los_demas`), así que en condiciones
    normales hay como mucho una fila activa. Si por lo que fuera hubiera dos,
    gana la de menor `prioridad` y desempata el `id` — determinista, aunque no
    sea un estado que la API deje construir.
    """
    return session.exec(
        select(ModeloIA)
        .where(ModeloIA.activo.is_(True))
        .order_by(ModeloIA.prioridad, ModeloIA.id)
    ).first()


# Qué hacer en lugar de cada adaptador que quedó reservado. Un mensaje genérico
# deja al operador sin saber si su proveedor está fuera de alcance o si hay una
# forma de usarlo hoy mismo — que en el caso de Anthropic, la hay.
SALIDAS = {
    Adaptador.ANTHROPIC: (
        "Anthropic se usa hoy con el adaptador `openai_compatible` y "
        "`base_url=https://api.anthropic.com/v1`: expone capa compatible. "
        "Dos advertencias. Su capa **ignora `response_format` en silencio** "
        "(verificado), así que el sondeo va a caer a `tools` — y que `tools` "
        "funcione ahí **no está comprobado**, porque el proyecto nunca tuvo una "
        "credencial con crédito para probarlo. Si el alta falla, ése es el "
        "motivo, y la salida es un gateway (LiteLLM, OpenRouter) que traduzca."
    ),
}


def construir(modelo: ModeloIA):
    """
    El adaptador de este modelo, listo para usar.

    Levanta si el adaptador no existe en vez de reventar con un `KeyError`:
    `Adaptador` declara tres protocolos y `anthropic` quedó reservado a
    propósito (ver specs/roadmap.md, punto 2), así que quien lo elija merece
    leer por qué no está y **qué hacer en su lugar**.
    """
    fabrica = REGISTRO.get(modelo.adaptador)
    if fabrica is None:
        disponibles = ", ".join(sorted(a.value for a in REGISTRO))
        salida = SALIDAS.get(
            modelo.adaptador,
            "Un proveedor que no hable ninguno de estos se resuelve poniéndole "
            "adelante un gateway que traduzca a formato OpenAI.",
        )
        raise AdaptadorNoImplementado(
            f"El adaptador {modelo.adaptador.value!r} no está implementado. "
            f"Disponibles: {disponibles}. {salida}"
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

        # **El cuerpo va al log y NO a la respuesta**, aunque sea la pista más
        # útil que hay acá. Es la misma regla que ya aplica `_mensaje_de_error`
        # para las respuestas 4xx, extendida a la rama que se le había escapado:
        # la que contesta 200. `POST /modelos` no tiene autenticación y acepta
        # cualquier `base_url`, así que devolver lo que contestó el destino
        # convierte un SSRF a ciegas en una lectura de servicios internos —
        # basta con que ese servicio hable formato OpenAI, que es justamente el
        # caso de un LiteLLM o un vLLM en la red de al lado.
        #
        # El operador que depura su propio modelo tiene el log en la misma
        # máquina; quien sondea a ciegas desde afuera, no.
        logger.warning(
            f"Sondeo de {candidato.nombre} vía {modo.value}: respondió 200 sin la "
            f"forma pedida. Lo que devolvió: {crudo[:500]!r}"
        )
        intentos.append(
            f"{modo.value} → respondió 200 pero sin la forma que pide el esquema "
            f"(el cuerpo quedó en el log del motor)"
        )

    # El mensaje **no afirma la causa**: puede ser que el proveedor ignore el
    # esquema (el caso Anthropic), pero también un modelo inexistente o una key
    # inválida, y desde acá no se distinguen.
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
