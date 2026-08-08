"""
Entorno de Alembic para Sin Ruido.

Dos ajustes sobre la plantilla que genera `alembic init`:

  1. La URL de conexión se toma de `src.config.settings` (o sea, del `.env`)
     en vez de `alembic.ini`. `alembic.ini` se commitea, así que no debe
     contener credenciales -- ver specs/mission.md, "No commitear .env".

  2. `target_metadata` apunta a `SQLModel.metadata` para que `--autogenerate`
     detecte los cambios de los modelos.
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text
from sqlmodel import SQLModel

from alembic import context

# Importar los modelos registra todas las tablas en SQLModel.metadata.
from src import models  # noqa: F401
from src.config import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def get_url() -> str:
    """URL de conexión desde la configuración de la app (no desde alembic.ini)."""
    if not settings.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL no está configurada. Definila en el archivo .env "
            "o como variable de entorno (ver .env.example)."
        )
    return settings.DATABASE_URL


def run_migrations_offline() -> None:
    """Genera el SQL de las migraciones sin conectarse a la base."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Aplica las migraciones conectándose a la base."""
    seccion = config.get_section(config.config_ini_section, {})
    seccion["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        seccion,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # pgvector tiene que existir antes de crear cualquier columna Vector.
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        connection.commit()

        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
