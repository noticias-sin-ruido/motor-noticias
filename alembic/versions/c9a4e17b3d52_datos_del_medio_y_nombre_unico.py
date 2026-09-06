"""datos del medio y nombre unico

Agrega `Medio.idioma`, `Medio.pais` y `Medio.logo_url`, y convierte el índice de
`Medio.nombre` en único.

Los tres campos los provee el operador al dar de alta un medio por
`POST /medios` (backlog punto 3). El sondeo del alta los **propone** leyéndolos
del canal RSS —`<language>` y `<image><url>`— pero el valor que se guarda es el
que manda el operador: esos tags son opcionales y muchos feeds los traen mal.

El índice único de `nombre` no es cosmético. `POST /medios` comprueba duplicados
con un SELECT antes de insertar, y entre ese SELECT y el INSERT hay una ventana;
el índice único es lo que hace que esa carrera salga como un 409 —vía
`IntegrityError`, ver `main.alta_medio`— en vez de como un 500. `ModeloIA.nombre`
ya lo tenía desde su propia alta; `Medio` se quedó sin él porque hasta ahora el
único que daba de alta medios era `scripts/seed_medios.py`, que deduplica a mano.

Escrita a mano y no autogenerada por lo mismo que `a72ec65ef1f1` y
`b4f1a9d27c30`: la tabla ya tiene filas, así que `idioma` necesita
`server_default` para poder nacer NOT NULL. Se saca al final — de acá en adelante
el valor lo pone la aplicación, que es lo que el modelo declara con
`Field(default="es")`.

`pais` y `logo_url` nacen nullable y ahí se quedan: hay medios sin país claro
(agencias internacionales, nativos digitales sin sede) y un default `'AR'`
mentiría sobre ellos. Los siete medios ya cargados son argentinos, pero eso lo
completa el operador o el seed, no la migración.

Revision ID: c9a4e17b3d52
Revises: 5f80e67d5404
Create Date: 2026-09-03 16:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401  -- lo usan las migraciones autogeneradas
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9a4e17b3d52"
down_revision: Union[str, Sequence[str], None] = "5f80e67d5404"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "medio",
        sa.Column(
            "idioma",
            sqlmodel.sql.sqltypes.AutoString(length=8),
            nullable=False,
            server_default="es",
        ),
    )
    op.alter_column("medio", "idioma", server_default=None)

    op.add_column(
        "medio",
        sa.Column("pais", sqlmodel.sql.sqltypes.AutoString(length=2), nullable=True),
    )
    op.add_column(
        "medio",
        sa.Column("logo_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )

    # El índice viejo no era único. Se reemplaza en vez de agregar uno al lado:
    # dos índices sobre la misma columna es trabajo doble en cada INSERT sin
    # ganancia de lectura.
    op.drop_index("ix_medio_nombre", table_name="medio")
    op.create_index("ix_medio_nombre", "medio", ["nombre"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_medio_nombre", table_name="medio")
    op.create_index("ix_medio_nombre", "medio", ["nombre"], unique=False)
    op.drop_column("medio", "logo_url")
    op.drop_column("medio", "pais")
    op.drop_column("medio", "idioma")
