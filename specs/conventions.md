# Convenciones — Sin Ruido

Cómo se escribe código **en este stack**. Qué es el proyecto vive en
[mission.md](mission.md); cómo trabajamos —cómo se decide, cómo se verifica, cómo
se documenta— vive en las skills del método y no se repite acá.

## Código

1. **Type hints explícitos** en todas las funciones y variables

   ```python
   # ✅ Bien
   def get_medio(session: Session, medio_id: int) -> Medio:
       ...

   # ❌ Mal
   def get_medio(session, medio_id):
       ...
   ```

2. **Modularidad por capas**
   - `models/` — Esquemas de datos
   - `services/` — Lógica de negocio
   - `routers/` — Endpoints FastAPI (si se separan de `main.py` en el futuro)
   - `utils/` — Funciones auxiliares

3. **Docstrings en clases y funciones públicas**

   ```python
   def process_news(url: str) -> str:
       """Extrae y limpia contenido de una URL."""
   ```

4. **Manejo de errores explícito**

   ```python
   try:
       ...
   except SpecificError as e:
       logger.error(f"Error: {e}")
       raise HTTPException(status_code=400, detail=str(e))
   ```

5. **Sin hardcodes** — todo en `.env` o `config.py`

6. **Los comentarios explican el porqué, no el qué.** Una constante lleva al lado
   la medición que la justifica: `MAX_FEEDS = 20` no dice nada, `MAX_FEEDS = 20  #
   veinte feeds que no respondan ocupan un worker 200 s` se puede discutir.

## Testing

1. **Pytest obligatorio** para cualquier cambio de lógica

   ```bash
   pytest tests/ -v
   ```

2. **Cobertura mínima 80%**

   ```bash
   pytest --cov=src --cov-report=html
   ```

3. **Fixtures reutilizables** en `tests/conftest.py`

4. **Nombres descriptivos**

   ```python
   # ✅ Bien
   def test_crear_noticia_con_embedding():
       ...

   # ❌ Mal
   def test_noticia():
       ...
   ```

## BD y migraciones

**Alembic gestiona el esquema.** `init_db()` NO crea tablas: solo habilita la extensión `pgvector` y falla con un mensaje claro si la base no fue migrada. Cualquier cambio en los modelos requiere una migración.

```bash
# Después de tocar un modelo:
alembic revision --autogenerate -m "Descripción del cambio"
# Revisar SIEMPRE el archivo generado antes de aplicarlo
alembic upgrade head

# Otros comandos útiles:
alembic current      # en qué revisión está la base
alembic history      # historial de migraciones
alembic downgrade -1 # revertir la última
```

Notas:
- La URL de conexión sale de `src.config.settings` (o sea del `.env`), no de `alembic.ini` — ese archivo se commitea y no debe tener credenciales.
- `alembic/script.py.mako` importa `sqlmodel` y `pgvector.sqlalchemy` porque los tipos `AutoString` y `Vector` aparecen en las migraciones autogeneradas.
- **Revisar siempre el autogenerado**: Alembic no detecta bien renombres (los ve como drop + create, con pérdida de datos) ni cambios de tipo complejos.
- **Nunca dropear tablas** en producción.
- **Validar constraints** en modelos (unique, indexes).
- `alembic check` compara contra el **esquema vivo** de Postgres, no contra el árbol: no se puede deducir de que los archivos estén iguales.

## Git y versionado

1. **Commits atómicos** y descriptivos

   ```
   ✅ "Fase 2: Agregar ingesta de RSS feeds"
   ❌ "arreglo" o "cambios varios"
   ```

2. **Branches por feature**

   ```
   main
   └── develop
       ├── feature/ingesta-rss
       ├── feature/vectorization
       └── hotfix/bug-clustering
   ```

3. **No commitear**:
   - `.env` (credenciales)
   - `venv/` o `.venv/` (entorno virtual)
   - `__pycache__/`, `.pyc`
   - `htmlcov/` (reportes de coverage)

## Logging y health checks

```python
import logging

logger = logging.getLogger(__name__)
logger.info(f"Noticia insertada: {noticia.id}")
logger.error(f"Error al vectorizar: {e}")
```

- `GET /` — Health check general de la API

(El viejo `GET /test-db` fue removido: era temporal y cada llamada insertaba un `Medio` de prueba que después entraba al ciclo de ingesta.)

## Seguridad

1. **No commitear `.env`** (está en `.gitignore`)
2. **CORS configurado** en FastAPI cuando exista un frontend consumiéndola
3. **Validación de entrada** automática (Pydantic), con cotas explícitas de largo y forma en lo que entra por la API
4. **Las credenciales van al entorno, nunca a la base**
5. **Rate limiting** — pendiente, ver `roadmap.md` Fase 5
6. **Autenticación** — `API_TOKEN` opcional; obligatoria si la API pasa a ser pública, ver `roadmap.md` Fase 5

## Antes de empezar una fase nueva

- [ ] Verificar que el entorno local está en orden (`python scripts/verify_setup.py`)
- [ ] Ejecutar los tests existentes (`pytest`)
- [ ] Crear branch de feature
- [ ] Asegurar cobertura ≥80%
