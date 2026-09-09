"""el destino de entrega pasa a ser una fila

Revision ID: a1f27c93b8e0
Revises: b963fe84825f
Create Date: 2026-09-08 11:20:00.000000

"""
import os
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op
# Aparece en las migraciones autogeneradas para los tipos propios de SQLModel.
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = 'a1f27c93b8e0'
down_revision: Union[str, Sequence[str], None] = 'b963fe84825f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# El id de la fila única. Copiado y no importado de `src/models/entrega.py`, por
# el mismo motivo que documenta la migración `5f80e67d5404`: una migración tiene
# que seguir corriendo igual dentro de dos años, y ese módulo puede cambiar.
FILA_UNICA = 1


def _webhook_url_del_entorno() -> Optional[str]:
    """
    El `WEBHOOK_URL` que hasta hoy configuraba el destino, o `None`.

    Mira el `.env` además del entorno del proceso porque es donde está
    configurado en cualquier despliegue real y `pydantic-settings` no lo vuelca a
    `os.environ` — la misma trampa que ya documentan
    `services/proveedores/base._del_entorno` y la migración `5f80e67d5404`.

    **Se siembra tal cual, sin validar.** El validador nuevo
    (`services/entrega.validar_url_de_entrega`) permite la red interna, así que
    un destino que hoy funciona lo sigue pasando; y si el `.env` trajera algo que
    no pasa, lo correcto es que la fila lo refleje y que el barrido lo rechace
    **diciéndolo en el log**, no que la migración lo borre en silencio y deje al
    operador buscando por qué dejó de entregar. Duplicar acá el validador sería
    además duplicar un control de seguridad, que es la peor forma de tenerlo.
    """
    del_proceso = os.environ.get("WEBHOOK_URL")
    if del_proceso:
        return del_proceso.strip() or None
    try:
        from dotenv import dotenv_values

        del_archivo = dotenv_values(".env").get("WEBHOOK_URL")
        if del_archivo:
            return del_archivo.strip() or None
    except Exception:
        pass
    return None


def upgrade() -> None:
    """
    Crea la tabla y le pasa el destino que hoy vive en el entorno.

    **La fila se crea siempre, aunque no haya URL.** Es lo que hace que el
    upgrade sea el único momento en que esta tabla se puebla en producción: los
    lectores encuentran la fila y no tienen que manejar su ausencia.

    A diferencia de `5f80e67d5404` —que dejó el modelo de IA **apagado** porque
    ninguna migración elige proveedor— acá el destino se siembra prendido: no es
    una elección nueva, es la que el operador ya había tomado en el `.env`, y
    apagarla cortaría una entrega que hoy funciona.
    """
    op.create_table(
        'configuracion_entrega',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('url', sqlmodel.sql.sqltypes.AutoString(length=2048), nullable=True),
        sa.Column('actualizado_en', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    url = _webhook_url_del_entorno()
    op.bulk_insert(
        sa.table(
            'configuracion_entrega',
            sa.column('id', sa.Integer),
            sa.column('url', sa.String),
            sa.column('actualizado_en', sa.DateTime),
        ),
        [{'id': FILA_UNICA, 'url': url, 'actualizado_en': None}],
    )

    # Sin URL el mensaje también sirve: dice que la entrega quedó sin destino y
    # dónde configurarlo, en vez de dejar que se descubra por su ausencia.
    print(
        f"[migración {revision}] El destino de entrega pasó del entorno a la "
        f"base ({'venía uno del .env' if url else 'no había ninguno en el .env'}). "
        f"WEBHOOK_URL ya no se lee: de acá en adelante se cambia con "
        f"PATCH /entrega o desde la pantalla de Ajustes."
    )


def downgrade() -> None:
    """
    Borra la tabla. **La URL configurada se pierde**, y hay que decirlo.

    Volver atrás significa volver a leer `WEBHOOK_URL` del entorno, así que si el
    destino se cambió por la API después de migrar, ese cambio no está en ningún
    `.env` y el downgrade lo tira. No es reversible en el sentido de los datos:
    es reversible en el del esquema.

    **Y avisa antes de borrar**, en vez de dejarlo dicho sólo acá. Este camino no
    se recorre únicamente cuando alguien decide deshacer el punto 11: basta con
    bajar varias revisiones por un problema en OTRA migración y volver a subir, y
    entonces el `upgrade` re-siembra desde el `.env` y pisa lo que el operador
    hubiera configurado por la API. Un docstring no alcanza para eso; un mensaje
    en la consola de quien corre la migración, sí.
    """
    fila = op.get_bind().execute(
        sa.text("SELECT url FROM configuracion_entrega WHERE id = :id"),
        {"id": FILA_UNICA},
    ).first()
    if fila and fila[0]:
        print(
            f"[migración {revision}] ATENCIÓN: se borra la tabla y con ella el "
            f"destino de entrega configurado. Si volvés a subir a head, se siembra "
            f"de nuevo con el WEBHOOK_URL del .env, que puede no ser el que estabas "
            f"usando. Anotá el actual antes de seguir."
        )

    op.drop_table('configuracion_entrega')
