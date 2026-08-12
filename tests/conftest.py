"""
Configuración y fixtures compartidas para todos los tests.
"""
import os
from contextlib import contextmanager
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from src.config import settings
from src.database import get_session
from src.main import app
from src.models import Medio, Noticia, Cluster, Sintesis  # noqa: F401


@pytest.fixture(name="session")
def session_fixture() -> Generator[Session, None, None]:
    """
    Crea una base de datos SQLite en memoria para las pruebas.
    Esto es mucho más rápido que conectarse a PostgreSQL real.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session) -> TestClient:
    """
    Crea un cliente FastAPI que inyecta la sesión de prueba en los endpoints.
    """

    def get_session_override() -> Session:
        return session

    app.dependency_overrides[get_session] = get_session_override

    client = TestClient(app)
    yield client

    app.dependency_overrides.clear()


@contextmanager
def contar_queries(session: Session):
    """
    Cuenta las sentencias SQL ejecutadas dentro del bloque `with`. La usan los
    tests que fijan un techo a los fixes de N+1 de Fase 5: el número de
    queries no debe crecer con la cantidad de filas, solo quedar en una
    constante chica. Ver specs/change_logs.md, Fase 5.
    """
    engine = session.get_bind()
    contador = {"n": 0}

    def _contar(conn, cursor, statement, parameters, context, executemany):
        contador["n"] += 1

    event.listen(engine, "before_cursor_execute", _contar)
    try:
        yield contador
    finally:
        event.remove(engine, "before_cursor_execute", _contar)
