"""
Configuración y fixtures compartidas para todos los tests.
"""
import os
from typing import Generator

import pytest
from fastapi.testclient import TestClient
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
