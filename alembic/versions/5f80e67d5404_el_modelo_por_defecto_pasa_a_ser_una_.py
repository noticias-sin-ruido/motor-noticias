"""el modelo por defecto pasa a ser una fila

Revision ID: 5f80e67d5404
Revises: 87371d111df7
Create Date: 2026-08-21 13:41:00.000000

"""
import json
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5f80e67d5404'
down_revision: Union[str, Sequence[str], None] = '87371d111df7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Nombre de la fila que representa lo que hasta hoy era el camino histórico.
# Es el que va a quedar en `Sintesis.modelo_usado` de acá en adelante, así que
# tiene que ser reconocible en una serie temporal dentro de un año.
NOMBRE = "gemini-por-defecto"

# **Se lee del entorno y no de `src.config`, a propósito.** Una migración tiene
# que seguir corriendo igual dentro de dos años, y estas variables se borran de
# `Settings` en este mismo commit: importar `settings` acá la dejaría rota para
# cualquiera que migre desde una versión anterior. Los defaults son una copia de
# los que tenía `Settings` al momento de escribir esto, y no se actualizan.
DEFAULTS = {
    "GEMINI_MODEL": "gemini-3.5-flash-lite",
    "GEMINI_TEMPERATURA": "0.2",
    "GEMINI_THINKING_LEVEL": "LOW",
}


def _del_entorno(nombre: str) -> str:
    """
    El valor del entorno del proceso o del `.env`, con el default de siempre.

    Mira el `.env` porque es donde está configurado en cualquier despliegue real
    y `pydantic-settings` no lo vuelca a `os.environ` — la misma trampa que ya
    documenta `services/proveedores/base._del_entorno`.
    """
    del_proceso = os.environ.get(nombre)
    if del_proceso:
        return del_proceso
    try:
        from dotenv import dotenv_values

        del_archivo = dotenv_values(".env").get(nombre)
        if del_archivo:
            return del_archivo
    except Exception:
        pass
    return DEFAULTS[nombre]


# Los niveles que acepta Gemini. **Copiados y no importados de
# `services/proveedores/gemini.py`**, por el mismo motivo que `DEFAULTS`: una
# migración tiene que seguir corriendo aunque ese módulo cambie o desaparezca.
NIVELES = ("MINIMAL", "LOW", "MEDIUM", "HIGH")


def _temperatura() -> float:
    """
    La temperatura configurada, o el default si lo que hay no es un número.

    **No aborta la migración**, y es deliberado. Antes esto era un `float()`
    pelado, así que `GEMINI_TEMPERATURA=0,2` —o cualquier tipeo— hacía fallar el
    `upgrade()` entero y dejaba el despliegue trabado a mitad. El caso no era
    rebuscado: `dotenv_values` **no pela comentarios de fin de línea sin
    comillas**, y el `.env.example` que este mismo commit borra tenía comentarios
    explicativos alrededor de esa variable.

    Caer al default es aceptable ahora y no lo hubiera sido antes: la
    configuración pasó a vivir en una fila que se mira con `GET /modelos`, así
    que un valor que no se respetó queda a la vista en vez de perderse.
    """
    crudo = _del_entorno("GEMINI_TEMPERATURA")
    try:
        return float(crudo)
    except (TypeError, ValueError):
        print(
            f"[migración {revision}] GEMINI_TEMPERATURA={crudo!r} no es un "
            f"número; se usa {DEFAULTS['GEMINI_TEMPERATURA']}. Revisalo con "
            f"GET /modelos y corregilo con la API si hace falta."
        )
        return float(DEFAULTS["GEMINI_TEMPERATURA"])


def _nivel_de_razonamiento() -> str:
    """
    El nivel configurado, o el default si no es uno de los que Gemini acepta.

    Sin esta validación la migración escribía cualquier cosa —`'NONE'`, `'OFF'`,
    `'LOW '` con un espacio al final— y la fila quedaba **rechazada por el
    propio adaptador**, pero recién en la síntesis: `validar_opciones` levanta
    `ErrorDeProveedor`, que `llamar_modelo` traduce a la rama "esto se arregla
    solo" y reintenta tres veces con espera creciente **por cada cluster**.

    O sea, la migración esquivaba la puerta que `POST /modelos` sí custodia y
    dejaba el rechazo para el camino caliente.
    """
    crudo = (_del_entorno("GEMINI_THINKING_LEVEL") or "").strip().upper()
    if crudo in NIVELES:
        return crudo
    print(
        f"[migración {revision}] GEMINI_THINKING_LEVEL={crudo!r} no es uno de "
        f"{list(NIVELES)}; se usa {DEFAULTS['GEMINI_THINKING_LEVEL']}. Revisalo "
        f"con GET /modelos y corregilo con la API si hace falta."
    )
    return DEFAULTS["GEMINI_THINKING_LEVEL"]


