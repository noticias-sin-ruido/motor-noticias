"""Registro de eventos del motor

Punto 19 del backlog: que enterarse de un fallo no dependa del mail.

**No siembra nada, y no hay nada que sembrar.** A diferencia de la migración de
las alertas, acá no existe un valor previo en el entorno que convertir: los
eventos anteriores a esta tabla vivían sólo en el log de Docker, que rota. Se
arranca vacía y se llena desde la próxima alerta.

Revision ID: f7b02c5a91d4
Revises: d4e81a37c209
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7b02c5a91d4"
down_revision: Union[str, Sequence[str], None] = "d4e81a37c209"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "evento",
        sa.Column("id", sa.Integer(), nullable=False),
        # **Única, y eso es el diseño y no una restricción de prolijidad.** Una
        # fila por clave con contador, en vez de una por ocurrencia: un feed
        # caído toda la noche daría 96 filas idénticas que tapan todo lo demás.
        sa.Column("clave", sa.String(length=200), nullable=False),
        sa.Column("categoria", sa.String(length=200), nullable=False),
        sa.Column("asunto", sa.String(length=200), nullable=False),
        sa.Column("mensaje", sa.String(length=2000), nullable=False),
        sa.Column("veces", sa.Integer(), nullable=False),
        sa.Column("primera_vez", sa.DateTime(), nullable=False),
        sa.Column("ultima_vez", sa.DateTime(), nullable=False),
        sa.Column("terminal", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clave"),
    )
    # `categoria` para filtrar sin un LIKE que no usa índice; `ultima_vez`
    # porque es el orden natural de la lista y el criterio de la purga.
    op.create_index("ix_evento_categoria", "evento", ["categoria"])
    op.create_index("ix_evento_ultima_vez", "evento", ["ultima_vez"])


def downgrade() -> None:
    # Se avisa antes de borrar: acá no hay red que recoja lo que se pierde. Los
    # eventos no están en ningún otro lado salvo el log de Docker, que rota.
    print(
        "  ⚠ Se borra el registro de eventos. Los avisos vuelven a existir sólo "
        "en el log del contenedor, que rota a los 50 MB."
    )
    op.drop_index("ix_evento_ultima_vez", table_name="evento")
    op.drop_index("ix_evento_categoria", table_name="evento")
    op.drop_table("evento")
