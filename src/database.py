import logging
from typing import Optional

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from .config import settings

logger = logging.getLogger(__name__)

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
        # **`echo` va en False siempre, y no es un descuido.** El SQL se
        # enciende con `LOG_SQL`, que sube el logger `sqlalchemy.engine` a INFO
        # (ver `logging_config.py`). Da exactamente el mismo detalle, pero con
        # el formato y el destino del resto del motor.
        #
        # Volver a poner `echo=True` acá rompe el log: con un echo verdadero
        # SQLAlchemy le cuelga un StreamHandler propio al logger de la Engine y
        # **no le apaga la propagación**, así que cada sentencia saldría dos
        # veces — una con su formato y otra con el nuestro. Con `echo=False`
        # devuelve un Logger pelado y el nivel lo decide la jerarquía, que es lo
        # que `LOG_SQL` maneja.
        _engine = create_engine(
            settings.DATABASE_URL,
            echo=False,
            pool_pre_ping=True,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_timeout=settings.DB_POOL_TIMEOUT,
            pool_recycle=settings.DB_POOL_RECYCLE,
        )

    return _engine


def init_db() -> None:
    """
    Prepara la base de datos para que la aplicación pueda arrancar:
      1. Habilita la extensión `pgvector` si aún no existe.
      2. Verifica que el esquema haya sido migrado con Alembic.

    Las tablas **no** se crean acá. El esquema lo gestiona Alembic
    (`alembic upgrade head`): si se usara `SQLModel.metadata.create_all()`,
    las tablas nuevas se crearían por fuera del control de Alembic y los
    cambios sobre tablas existentes (ALTER) nunca se aplicarían, quedando
    el esquema real y las migraciones en estados distintos.
    """
    # Se importa acá adentro (y no al principio del archivo) para evitar
    # importaciones circulares entre database.py y los modelos.
    from . import models  # noqa: F401

    engine = get_engine()

    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))

    if not inspect(engine).has_table("alembic_version"):
        raise RuntimeError(
            "La base de datos no tiene el esquema aplicado. Ejecutá:\n"
            "    alembic upgrade head"
        )


def get_session():
    """Generador de sesiones de base de datos, usado como dependencia en los endpoints de FastAPI."""
    with Session(get_engine()) as session:
        yield session


def verificar_conexion(session: Session) -> bool:
    """
    Confirma que la sesión puede hablar con la base. La usa `GET /` -- antes
    ese endpoint solo confirmaba que Uvicorn respondía, así que un Postgres
    caído o un pool agotado no se notaba hasta que fallaba el primer endpoint
    que sí toca la base.
    """
    try:
        session.exec(text("SELECT 1"))
        return True
    except Exception as error:
        logger.error(f"Health check: no se pudo consultar la base: {error}")
        # **El rollback no es prolijidad: sin él la sesión queda envenenada.**
        # SQLAlchemy marca la sesión para rollback cuando una sentencia falla, y
        # a partir de ahí *cualquier* consulta siguiente sobre esa misma sesión
        # levanta `PendingRollbackError` en vez de intentar nada. O sea que este
        # `return False` le dejaba al llamador una sesión que parecía usable y no
        # lo era.
        #
        # No es teórico: `GET /` empezó a consultar la base después de llamar
        # acá, y con Postgres apagado devolvía **500 en vez de 503** — reventaba
        # en la consulta siguiente antes de llegar a mirar este booleano.
        # Verificado parando `sin_ruido_db` contra el motor real: 500 las tres
        # veces. El 503 es contrato: la cabina lo usa para distinguir "la base no
        # está" de "el motor no está" (`app/src-tauri/src/motor.rs`).
        #
        # Va en su propio `try` porque si la conexión se cortó, el rollback
        # también puede fallar, y esta función no puede levantar nunca: es la que
        # el healthcheck usa para decidir.
        try:
            session.rollback()
        except Exception:
            pass
        return False
