"""publicacion_redes: copy para redes sociales

Tabla nueva, no columnas en `sintesis`: no es 1:1 con toda síntesis, solo con
el subconjunto que Gemini marca de relevancia nacional
(`AnguloGenerado.relevancia_social`). Ver specs/change_logs.md, "Copy para
redes sociales".

`publicacionredes` (sin separador) sigue la convención ya usada por
`sintesisnoticia`: SQLModel nombra la tabla como la clase en minúsculas, sin
un `__tablename__` explícito.

Se recorta a mano el `alter_column` que el autogenerate proponía sobre
`sintesis.topicos`/`subtopicos` (los quería `nullable=True`): es un drift
preexistente entre el modelo -- que no declara `nullable` en su `Column()` --
y la base real, ajena a esta migración. No se toca acá.

Revision ID: 3c175c27adde
Revises: 27e6744ee0b2
Create Date: 2026-08-12 22:37:27.395192

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
# Necesario para el tipo propio del modelo: sqlmodel.sql.sqltypes.AutoString
# aparece en las migraciones autogeneradas.
import sqlmodel  # noqa: F401
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3c175c27adde'
down_revision: Union[str, Sequence[str], None] = '27e6744ee0b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'publicacionredes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sintesis_id', sa.Integer(), nullable=False),
        sa.Column('resumen_redes', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            'hashtags',
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'),
            nullable=False,
        ),
        sa.Column('fecha_generacion', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['sintesis_id'], ['sintesis.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_publicacionredes_sintesis_id'), 'publicacionredes', ['sintesis_id'], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_publicacionredes_sintesis_id'), table_name='publicacionredes')
    op.drop_table('publicacionredes')
