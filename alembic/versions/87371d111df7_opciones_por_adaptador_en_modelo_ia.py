"""opciones por adaptador en modelo_ia

Revision ID: 87371d111df7
Revises: 0ddec0462447
Create Date: 2026-08-21 12:44:13.076973

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
# Necesarios para los tipos propios de los modelos: sqlmodel.sql.sqltypes.AutoString
# y pgvector.sqlalchemy.Vector aparecen en las migraciones autogeneradas.
import sqlmodel
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = '87371d111df7'
down_revision: Union[str, Sequence[str], None] = '0ddec0462447'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Agrega `modelo_ia.opciones`.

    **No nula y con default `{}`**, en vez del nullable que autogeneró Alembic.
    "Sin opciones" y "diccionario vacío" son el mismo estado, y dejar que NULL
    lo represente obliga a cada lector a acordarse del `or {}`. El
    `server_default` además rellena las filas que ya existen en la instancia que
    corre hoy, así que la migración no las deja en un estado que el modelo no
    declara.
    """
    op.add_column(
        'modelo_ia',
        sa.Column(
            'opciones', sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('modelo_ia', 'opciones')
