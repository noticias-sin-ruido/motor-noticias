"""bandera de extraccion por url por medio

Agrega `Medio.extraer_por_url`: si el cuerpo de la nota se extrae de la página
del artículo en vez de leerlo del `content:encoded` del feed.

Habilita la segunda vía de ingesta (Clarín, Perfil). Es por medio y no global
porque los 6 medios del roster ya entregan el cuerpo completo por RSS y no hay
razón para mandarles un request de página por nota. Ver specs/change_logs.md,
"Backlog punto 1: segunda vía de ingesta por URL".

Escrita a mano y no autogenerada por lo mismo que `a72ec65ef1f1`: la tabla ya
tiene filas, así que la columna necesita `server_default` para poder nacer
NOT NULL. Se saca al final — de acá en adelante el valor lo pone la aplicación,
que es lo que el modelo declara con `Field(default=False)`.

Revision ID: b4f1a9d27c30
Revises: 3c175c27adde
Create Date: 2026-08-18 15:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401  -- lo usan las migraciones autogeneradas
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4f1a9d27c30"
down_revision: Union[str, Sequence[str], None] = "3c175c27adde"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "medio",
        sa.Column(
            "extraer_por_url",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("medio", "extraer_por_url", server_default=None)


def downgrade() -> None:
    op.drop_column("medio", "extraer_por_url")
