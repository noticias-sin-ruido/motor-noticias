# 🚀 Quick Start — Pruebas Fase 1

## ⚡ Opción 1: Verificación Rápida (10 segundos)

**SIN necesidad de pytest. Solo Python.**

```bash
# Descomprime el zip
unzip sin_ruido_fase1_complete.zip
cd sin_ruido

# 1. Crear el entorno virtual
python -m venv venv

# 2. Activar el entorno
# En Windows:
venv\Scripts\activate

# En macOS/Linux:
source venv/bin/activate

# 3. Instalar dependencias (dentro del venv activado)
pip install -r requirements.txt

# 4. Correr la verificación
python verify_setup.py

**Resultado esperado:**
```
🚀 VERIFICACIÓN DEL SETUP: Sin Ruido Fase 1
================================================
🔍 Verificando imports...
  ✓ fastapi 0.115.0
  ✓ sqlmodel ...
  ...
🎉 ¡TODAS LAS VERIFICACIONES PASARON! La API está lista.
```

Si ves `✅ PASS` en todas las 4 secciones → **la API está lista para Fase 2.**

---

## 🧪 Opción 2: Tests Completos con Pytest (< 1 segundo)

**Recomendado para CI/CD y desarrollo continuo.**

```bash
# 1. Descomprime
unzip sin_ruido_fase1_complete.zip
cd sin_ruido

# 2. Instala dependencias de testing (una sola vez)
pip install -r requirements-dev.txt

# 3. Corre todos los tests (15 tests, < 1 segundo)
pytest

# O solo tests de BD
pytest tests/test_db.py -v

# O solo tests de API
pytest tests/test_api.py -v

# O con cobertura
pytest --cov=src --cov-report=html
```

**Resultado esperado:**
```
======================== 15 passed in 0.42s ========================
```

Si ves `passed` en todo → **la API está lista para Fase 2.**

---

## 🌐 Opción 3: Test Manual en Servidor Real (5+ segundos)

**Requiere PostgreSQL real. Para end-to-end testing.**

```bash
# 1. Descomprime
unzip sin_ruido_fase1_complete.zip
cd sin_ruido

# 2. Configura .env con tus credenciales reales
cp .env.example .env
# Edita .env y completa:
#   DATABASE_URL=postgresql+psycopg://usuario:password@localhost:5432/sin_ruido
#   GEMINI_API_KEY=tu_clave_aqui

# 3. Instala dependencias
pip install -r requirements.txt

# 4. Levanta el servidor
uvicorn src.main:app --reload

# Salida esperada:
# INFO:     Uvicorn running on http://127.0.0.1:8000
# INFO:     Application startup complete
```

**Desde otra terminal, prueba:**

```bash
# Test 1: Estado de la API
curl http://localhost:8000/

# Test 2: Insertar Medio (requiere BD PostgreSQL real)
curl http://localhost:8000/test-db

# Respuesta esperada:
# {"status":"ok","mensaje":"Conexión e inserción...","medio_creado":{...}}
```

Si ambos endpoints responden con `status: ok` → **la API está lista para Fase 2.**

---

## ✅ Checklist Rápido

Después de correr las pruebas, verifica:

- [ ] **Opción 1 (`verify_setup.py`)**: Todos los `✅ PASS`
- [ ] **Opción 2 (`pytest`)**: `15 passed`
- [ ] **Opción 3 (servidor real)**: `status: ok` en ambos endpoints

Si al menos una opción funciona → **¡Fase 1 completada exitosamente!**

---

## 🗂️ Estructura Entregada

```
sin_ruido/
├── verify_setup.py              ← Corre esto primero (Opción 1)
├── pytest.ini                   ← Config para pytest
├── requirements.txt             ← Dependencias de producción
├── requirements-dev.txt         ← + pytest, coverage, etc.
├── .env.example                 ← Plantilla de configuración
├── Dockerfile                   ← Para desplegar
├── .dockerignore
│
├── PRUEBAS_RESUMEN.md          ← Este archivo (resumen)
├── QUICK_START.md              ← Este archivo (quick start)
├── TESTING.md                  ← Guía detallada
│
├── src/
│   ├── main.py                 ← Endpoints GET / y GET /test-db
│   ├── config.py               ← Settings desde .env
│   ├── database.py             ← Engine, init_db(), get_session()
│   └── models/
│       ├── medio.py
│       ├── noticia.py
│       ├── cluster.py
│       └── sintesis.py
│
└── tests/
    ├── conftest.py             ← Fixtures (session, client)
    ├── test_db.py              ← 9 tests de BD
    └── test_api.py             ← 6 tests de API
```

---

## 💡 Recomendación

1. **Primera ejecución**: Corre `python verify_setup.py` (rápido, sin dependencias extra)
2. **Para desarrollo**: Instala `requirements-dev.txt` y usa `pytest`
3. **Para QA/producción**: Test manual en servidor real con PostgreSQL

---

## 🐛 Troubleshooting en 30 segundos

| Error | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'src'` | Ejecuta desde la raíz (`cd sin_ruido` primero) |
| `pytest: command not found` | Corre `pip install pytest` o `pip install -r requirements-dev.txt` |
| Timeout en `/test-db` | Verifica que PostgreSQL está corriendo y `DATABASE_URL` es correcto |
| `FAILED ... IntegrityError` en `test_url_unica` | Es intencional (valida URLs únicas) |

---

## 📞 ¿Qué sigue después?

Una vez que todas las pruebas pasen:

- **Fase 2**: Ingesta de noticias (RSS, trafilatura, newspaper4k)
- **Fase 3**: Vectorización (sentence-transformers, almacenamiento en pgvector)
- **Fase 4**: Síntesis neutra (Google Gemini, comparativas de enfoques)

Cada fase tendrá sus propias pruebas.
