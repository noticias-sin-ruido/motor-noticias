"""varios feeds rss por medio

`Medio.feed_rss` (un string) pasa a `Medio.feeds_rss` (lista JSONB).

El feed general de un diario grande es una selección de portada, no todo lo que
publica: medido sobre La Nación, el general trae 89 items cuando entre sus
secciones hay 342. Ver specs/change_logs.md.

Escrita a mano y no autogenerada porque hay **datos que migrar**: el feed que
cada medio ya tiene se convierte en una lista de un elemento antes de borrar la
columna vieja. Un autogenerate habría hecho add + drop y dejado la tabla con
todos los feeds en null.

Revision ID: a72ec65ef1f1
Revises: eb625bff05fc
Create Date: 2026-08-10 02:05:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401  -- lo usan las migraciones autogeneradas
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a72ec65ef1f1"
down_revision: Union[str, Sequence[str], None] = "eb625bff05fc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # El `server_default` es lo que permite agregar una columna NOT NULL sobre
    # una tabla que ya tiene filas. Se saca al final: de acá en adelante el
    # valor lo pone la aplicación.
    op.add_column(
        "medio",
        sa.Column(
            "feeds_rss",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )

    # El feed actual de cada medio pasa a ser el primero de su lista.
    op.execute("UPDATE medio SET feeds_rss = jsonb_build_array(feed_rss)")

    op.alter_column("medio", "feeds_rss", server_default=None)
    op.drop_column("medio", "feed_rss")


def downgrade() -> None:
    op.add_column(
        "medio",
        sa.Column("feed_rss", sa.VARCHAR(), nullable=False, server_default=""),
    )

    # Vuelve el primer feed de la lista. Los demás se pierden, que es lo único
    # que se puede hacer al volver a una columna que guarda uno solo.
    op.execute("UPDATE medio SET feed_rss = COALESCE(feeds_rss->>0, '')")

    op.alter_column("medio", "feed_rss", server_default=None)
    op.drop_column("medio", "feeds_rss")