def upgrade() -> None:
    """
    Convierte el default implícito de Gemini en una fila explícita, **apagada**.

    **Por qué existe esta migración.** Hasta acá, una base sin filas activas
    sintetizaba por el camino histórico, que hablaba Gemini directo leyendo
    `settings.GEMINI_*`. Ese camino se borra en este commit (backlog punto 2,
    etapa 4), así que sin esta migración quien ya estuviera corriendo se queda
    sin la configuración que venía usando.

    Lo que hace es traducir esa configuración a la fila que ahora la representa.
    No inventa nada: lee las mismas variables que leía el código que se borra.

    **La fila entra apagada, y esa es la corrección de una versión anterior de
    esta misma migración.** Aquélla la activaba cuando no hubiera ninguna otra
    fila activa, razonando sobre el despliegue que actualiza. Pero la migración
    también corre en el que **nace**, y ahí esa condición se cumple siempre: una
    instalación limpia terminaba con Gemini activo antes del primer
    `POST /modelos`, o sea le mandaba los cuerpos de los artículos a Google sin
    que nadie lo hubiera elegido — exactamente lo que la etapa 4 vino a sacar,
    ahora con una fila que lo hacía parecer deliberado.

    La regla que queda es única y no depende de adivinar en qué caso estamos:
    **ninguna migración elige proveedor.** El costo asumido es que quien
    actualiza tiene que prender la fila con `PATCH /modelos/{id}?activo=true`;
    a cambio, nadie sintetiza contra un proveedor que no eligió. El motor lo
    avisa en cada corrida mientras no haya ninguno activo.

    **La credencial no se toca ni se copia acá.** La fila guarda el nombre de la
    variable, nunca su valor: la base se respalda y se dumpea. Quien migre tiene
    que definir `MODELO_API_KEY` con la key que antes estaba en `GEMINI_API_KEY`
    — es un renombre, mismo valor. Si no lo hace, el motor no sintetiza y lo
    dice; no adivina.
    """
    conexion = op.get_bind()

    # **Dos guardas, y cada una cubre un caso distinto.**
    #
    # Que ya exista el nombre: la migración se corrió antes, o alguien creó una
    # fila así a mano. Insertar de nuevo violaría el índice único.
    ya_esta = conexion.execute(
        sa.text("SELECT 1 FROM modelo_ia WHERE nombre = :n"), {"n": NOMBRE}
    ).first()
    if ya_esta:
        return

    # **INSERT explícito y no `bulk_insert`.** `adaptador` y `modo_estructura`
    # son ENUM de Postgres, que no acepta el cast implícito desde varchar: con
    # una tabla declarada al vuelo, el INSERT falla con "column is of type
    # adaptador but expression is of type character varying". Los casts van
    # escritos a mano, y las etiquetas son los **nombres** del enum de Python
    # (`GEMINI`), no sus valores (`gemini`) — verificado contra `pg_enum`.
    conexion.execute(
        sa.text(
            "INSERT INTO modelo_ia (nombre, adaptador, modelo, base_url, "
            "api_key_env, modo_estructura, temperatura, max_tokens, opciones, "
            "activo, prioridad) VALUES (:nombre, 'GEMINI'::adaptador, :modelo, "
            "NULL, 'MODELO_API_KEY', 'RESPONSE_FORMAT'::modoestructura, "
            ":temperatura, NULL, CAST(:opciones AS JSON), FALSE, 100)"
        ),
        {
            "nombre": NOMBRE,
            "modelo": _del_entorno("GEMINI_MODEL"),
            "temperatura": _temperatura(),
            # El adaptador nativo lee la palanca de razonamiento de acá. Es la
            # única de costo del pipeline, y perderla al migrar sería subir el
            # gasto en silencio.
            "opciones": json.dumps({"thinking_level": _nivel_de_razonamiento()}),
        },
    )


def downgrade() -> None:
    """
    Saca la fila, pero **solo si sigue siendo la que puso esta migración**.

    Si el operador la editó ya no es un artefacto de la migración sino una
    decisión suya, y borrarla sería tirar configuración que nadie pidió tirar.
    En ese caso se la deja y el downgrade no hace nada: una fila de más no rompe
    el esquema anterior.

    **El `WHERE` mira los campos que el operador realmente puede haber tocado.**
    Una versión anterior prometía esto mismo en el docstring pero filtraba por
    `adaptador`, `base_url` y `max_tokens`, que no discriminan nada: en el
    adaptador de Gemini `base_url` se rechaza en el constructor, así que
    **siempre** es NULL en una fila usable. Los dos casos que el texto nombraba
    —otro modelo, otra temperatura— pasaban el filtro y se borraban igual.

    `activo` entra en la lista porque prenderla es la acción que se espera de
    quien actualiza: una fila prendida ya fue adoptada.
    """
    op.execute(
        sa.text(
            "DELETE FROM modelo_ia "
            "WHERE nombre = :n "
            "  AND adaptador = 'GEMINI' "
            "  AND modelo = :modelo "
            "  AND temperatura = :temperatura "
            "  AND opciones::text = :opciones "
            "  AND base_url IS NULL "
            "  AND max_tokens IS NULL "
            "  AND activo IS FALSE"
        ).bindparams(
            n=NOMBRE,
            modelo=_del_entorno("GEMINI_MODEL"),
            temperatura=_temperatura(),
            opciones=json.dumps({"thinking_level": _nivel_de_razonamiento()}),
        )
    )
