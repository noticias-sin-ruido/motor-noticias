"""Los destinos de alerta pasan a ser una fila

Punto 9 del backlog: la casilla a la que el motor avisa la elige el operador,
no el `.env`.

**Siembra desde `ALERT_EMAIL_TO` para que un despliegue existente no se quede
sin avisos al actualizar.** Es la misma decisión que tomó la migración de la
entrega: convertir la configuración de entorno en una fila, sin pedirle nada a
nadie. Quien tenía la variable puesta sigue recibiendo en la misma casilla.

**Y las credenciales SMTP no se tocan.** `SMTP_HOST`, `SMTP_USER` y
`SMTP_PASSWORD` se quedan en el entorno. La distinción es la misma que con la
entrega: el destino es *a quién se le avisa* y la credencial es *con qué cuenta
se manda*; la base se respalda y se lee desde endpoints, una credencial adentro
de una fila se filtra sola.

Revision ID: d4e81a37c209
Revises: a1f27c93b8e0
"""

import os
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e81a37c209"
down_revision: Union[str, Sequence[str], None] = "a1f27c93b8e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _destino_del_entorno() -> Optional[str]:
    """
    `ALERT_EMAIL_TO` tal como está, sin validar.

    **Se siembra verbatim a propósito.** Si la dirección tuviera algo raro,
    validarla acá dejaría la fila vacía y el despliegue sin avisos justo al
    actualizar — o sea, la migración apagaría las alertas sin decirlo. Lo que
    hay hoy se conserva; corregirlo es trabajo de `PATCH /alertas`, que sí
    valida y sí puede explicar el error.
    """
    valor = (os.getenv("ALERT_EMAIL_TO") or "").strip()
    return valor or None


def upgrade() -> None:
    op.create_table(
        "configuracionalertas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("destinos", sa.JSON(), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(), nullable=True),
        sa.Column("ultima_prueba_ok", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    destino = _destino_del_entorno()
    # La fila se crea siempre, con o sin destino: que exista simplifica a todos
    # los lectores, y `services/alertas.configuracion` la crearía igual al vuelo.
    # **El parámetro va tipado como JSON, no como texto.** Sin `type_=sa.JSON`,
    # psycopg lo manda como VARCHAR y Postgres rechaza el INSERT contra una
    # columna `json` ("column is of type json but expression is of type character
    # varying"). Con la app arrancando por `alembic upgrade head && uvicorn`, eso
    # deja el contenedor en bucle de reinicio -- comprobado.
    op.execute(
        sa.text(
            "INSERT INTO configuracionalertas (id, destinos, actualizado_en, "
            "ultima_prueba_ok) VALUES (1, :destinos, NULL, NULL)"
        ).bindparams(
            sa.bindparam("destinos", [destino] if destino else [], type_=sa.JSON)
        )
    )
    if destino:
        print(f"  → destino de alerta sembrado desde ALERT_EMAIL_TO: {destino}")
    else:
        print("  → ALERT_EMAIL_TO no estaba definida: la fila queda sin destinos")


def downgrade() -> None:
    # Se avisa antes de borrar: bajar esto **apaga los avisos por mail** salvo
    # que `ALERT_EMAIL_TO` siga en el entorno, porque `alerts._destinos` cae a
    # esa variable cuando no puede leer la fila.
    print(
        "  ⚠ Se borra la configuración de alertas. El motor vuelve a depender de "
        "ALERT_EMAIL_TO: si no está definida, deja de avisar por mail."
    )
    op.drop_table("configuracionalertas")
