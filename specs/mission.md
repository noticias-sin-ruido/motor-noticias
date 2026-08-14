# 🎯 Mission — Sin Ruido

## Rol

Este archivo (junto con el resto de `specs/`) es el contexto que cualquier persona o asistente (incluido Claude) debe leer antes de tocar este repositorio. Tu rol al trabajar acá es el de ingeniero/a backend colaborando en el desarrollo de este motor: proponé, debatí y documentá decisiones de diseño antes de implementar cambios estructurales, seguí las reglas de desarrollo de este archivo, y mantené `specs/` al día — es la fuente de verdad del proyecto, no el código por sí solo.

## Qué es Sin Ruido

**Sin Ruido** es un **motor backend para ingesta, vectorización y síntesis neutra de noticias**.

### Objetivo
Agregar noticias de múltiples fuentes (RSS feeds), vectorizarlas, agruparlas por similitud semántica, y generar síntesis neutrales con comparativa de enfoques editoriales.

### Público objetivo
Usuarios que desean entender eventos noticiosos sin sesgos editoriales, viendo cómo cada medio reporta el mismo hecho.

### Qué NO hace este motor
Compara enfoques editoriales sobre **un mismo hecho** y entrega síntesis. **Si no hay hecho, no es su trabajo.** Los horóscopos, las recetas y la quiniela se clasifican y quedan fuera del agrupamiento (`services/categorias.py`): no se pierden, pero qué se hace con ellos lo resuelve el back-end del producto. Meter acá un circuito para contenido sin hecho mezclaría dos productos distintos en el mismo motor.

### Principio rector
Este proyecto prioriza **claridad sobre optimalidad prematura**. No sobre-ingenierices: preferí tres líneas parecidas antes que una abstracción prematura, y no resuelvas problemas de escala que todavía no existen (ver `tech_stack.md`, sección "Arquitectura y Escalabilidad", para los que sí están identificados y a propósito pospuestos).

---

## Reglas de desarrollo

### Código

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

### Testing

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

### BD y migraciones

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

### Git y versionado

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

### Logging y health checks

```python
import logging

logger = logging.getLogger(__name__)
logger.info(f"Noticia insertada: {noticia.id}")
logger.error(f"Error al vectorizar: {e}")
```

- `GET /` — Health check general de la API

(El viejo `GET /test-db` fue removido: era temporal y cada llamada insertaba un `Medio` de prueba que después entraba al ciclo de ingesta.)

### Seguridad

1. **No commitear `.env`** (está en `.gitignore`)
2. **CORS configurado** en FastAPI cuando exista un frontend consumiéndola
3. **Validación de entrada** automática (Pydantic)
4. **Rate limiting** — pendiente, ver `roadmap.md` Fase 5
5. **Autenticación** — pendiente si la API pasa a ser pública, ver `roadmap.md` Fase 5

---

## Checklist antes de empezar cualquier fase nueva

- [ ] Leer `specs/roadmap.md` y `specs/change_logs.md` para el estado y las decisiones ya tomadas
- [ ] Verificar que el entorno local está en orden (`python scripts/verify_setup.py`)
- [ ] Ejecutar tests existentes (`pytest`)
- [ ] Crear branch de feature
- [ ] Escribir tests para nuevas funcionalidades
- [ ] Asegurar cobertura ≥80%
- [ ] Hacer commit atómico y descriptivo
- [ ] Documentar decisiones nuevas en `change_logs.md` antes de darlas por cerradas

---

## Notas finales

- **La documentación es código**: mantené `specs/` actualizado en el mismo cambio que toca el código, no después.
- **Pregunta/debatí temprano**: si una decisión de diseño no está clara o es estructural, discutila antes de implementar (así se trabajó toda la Fase 2, ver `change_logs.md`).
- **Aprendé del historial**: `change_logs.md` documenta no solo qué se decidió sino por qué — incluidas las alternativas descartadas.
