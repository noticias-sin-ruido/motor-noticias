"""Sintesis por angulo: titulo, noticias que la respaldan y estado de entrega

Revision ID: 979689aeb928
Revises: 5d06f53f689a
Create Date: 2026-08-08 20:23:10.829714

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
# Necesarios para los tipos propios de los modelos: sqlmodel.sql.sqltypes.AutoString
# y pgvector.sqlalchemy.Vector aparecen en las migraciones autogeneradas.
import sqlmodel
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = '979689aeb928'
down_revision: Union[str, Sequence[str], None] = '5d06f53f689a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('sintesisnoticia',
    sa.Column('sintesis_id', sa.Integer(), nullable=False),
    sa.Column('noticia_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['noticia_id'], ['noticia.id'], ),
    sa.ForeignKeyConstraint(['sintesis_id'], ['sintesis.id'], ),
    sa.PrimaryKeyConstraint('sintesis_id', 'noticia_id')
    )

    # Las tres columnas NOT NULL se agregan con `server_default` y después se le
    # saca el default. El autogenerado no lo hacía, y así falla contra cualquier
    # base que ya tenga síntesis: PostgreSQL no puede completar una columna NOT
    # NULL sin decirle con qué. Al momento de escribir esto la tabla está vacía,
    # pero la migración tiene que valer para cualquier entorno.
    # El default se quita después para que el esquema quede igual al modelo, que
    # define esos valores por defecto en Python y no en la base.
    op.add_column('sintesis', sa.Column('titulo_angulo', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''))
    op.add_column('sintesis', sa.Column('enviado_backend', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('sintesis', sa.Column('fecha_envio', sa.DateTime(), nullable=True))
    op.add_column('sintesis', sa.Column('intentos_envio', sa.Integer(), nullable=False, server_default='0'))

    op.alter_column('sintesis', 'titulo_angulo', server_default=None)
    op.alter_column('sintesis', 'enviado_backend', server_default=None)
    op.alter_column('sintesis', 'intentos_envio', server_default=None)

    op.create_index(op.f('ix_sintesis_enviado_backend'), 'sintesis', ['enviado_backend'], unique=False)
    op.create_index(op.f('ix_sintesis_titulo_angulo'), 'sintesis', ['titulo_angulo'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_sintesis_titulo_angulo'), table_name='sintesis')
    op.drop_index(op.f('ix_sintesis_enviado_backend'), table_name='sintesis')
    op.drop_column('sintesis', 'intentos_envio')
    op.drop_column('sintesis', 'fecha_envio')
    op.drop_column('sintesis', 'enviado_backend')
    op.drop_column('sintesis', 'titulo_angulo')
    op.drop_table('sintesisnoticia')
