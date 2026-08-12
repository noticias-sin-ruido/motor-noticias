"""topicos y subtopicos como listas

`Sintesis.topico` + `Sintesis.topico_secundario` (dos strings nullable) pasan a
`Sintesis.topicos` + `Sintesis.subtopicos` (dos listas JSONB).

El campo `topico_secundario` mezclaba dos preguntas distintas: "de qué otra
categoría es esto además" (deportes Y espectáculos, un caso real y correcto) y
"qué recorte más fino tiene dentro de una categoría" (fútbol, dentro de
deportes). `topicos` cubre la primera, `subtopicos` la segunda. Ver
specs/change_logs.md, Fase 5 -- rediseño de tópicos.

Escrita a mano y no autogenerada porque hay **datos que migrar**: `topico` pasa
a ser el primer elemento de `topicos`, y si había `topico_secundario` se agrega
como el segundo. Un autogenerate habría hecho add + drop y perdido el tópico de
cada síntesis ya publicada.

Revision ID: 27e6744ee0b2
Revises: a72ec65ef1f1
Create Date: 2026-08-12 15:20:47.587341

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401  -- lo usan las migraciones autogeneradas
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '27e6744ee0b2'
down_revision: Union[str, Sequence[str], None] = 'a72ec65ef1f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `server_default` es lo que permite agregar una columna NOT NULL sobre una
    # tabla que ya tiene filas. Se saca al final: de acá en adelante el valor
    # lo pone la aplicación.
    op.add_column(
        "sintesis",
        sa.Column(
            "topicos",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "sintesis",
        sa.Column(
            "subtopicos",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )

    # El tópico ya existente pasa a ser el primer elemento de la lista.
    op.execute(
        "UPDATE sintesis SET topicos = jsonb_build_array(topico) "
        "WHERE topico IS NOT NULL"
    )
    # Si había secundario, se agrega como un segundo tópico PAR -- no como
    # subtópico: bajo el diseño viejo era una categoría de pleno derecho
    # (deportes + espectaculos), no un recorte fino de la principal.
    op.execute(
        "UPDATE sintesis SET topicos = topicos || jsonb_build_array(topico_secundario) "
        "WHERE topico_secundario IS NOT NULL"
    )
    # `subtopicos` queda en '[]' para todas las filas existentes: no hay forma
    # de reconstruir un recorte que el diseño anterior no capturaba.

    op.alter_column("sintesis", "topicos", server_default=None)
    op.alter_column("sintesis", "subtopicos", server_default=None)

    op.drop_column("sintesis", "topico")
    op.drop_column("sintesis", "topico_secundario")


def downgrade() -> None:
    op.add_column("sintesis", sa.Column("topico", sa.VARCHAR(), nullable=True))
    op.add_column(
        "sintesis", sa.Column("topico_secundario", sa.VARCHAR(), nullable=True)
    )

    # Vuelven el primer y segundo elemento de `topicos`. Cualquier tercero, y
    # todo lo que hubiera en `subtopicos`, se pierde -- es lo único que se
    # puede hacer al volver a un esquema que no los representa.
    op.execute("UPDATE sintesis SET topico = topicos->>0")
    op.execute("UPDATE sintesis SET topico_secundario = topicos->>1")

    op.create_index(op.f("ix_sintesis_topico"), "sintesis", ["topico"])
    op.create_index(
        op.f("ix_sintesis_topico_secundario"), "sintesis", ["topico_secundario"]
    )

    op.drop_column("sintesis", "subtopicos")
    op.drop_column("sintesis", "topicos")
