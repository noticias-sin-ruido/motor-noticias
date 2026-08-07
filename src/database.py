from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

_engine: Optional[Engine] = None


def get_engine() -> Engine:
    """
    Devuelve el engine de SQLAlchemy, creándolo la primera vez que se necesita.
    Se crea de forma perezosa (y no al importar el módulo) para que `DATABASE_URL`
    solo sea obligatoria cuando efectivamente se intenta usar la base de datos
    (por ejemplo, en los tests se reemplaza `get_session` por una sesión SQLite
    y nunca se llega a necesitar este engine).
    """
    global _engine

    if _engine is None:
        if not settings.DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL no está configurada. Definila en el archivo .env "
                "o como variable de entorno (ver .env.example)."
            )

        # El driver psycopg (v3) debe indicarse explícitamente en la URL de conexión, ej.:
        #   postgresql+psycopg://usuario:password@localhost:5432/sin_ruido
        _engine = create_engine(
            settings.DATABASE_URL,
            echo=settings.ENVIRONMENT == "development",
            pool_pre_ping=True,
        )

    return _engine


def init_db() -> None:
    """
    Inicializa la base de datos:
      1. Habilita la extensión `pgvector` si aún no existe.
      2. Crea todas las tablas definidas en los modelos SQLModel (si no existen).
    """
    # Se importa acá adentro (y no al principio del archivo) para evitar
    # importaciones circulares entre database.py y los modelos, y para
    # asegurar que todos los modelos queden registrados en SQLModel.metadata
    # antes de crear las tablas.
    from . import models  # noqa: F401

    engine = get_engine()

    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))

    SQLModel.metadata.create_all(engine)


def get_session():
    """Generador de sesiones de base de datos, usado como dependencia en los endpoints de FastAPI."""
    with Session(get_engine()) as session:
        yield session
