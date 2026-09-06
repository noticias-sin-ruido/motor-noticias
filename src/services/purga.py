"""
Purga del cuerpo de las noticias huérfanas. Backlog post-1.0, punto 8.

**Se borra solo `contenido_limpio`, nunca la noticia.** Sobreviven título, URL,
guid, fecha, medio y el `embedding`: `GET /search` no se entera y la nota sigue
existiendo como registro. Lo que se libera es el texto que copiamos del medio
para poder procesarlo, una vez que ya cumplió esa función.

**El alcance es deliberadamente chico: solo las huérfanas.** Una noticia sin
cluster que ya venció su ventana no tiene ninguna vía de entrada al pipeline —
ver la condición de seguridad en `_limite_de_purga` — así que purgarla no le
quita nada a nadie. Los clusters cerrados y entregados quedan afuera a
propósito, aunque también son candidatos por el mismo argumento: son una
segunda población con su propia condición de seguridad (la re-síntesis), y
mezclarla acá sería resolver dos problemas con una sola comprobación.

**Esto no se deshace.** El texto purgado no vuelve: la ventana del feed que lo
trajo ya pasó, así que ni re-ingiriendo se recupera. El costo real no es
perderlo para el producto —ya cumplió, tiene que haberse vectorizado antes de
que este paso corra— sino no poder revectorizar si algún día cambia
`EMBEDDING_MODEL`. Por eso `solo_contar=True` existe como primera clase y no
como curiosidad: es cómo se comprueba el alcance contra datos reales antes de
tocarlos.

**"Tiene que haberse vectorizado" es una condición que se exige, no un
supuesto.** `_condicion_de_purga` pide `embedding IS NOT NULL` explícitamente:
si la vectorización fallara alguna corrida —`_correr_paso` en `main.py` está
diseñado para que un paso roto avise y no frene al resto—, esa noticia se
queda sin cluster posible (`agrupar_pendientes` exige el embedding) y, sin
esta condición, a los 7 días se le borraría el cuerpo igual: se perdería el
único insumo del que sale el embedding, justo cuando más se lo necesita para
reintentar. Auditado contra la corrida real del 04/09: 0 filas en ese estado,
así que la condición no cambia nada de lo ya purgado — cierra un camino que
todavía no se había tomado.
"""
import logging
from datetime import timedelta
from typing import Dict

from sqlalchemy import func, update
from sqlmodel import Session, select

from ..config import settings
from ..models import Noticia
from ..tiempo import ahora_utc

logger = logging.getLogger(__name__)


def _limite_de_purga():
    """
    Fecha de publicación a partir de la cual una noticia es purgable.

    Es `max(DIAS_RETENCION_CUERPO * 24, HORAS_CLUSTER_ABIERTO)` **en horas** —
    no `max(DIAS_RETENCION_CUERPO, HORAS_CLUSTER_ABIERTO)` a secas, que
    compararía 7 contra 12 en vez de 168 contra 12 y invitaría a "simplificar"
    esto mal el día de mañana—, y no directamente `DIAS_RETENCION_CUERPO * 24`.
    La razón del `max()` es una condición de seguridad real y no una cautela
    decorativa: `HORAS_CLUSTER_ABIERTO` es configurable por el operador, y su
    propio default de `DIAS_RETENCION_CUERPO` invita a subirlo como la
    recuperación ante un problema. Si `HORAS_CLUSTER_ABIERTO` alguna vez
    quedara por encima de los 7 días de default, un límite fijo purgaría
    noticias que `agrupar_pendientes` todavía considera candidatas a cluster —
    la purga le comería el cuerpo a material que el pipeline todavía puede
    necesitar. El `max()` hace que ese error de configuración no pueda pasar
    nunca, en vez de confiar en que dos números configurados por separado se
    mantengan en el orden correcto.
    """
    horas = max(settings.DIAS_RETENCION_CUERPO * 24, settings.HORAS_CLUSTER_ABIERTO)
    return ahora_utc() - timedelta(hours=horas)


