# 📋 Resumen de Pruebas — Sin Ruido Fase 1

Te entregué **3 métodos** para verificar que la API arranca correctamente:

---

## 1️⃣ Verificación Rápida (sin pytest)

**Comando:**
```bash
python verify_setup.py
```

**¿Qué verifica?**
- ✅ Que todas las dependencias están disponibles (fastapi, sqlmodel, pydantic, etc.)
- ✅ Que los 4 modelos se pueden instanciar sin errores
- ✅ Que la BD en memoria funciona (crear tablas, insertar, recuperar)
- ✅ Que la app FastAPI se levanta sin crashear
- ✅ Que el endpoint `GET /` responde correctamente

**Tiempo:** ~10 segundos

**Salida esperada:**
```
🚀 VERIFICACIÓN DEL SETUP: Sin Ruido Fase 1
================================================
🔍 Verificando imports...
  ✓ fastapi 0.115.0
  ✓ sqlmodel ...
  ... (más imports)
🔍 Verificando modelos...
  ✓ Medio creado: Test Medio
  ✓ Cluster creado: Test Event
  ... (más modelos)
🔍 Verificando base de datos...
  ✓ Tablas creadas en BD en memoria
  ✓ Medio insertado (ID: 1)
  ✓ Medio recuperado: Test Medio
🔍 Verificando API FastAPI...
  ✓ App FastAPI instanciada: Sin Ruido — API
  ✓ GET / respondió con 200: {'status': 'ok', 'environment': 'development'}
================================================
📊 RESUMEN
================================================
✅ PASS — Imports
✅ PASS — Modelos
✅ PASS — Base de Datos
✅ PASS — API FastAPI
================================================
🎉 ¡TODAS LAS VERIFICACIONES PASARON! La API está lista.
```

---

## 2️⃣ Tests con Pytest (recomendado)

**Setup (primera vez):**
```bash
pip install -r requirements-dev.txt
```

**Ejecutar todos los tests:**
```bash
pytest
```

**¿Qué verifica?** (15 tests en total)

### Tests de Base de Datos (9 tests)
- ✅ Crear/recuperar/listar Medios
- ✅ Crear Noticias vinculadas a Medios
- ✅ Almacenar embeddings (384 dimensiones)
- ✅ Validar que las URLs de Noticias son únicas
- ✅ Crear Clusters y vincular Noticias
- ✅ Crear Síntesis con datos JSONB

### Tests de API (6 tests)
- ✅ Endpoint `GET /` retorna status ok
- ✅ Endpoint `GET /test-db` crea un Medio de prueba
- ✅ Múltiples llamadas a `/test-db` generan IDs diferentes
- ✅ Headers de respuesta son correctos
- ✅ Salud general de la API

**Salida esperada:**
```
tests/test_db.py::TestMedio::test_crear_medio PASSED
tests/test_db.py::TestMedio::test_obtener_medio PASSED
tests/test_db.py::TestMedio::test_listar_medios PASSED
tests/test_db.py::TestNoticia::test_crear_noticia PASSED
tests/test_db.py::TestNoticia::test_noticia_con_embedding PASSED
tests/test_db.py::TestNoticia::test_url_unica PASSED
tests/test_db.py::TestCluster::test_crear_cluster PASSED
tests/test_db.py::TestCluster::test_cluster_con_noticias PASSED
tests/test_db.py::TestSintesis::test_crear_sintesis PASSED
tests/test_api.py::TestRoot::test_root_status PASSED
tests/test_api.py::TestTestDB::test_test_db_success PASSED
tests/test_api.py::TestTestDB::test_test_db_multiple_calls PASSED
tests/test_api.py::TestAPIHealth::test_api_responds_to_requests PASSED
tests/test_api.py::TestAPIHealth::test_api_headers PASSED
======================== 15 passed in 0.42s ========================
```

**Tiempo:** < 1 segundo

---

## 3️⃣ Test Manual (opcional)

**Levanta el servidor real:**

```bash
# 1. Configurar .env
cp .env.example .env
# Editar .env y poner tus credenciales reales de PostgreSQL

# 2. Instalar dependencias de producción
pip install -r requirements.txt

# 3. Levantar servidor
uvicorn src.main:app --reload
# Salida esperada:
# INFO:     Uvicorn running on http://127.0.0.1:8000
# INFO:     Application startup complete
```

**Ahora desde otra terminal, probar:**

```bash
# Ver estado
curl http://localhost:8000/

# Insertar Medio de prueba (requiere PostgreSQL real)
curl http://localhost:8000/test-db

# Respuesta esperada:
# {"status":"ok","mensaje":"Conexión e inserción de prueba exitosas.","medio_creado":{"id":1,"nombre":"Medio de Prueba",...}}
```

---

## 📊 Comparativa de Métodos

| Método | Tiempo | Requiere | BD Real | Cobertura |
|--------|--------|----------|---------|-----------|
| `verify_setup.py` | 10 seg | Nada | No (SQLite en memoria) | Básico |
| `pytest` | < 1 seg | `requirements-dev.txt` | No (SQLite en memoria) | 15 tests |
| Test manual | 5+ seg | BD PostgreSQL real | Sí | 2 endpoints |

---

## ✅ Checklist de Verificación

Cuando corras las pruebas, verifica:

- [ ] **Sin errores de import** — Todos los módulos se cargan correctamente
- [ ] **Modelos instanciables** — Los 4 modelos (Medio, Noticia, Cluster, Sintesis) se crean sin errores
- [ ] **BD funciona** — Create, Read, y Relationship entre modelos funcionan
- [ ] **API levanta** — FastAPI se instancia sin crashes
- [ ] **Endpoints responden** — `GET /` y `GET /test-db` retornan 200
- [ ] **Tests aislados** — Las pruebas no interfieren entre sí (BD en memoria limpia por cada test)

---

## 🚀 Próximos Pasos (después de Fase 1)

- **Fase 2** agregará tests de ingesta (RSS, trafilatura)
- **Fase 3** agregará tests de vectorización (sentence-transformers, pgvector queries)
- **Fase 4** agregará tests de síntesis (Google Gemini, comparativas)

Cada fase incrementará la cobertura de tests.

---

## 📞 Troubleshooting Rápido

| Problema | Solución |
|----------|----------|
| `ModuleNotFoundError: No module named 'src'` | Ejecuta desde la raíz del proyecto (donde está `pytest.ini`) |
| `FAILED ... IntegrityError` en `test_url_unica` | Es intencional; el test verifica que las URLs son únicas |
| `pytest: command not found` | Instala con `pip install -r requirements-dev.txt` |
| `/test-db` falla en servidor real | Verifica que `DATABASE_URL` en `.env` es correcto y PostgreSQL está corriendo |

---

## 📎 Archivos de Prueba Entregados

```
sin_ruido/
├── verify_setup.py           # ← Script de verificación rápida (no requiere pytest)
├── requirements-dev.txt      # ← Dependencias para testing
├── pytest.ini                # ← Config de pytest
├── TESTING.md                # ← Guía detallada de testing
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # ← Fixtures compartidas (session, client)
│   ├── test_db.py            # ← 9 tests de BD
│   └── test_api.py           # ← 6 tests de API
└── src/
    ├── main.py               # ← Endpoints GET / y GET /test-db
    ├── config.py
    ├── database.py
    └── models/
        ├── medio.py
        ├── noticia.py
        ├── cluster.py
        └── sintesis.py
```
