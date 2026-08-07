# 🧪 Guía de Pruebas — Sin Ruido Fase 1

## Verificación Rápida (sin dependencias de test)

Si no quieres instalar `pytest` aún, ejecuta el script de verificación:

```bash
python verify_setup.py
```

Esto verifica:
- ✅ Que todas las dependencias están disponibles
- ✅ Que los modelos se pueden importar e instanciar
- ✅ Que la conexión a BD (en memoria) funciona
- ✅ Que la app FastAPI se levanta sin errores

---

## Pruebas con Pytest

### 1. Instalar dependencias de desarrollo

```bash
pip install -r requirements-dev.txt
```

### 2. Ejecutar todos los tests

```bash
pytest
```

Salida esperada:
```
tests/test_db.py::TestMedio::test_crear_medio PASSED
tests/test_db.py::TestMedio::test_obtener_medio PASSED
tests/test_db.py::TestMedio::test_listar_medios PASSED
tests/test_db.py::TestNoticia::test_crear_noticia PASSED
...
======================== 15 passed in 0.42s ========================
```

### 3. Ejecutar solo tests de BD

```bash
pytest tests/test_db.py -v
```

### 4. Ejecutar solo tests de API

```bash
pytest tests/test_api.py -v
```

### 5. Ver cobertura de código

```bash
pytest --cov=src --cov-report=html
```

Genera un reporte HTML en `htmlcov/index.html`.

### 6. Ejecutar tests con salida más detallada

```bash
pytest -vv --tb=long
```

---

## Tests Incluidos

### `test_db.py` — Pruebas de Base de Datos

| Test | Descripción |
|------|---|
| `TestMedio::test_crear_medio` | Verifica creación de un Medio |
| `TestMedio::test_obtener_medio` | Verifica recuperación de un Medio por query |
| `TestMedio::test_listar_medios` | Verifica listado de todos los Medios |
| `TestNoticia::test_crear_noticia` | Verifica creación de una Noticia vinculada a un Medio |
| `TestNoticia::test_noticia_con_embedding` | Verifica que se almacena un embedding (384 dims) |
| `TestNoticia::test_url_unica` | Verifica que las URLs de Noticias son únicas |
| `TestCluster::test_crear_cluster` | Verifica creación de un Cluster |
| `TestCluster::test_cluster_con_noticias` | Verifica vinculación de Noticias a un Cluster |
| `TestSintesis::test_crear_sintesis` | Verifica creación de una Síntesis con JSONB |

### `test_api.py` — Pruebas de Endpoints

| Test | Descripción |
|------|---|
| `TestRoot::test_root_status` | Verifica que `GET /` retorna 200 con status ok |
| `TestTestDB::test_test_db_success` | Verifica que `GET /test-db` crea un Medio y retorna 200 |
| `TestTestDB::test_test_db_multiple_calls` | Verifica que se pueden hacer múltiples llamadas a `/test-db` |
| `TestAPIHealth::test_api_responds_to_requests` | Verifica salud general de la API |
| `TestAPIHealth::test_api_headers` | Verifica que los headers son correctos |

---

## ¿Qué hace cada fixture?

### `session` (en `conftest.py`)

Crea una base de datos SQLite en memoria para cada test. Es **mucho** más rápido que PostgreSQL real y aislado para que los tests no se interfieran.

```python
def test_crear_medio(self, session: Session):
    # session es una conexión a una BD en memoria nueva y limpia
    medio = Medio(...)
    session.add(medio)
    session.commit()
```

### `client` (en `conftest.py`)

Crea un cliente HTTP de prueba que inyecta la sesión en memoria en los endpoints. Permite hacer requests a la API sin levantar un servidor real.

```python
def test_root_status(self, client: TestClient):
    # client hace requests a la app FastAPI inyectando la sesión de prueba
    response = client.get("/")
    assert response.status_code == 200
```

---

## Flujo de Testing Típico

1. **Verificación rápida** (30 segundos):
   ```bash
   python verify_setup.py
   ```

2. **Instalación de dependencias de test** (primera vez):
   ```bash
   pip install -r requirements-dev.txt
   ```

3. **Correr todos los tests** (< 1 segundo):
   ```bash
   pytest
   ```

4. **Si algo falla**, debugging:
   ```bash
   pytest tests/test_db.py::TestMedio::test_crear_medio -vv
   ```

---

## Notas Importantes

- **Los tests usan SQLite en memoria**, no PostgreSQL. Esto es intencional: es más rápido y aislado.
- **Fase 2 (Ingesta de Noticias)** agregará tests de integración con RSS/trafilatura.
- **Fase 3 (Vectorización)** agregará tests de embeddings con sentence-transformers.
- Los tests de BD verifican que **las relaciones (`Relationship`) funcionan** correctamente entre modelos.
- El endpoint `/test-db` está pensado como verificación temporal; en producción podría exposer información sensible.

---

## Troubleshooting

### Error: `ModuleNotFoundError: No module named 'src'`

Asegúrate de ejecutar pytest desde la raíz del proyecto (donde está `pytest.ini`):

```bash
cd /ruta/a/sin_ruido
pytest
```

### Error: `FAILED ... IntegrityError ... UNIQUE constraint failed`

Esto es esperado en `test_url_unica` — verifica que las URLs de Noticias son únicas capturando la excepción.

### Los tests pasan pero quiero mayor cobertura

Edita `tests/test_db.py` o `tests/test_api.py` y agrega más casos (ej. borrado, actualización, filtros complejos).
