"""
Configuración y fixtures compartidas para todos los tests.
"""
import os
from contextlib import contextmanager
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from src.config import settings
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


@pytest.fixture(autouse=True)
def api_sin_token(monkeypatch):
    """
    Ningún test hereda el `API_TOKEN` del `.env` del desarrollador.

    **Lo destapó el propio arreglo**: al definir `API_TOKEN` para cerrar el
    CSRF y la exfiltración de credencial, 63 tests de endpoints pasaron a
    fallar con 401 — no porque estuviera mal el código, sino porque el
    resultado de la suite dependía de si quien la corría tenía un token
    configurado. Eso es exactamente lo que un test no puede hacer.

    Es el mismo criterio de `test_modelos.sin_el_env_de_la_maquina`, que ya
    aislaba las credenciales, extendido a la variable que se le había escapado.

    `test_auth.py` la pisa con sus propias fixtures `con_token` y `sin_token`:
    son las que prueban la puerta en sí, así que ahí el token es el objeto del
    test y no ruido del entorno.
    """
    monkeypatch.setattr(settings, "API_TOKEN", None)


@pytest.fixture(autouse=True)
def sin_credencial_de_ia(monkeypatch, tmp_path):
    """
    Ningún test puede gastar la cuota real del proveedor de IA.

    **Lo destapó un test que salió a internet de verdad**: un parche mal puesto
    dejó a `sintetizar_cluster` resolviendo el modelo activo de la base, y el
    adaptador leyó la credencial del `.env` del desarrollador y le pegó a Gemini.
    Falló con un 404 del proveedor -- o sea que la llamada SALIÓ--, y en un
    proyecto con límite de costos duro eso no puede depender de que ningún
    parche se equivoque.

    Se cierran las dos puertas por las que entra la credencial, porque
    `_del_entorno` mira las dos: el entorno del proceso y el `.env` del
    directorio actual. El `chdir` a un temporal es el mismo truco que
    `test_modelos.sin_el_env_de_la_maquina` ya usaba en su archivo, subido acá
    para que valga en toda la suite.

    Los tests que necesitan una credencial se la ponen ellos con `monkeypatch`
    -- se ejecutan después de esta fixture, así que la pisan sin problema-- y
    los que no, fallan como "sin configurar", que es lo correcto.
    """
    monkeypatch.chdir(tmp_path)
    for variable in list(os.environ):
        if variable.startswith("MODELO_API_KEY"):
            monkeypatch.delenv(variable, raising=False)


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
