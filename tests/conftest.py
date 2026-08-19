"""
Configuración y fixtures compartidas para todos los tests.
"""
from contextlib import contextmanager
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from src.database import get_session
from src.main import app
from src.models import Medio, Noticia, Cluster, Sintesis  # noqa: F401
from src.services import preprocessing


class _DocSinEntidades:
    """Lo único que `construir_evidencia` le pide a un doc de spaCy."""

    ents = ()


@pytest.fixture(autouse=True)
def spacy_mockeado():
    """
    Ningún test carga el modelo real de spaCy.

    Es la decisión que ya tomó Fase 5 al armar el CI —el job de tests no
    instala `es_core_news_md`, son cientos de MB y una descarga de red por
    corrida— pero **27 tests de síntesis se le escapaban**: llegan a spaCy sin
    quererlo, por la cadena `sintetizar_pendientes` -> `construir_evidencia`
    -> `get_nlp`. Localmente pasaban porque el modelo está instalado en el
    entorno; en CI reventaban con `OSError: [E050] Can't find model`. El CI
    estuvo rojo por esto desde el 12/08/2026, incluida la corrida de la 1.0.

    Es `autouse` y no una fixture que cada test pida a propósito: el que se
    olvide de pedirla vuelve a romper el CI y no lo nota, que es exactamente
    lo que ya pasó. Devuelve cero entidades porque estos tests mockean
    `llamar_modelo` y solo usan la evidencia para armar el prompt, no para
    asertar. Los que sí prueban NER de verdad (`test_preprocessing.py`)
    parchean `get_nlp` con su propio mapa dentro del test, y ese parche gana
    sobre este.
    """
    with patch.object(preprocessing, "get_nlp", return_value=lambda _: _DocSinEntidades()):
        yield


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
