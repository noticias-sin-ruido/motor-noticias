"""purgado_en en noticia

Agrega `Noticia.purgado_en`, nullable e indexada: cuándo se purgó el cuerpo de
una noticia, o `None` si todavía lo tiene. Backlog punto 8.

`contenido_limpio` sigue `NOT NULL` — una purgada guarda `''`, no `NULL` — así
que esta columna es la que distingue "se purgó" de "nunca tuvo cuerpo" (que hoy
no pasa: la ingesta descarta antes de insertar cualquier nota sin cuerpo). Sin
la marca, todas las filas vacías se verían iguales y la retención de texto de
terceros dejaría de ser medible.

Nace en `None` para toda la tabla, que es lo correcto: ninguna fila existente
está purgada. Indexada porque la usa la condición de `services/purga.py`
(`purgado_en IS NULL`), aunque con una fracción grande de la tabla en `NULL`
es esperable que Postgres prefiera un seq scan de todos modos para ese filtro
puntual — el índice le sirve más a `fecha_publicacion < límite` combinado con
esta columna que a un `IS NULL` aislado.

Revision ID: 5a246beb14bd
Revises: c9a4e17b3d52
Create Date: 2026-09-04 14:50:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a246beb14bd"
down_revision: Union[str, Sequence[str], None] = "c9a4e17b3d52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "noticia",
        sa.Column("purgado_en", sa.DateTime(), nullable=True),
    )
    op.create_index(
        op.f("ix_noticia_purgado_en"), "noticia", ["purgado_en"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_noticia_purgado_en"), table_name="noticia")
    op.drop_column("noticia", "purgado_en")
