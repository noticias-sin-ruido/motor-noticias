"""Renombrar la marca de sintesis a noticias_al_sintetizar

Revision ID: faa5d6fc466e
Revises: 98c48e2dc7b1
Create Date: 2026-08-09 15:01:02.700971

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
# Necesarios para los tipos propios de los modelos: sqlmodel.sql.sqltypes.AutoString
# y pgvector.sqlalchemy.Vector aparecen en las migraciones autogeneradas.
import sqlmodel
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = 'faa5d6fc466e'
down_revision: Union[str, Sequence[str], None] = '98c48e2dc7b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Escrita a mano: el autogenerado ve los renombres como drop + create, que
    # acá daría igual porque la columna está entera en NULL, pero deja el
    # historial diciendo algo que no es. `alter_column` documenta la intención.
    op.alter_column(
        "cluster", "medios_al_sintetizar", new_column_name="noticias_al_sintetizar"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "cluster", "noticias_al_sintetizar", new_column_name="medios_al_sintetizar"
    )