def _condicion_de_purga(limite):
    """
    La única población que esta purga toca, en un solo lugar.

    `Noticia.cluster_id.is_(None)` es la mitad que importa: sin ella se estaría
    tocando la población agrupada, que es la que necesita la comprobación de
    re-síntesis que este módulo deliberadamente no implementa. Ver el docstring
    del módulo, "El alcance es deliberadamente chico".

    `Noticia.embedding.is_not(None)` cierra el otro camino: si la vectorización
    de esa noticia falló o todavía no corrió, purgarla igual borraría el único
    insumo del que sale el embedding, justo el que hace falta para reintentar
    cuando el problema se resuelva. Ver el docstring del módulo, la nota sobre
    "tiene que haberse vectorizado".
    """
    return (
        Noticia.purgado_en.is_(None),
        Noticia.cluster_id.is_(None),
        Noticia.embedding.is_not(None),
        Noticia.fecha_publicacion < limite,
    )


def purgar_cuerpos_vencidos(session: Session, *, solo_contar: bool = False) -> Dict[str, int]:
    """
    Borra el cuerpo de las noticias huérfanas que ya vencieron su ventana.

    Idempotente por construcción: `purgado_en IS NULL` en la condición hace que
    una noticia ya purgada no vuelva a contarse ni a tocarse en la corrida
    siguiente, así que correrla de más no tiene costo.

    `solo_contar=True` mide sin escribir — ni `contenido_limpio` ni
    `purgado_en` cambian, y no hay `commit()`. Es la corrida en seco de
    `POST /purge?solo_contar=true`.

    Devuelve `evaluadas`/`bytes_evaluados` (la población candidata, medida
    antes de escribir) y `purgadas`/`bytes_liberados` (lo que de verdad se
    tocó — quedan en 0 con `solo_contar=True`). Los cuatro campos por separado
    es lo que vuelve inequívoca la respuesta de una corrida en seco: nada en el
    nombre del campo sugiere una acción que no pasó.

    `purgadas` sale del `rowcount` del `UPDATE`, no del `SELECT COUNT` de
    arriba — son la misma consulta salvo por una ventana de tiempo entre las
    dos, y una purga concurrente de una fila que este `UPDATE` también
    contaba haría que el conteo previo sobrestimara lo que esta corrida
    realmente tocó. `bytes_liberados` sí queda atado al `SELECT` de arriba: no
    hay forma de medir el largo de un texto después de haberlo puesto en
    blanco, así que en el caso (raro, un solo operador) de una purga
    concurrente sobre las mismas filas, esta cifra queda como una aproximación
    y `purgadas` como el número exacto.
    """
    limite = _limite_de_purga()
    condicion = _condicion_de_purga(limite)

    evaluadas, bytes_evaluados = session.exec(
        select(
            func.count(),
            # `octet_length` y no `length`: en Postgres, `length()` sobre
            # `text` cuenta CARACTERES, no bytes. Con acentos y eñes de sobra
            # en español, contar caracteres subestima el texto real que se
            # libera — verificado: "ñññ" da 3 con `length` y 6 con
            # `octet_length`, que es lo que de verdad ocupa en disco.
            func.coalesce(func.sum(func.octet_length(Noticia.contenido_limpio)), 0),
        ).where(*condicion)
    ).one()

    resultado = {
        "evaluadas": evaluadas,
        "bytes_evaluados": bytes_evaluados,
        "purgadas": 0,
        "bytes_liberados": 0,
    }

    if evaluadas == 0:
        logger.info("Purga de cuerpos: nada que purgar")
        return resultado

    if solo_contar:
        logger.info(
            f"Purga de cuerpos (solo contar): {evaluadas} noticias, "
            f"{bytes_evaluados} bytes"
        )
        return resultado

    resultado_update = session.exec(
        update(Noticia)
        .where(*condicion)
        .values(contenido_limpio="", purgado_en=ahora_utc())
    )
    session.commit()

    purgadas = resultado_update.rowcount
    resultado["purgadas"] = purgadas
    resultado["bytes_liberados"] = bytes_evaluados
    logger.info(f"Purga de cuerpos: {purgadas} noticias, {bytes_evaluados} bytes liberados")
    return resultado
