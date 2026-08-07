# 📚 CLAUDE.md — Guía de Contexto y Desarrollo para Sin Ruido

Este archivo documenta el contexto completo del proyecto, decisiones técnicas, arquitectura y buenas prácticas para que Claude (y futuros desarrolladores) puedan continuar el desarrollo de forma coherente.

---

## 🎯 Visión General del Proyecto

**Sin Ruido** es un **motor backend para ingesta, vectorización y síntesis neutra de noticias**.

### Objetivo
Agregar noticias de múltiples fuentes (RSS feeds), vectorizarlas, agruparlas por similitud semántica, y generar síntesis neutrales con comparativa de enfoques editoriales.

### Público Objetivo
Usuarios que desean entender eventos noticiosos sin sesgos editoriales, viendo cómo cada medio reporta el mismo hecho.

### Stack Tecnológico

| Componente | Tecnología | Versión |
|---|---|---|
| **Lenguaje** | Python | 3.12 |
| **Framework Web** | FastAPI | ≥0.115.0 |
| **Servidor** | Uvicorn | ≥0.31.0 |
| **ORM** | SQLModel (SQLAlchemy 2.0 + Pydantic v2) | ≥0.0.16 |
| **Base de Datos** | PostgreSQL | 16+ |
| **Vectores** | pgvector | ≥0.2.5 |
| **Embeddings** | sentence-transformers | ≥2.6.0 |
| **NLP** | spacy | ≥3.8.0 |
| **Ingesta** | feedparser, beautifulsoup4 (trafilatura/newspaper4k reservados) | Últimas versiones |
| **Testing** | pytest | ≥7.4.0 |
| **Containerización** | Docker + docker-compose | Latest |
| **Control de versiones** | Git | Latest |

---

## 🏗️ Arquitectura del Proyecto

### Estructura de Directorios

```
sin_ruido/
├── src/
│   ├── __init__.py
│   ├── main.py                 # Aplicación FastAPI, endpoints, lifespan
│   ├── config.py               # Settings desde .env (pydantic-settings)
│   ├── database.py             # Engine, init_db(), get_session()
│   ├── models/
│   │   ├── __init__.py         # Imports centralizados de modelos
│   │   ├── medio.py            # Modelo: Medio (fuente de noticias)
│   │   ├── noticia.py          # Modelo: Noticia (con embedding Vector)
│   │   ├── cluster.py          # Modelo: Cluster (agrupación por evento)
│   │   └── sintesis.py         # Modelo: Sintesis (resumen neutral + JSONB)
│   └── services/
│       ├── __init__.py
│       └── ingestion.py        # Pipeline de ingesta RSS (Fase 2)
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # Fixtures pytest (session, client)
│   ├── test_db.py              # 9 tests de BD y modelos
│   ├── test_api.py             # 7 tests de endpoints (incluye POST /ingest)
│   └── test_ingestion.py       # 8 tests del pipeline de ingesta
│
├── .env.example                # Plantilla de configuración
├── .env                        # Credenciales (no commitear)
├── .gitignore                  # Archivos a ignorar
├── .dockerignore               # Archivos a ignorar en Docker
├── Dockerfile                  # Imagen de producción
├── docker-compose.yml          # PostgreSQL + pgvector localmente
├── requirements.txt            # Dependencias de producción
├── requirements-dev.txt        # + pytest, coverage, etc.
├── pytest.ini                  # Config de pytest
│
├── seed_medios.py              # Seed idempotente de la tabla Medio
├── verify_setup.py             # Script de verificación rápida
├── VALIDACION_FASE2.md         # Guía de validación contra Postgres real + queries de chequeo
├── QUICK_START.md              # Cómo empezar en 10 segundos
├── SETUP_DOCKER.md             # Cómo levantar PostgreSQL + testing
├── TESTING.md                  # Guía detallada de tests
├── PRUEBAS_RESUMEN.md          # Resumen ejecutivo de pruebas
└── CLAUDE.md                   # Este archivo
```

### Diagrama de Datos

```
Medio (1) ─────→ (∞) Noticia
                   ↓
                   └─→ Cluster (1) ─────→ (∞) Sintesis
```

- **Medio**: Fuente de noticias (ej. Clarín, La Nación, Infobae, TN)
- **Noticia**: Artículo individual con `embedding` (Vector 384 dims)
- **Cluster**: Agrupación de noticias del mismo evento (similitud semántica)
- **Sintesis**: Resumen neutro + comparativa de enfoques (JSON/JSONB)

---

## 🔧 Decisiones Técnicas Importantes

1. **SQLModel + SQLAlchemy 2.0** — Combina ORM + validación Pydantic con type hints completos; un solo modelo para BD y API responses. Nota: `Relationship()` en SQLModel 0.0.39 no acepta `default`/`default_factory` (ver fixes de Fase 1).
2. **PostgreSQL con pgvector** — Búsqueda semántica nativa (HNSW/IVFFlat) sin BD vectorial separada. Solo funciona con PostgreSQL, no con SQLite (tests usan SQLite en memoria, sin vectores reales).
3. **Embeddings 384 dimensiones** — Modelo `all-MiniLM-L6-v2`, rápido y preciso para clustering. Configurable via `EMBEDDING_DIM` en `src/models/noticia.py`.
4. **JSONB para metadata** — Queries eficientes sobre enfoques editoriales en Postgres; `JSON` genérico como variante para SQLite en tests.
5. **FastAPI + Uvicorn** — Async nativo, validación automática, OpenAPI. Nota: hoy usa engine **síncrono** — ver sección de Escalabilidad.
6. **Docker + docker-compose** — Reproducibilidad; Postgres + pgvector preconfigurados. `.env` real nunca entra a la imagen (`.dockerignore`).
7. **Ingesta vía RSS propio de cada medio** (no scraping de página completa, no APIs pagas) — el RSS es contenido que el medio publica a propósito para sindicación de terceros, el canal legalmente más seguro. Detalle completo en Fase 2. Nota: no todos los medios traen el artículo completo en el RSS (`content:encoded`) — Página12 solo da un snippet corto, por eso queda fuera del line-up inicial.

---

## ⚠️ Arquitectura y Escalabilidad — Puntos de Quiebre a Vigilar

Lista viva de límites conocidos del stack actual. No son bugs ni deuda técnica hoy — son decisiones válidas a la escala actual (4 medios, uso interno) que hay que revisar **antes** de escalar. Cada punto indica en qué fase conviene resolverlo, para no perderlos de vista mientras se avanza con las fases funcionales.

1. **Engine de BD síncrono** (revisar en Fase 3+): `database.py` usa `create_engine` síncrono. Bajo carga real (ingesta corriendo a la vez que se atienden queries de usuarios) esto bloquea el event loop de FastAPI. Migrar a `AsyncEngine`/`asyncpg` si la latencia se vuelve un problema medible.
2. **Pool de conexiones sin configurar** (Fase 3+): `get_engine()` no fija `pool_size`/`max_overflow` explícitamente. Con varios workers de Uvicorn el pool por defecto puede agotarse. Definir al fijar el despliegue real (Fase 5).
3. **Índice de pgvector** (Fase 3): todavía no hay índice HNSW/IVFFlat declarado sobre `Noticia.embedding`. Sin eso, la búsqueda semántica hace scan secuencial — decidir tipo de índice al implementar `clustering.py` / `GET /search`.
4. **Scheduler embebido, un solo proceso** (Fase 5): si en el futuro se escala la API a varias réplicas, cada una levantaría su propio scheduler y pollearía los feeds por separado (N veces el mismo trabajo). Ya anotado como riesgo conocido en Fase 2 — separar en proceso propio si eso llega a pasar.
5. **Costo y rate limit del LLM de síntesis** (Fase 4): si `Sintesis` se genera on-demand por cada request en vez de precalculada al cerrar el cluster, un pico de tráfico dispara costo y latencia sin control. Decidir si se precalcula al cerrar el `Cluster`.
6. **Modelo de embeddings en memoria** (Fase 3/5): `sentence-transformers` carga el modelo en RAM por proceso; con varios workers de Uvicorn eso multiplica el consumo de memoria. Decidir si se comparte un único proceso de embeddings o se acepta el costo por worker.
7. **Migraciones con Alembic sin implementar** (todas las fases, urgente antes de tener datos reales): mencionado en "Reglas de Desarrollo" pero nunca configurado — hoy cualquier cambio de esquema depende de recrear tablas desde cero. Implementar antes de que haya datos en producción que no se puedan perder.
8. **Alertas sin observabilidad** (Fase 2/5): la alerta por mail ante fallo de ingesta (ver Fase 2) avisa de un fallo puntual, pero no hay métricas ni logging estructurado todavía — un patrón de fallos recurrente no se nota salvo leyendo mails uno por uno. Cubierto parcialmente por "Monitoreo y Logs" más abajo y por el ítem de Monitoring de Fase 5.

---

## 📋 Fases del Proyecto

### Fase 1: Persistencia y Modelado de Datos ✅ (COMPLETA)
- ✅ Configuración de PostgreSQL con pgvector
- ✅ Modelos SQLModel (Medio, Noticia, Cluster, Sintesis)
- ✅ ORM y relaciones (Relationship)
- ✅ Verificación básica (verify_setup.py) — 4/4 checks OK
- ✅ Tests (14/14 passing)

**Fixes aplicados para dejar Fase 1 funcional** (SQLModel 0.0.39):
- **Relaciones** (`medio.py`, `noticia.py`, `cluster.py`): se sacó `from __future__ import annotations` y `Mapped[...]`. Con anotaciones diferidas (PEP 563), SQLModel no logra resolver la clase destino de la relación (la trata como string literal). Se usa `List["X"] = Relationship(...)` simple, como recomienda la propia doc de SQLModel.
- **`Sintesis` (JSONB)**: `puntos_clave` y `comparativa_enfoques` usan `JSONB().with_variant(JSON(), "sqlite")` — JSONB real en PostgreSQL, JSON genérico en SQLite (los tests en memoria no soportan JSONB).
- **`database.py`**: engine perezoso vía `get_engine()`, no se crea al importar el módulo — así `DATABASE_URL` solo es obligatoria cuando se usa la BD real, y los tests corren sin necesitar Postgres levantado.

**Entregables:**
- src/config.py, database.py, models/
- docker-compose.yml
- verify_setup.py
- Tests básicos (14 tests)

### Fase 2: Ingesta de Noticias ✅ (implementada — pendiente validar contra Postgres real)

**Fuente de datos — decisión clave del diseño.**
Se descartaron: scraping de páginas completas, APIs de noticias comerciales (NewsAPI, GNews, Mediastack) y Google News RSS. Motivo principal: riesgo legal/ToS al extraer contenido completo de la página de un medio sin permiso explícito. Las APIs comerciales tampoco resuelven esto del todo: casi ninguna da texto completo (solo título/snippet) por acuerdos de licencia con los medios, así que igual habría que terminar visitando la página original. Se optó por **RSS directo de cada medio** (el modelo `Medio.feed_rss` ya lo asumía) — es el canal legalmente más seguro, porque el medio lo publica a propósito para sindicación de terceros.

**Medios elegidos** (confirmado empíricamente que traen el artículo completo vía el tag `content:encoded` del item RSS, no solo título/snippet corto). Seed en `seed_medios.py`:
- La Nación — `https://www.lanacion.com.ar/arc/outboundfeeds/rss/`
- Clarín — `https://www.clarin.com/rss/lo-ultimo/`
- TN — `https://tn.com.ar/feed/`
- El Cronista — `https://www.cronista.com/files/rss/news.xml` — confirmado `content:encoded` con artículos completos (~1.200 a 8.500 caracteres). Es 100% argentino (economía/política), sin mezcla de países como Infobae, así que no necesita ningún filtro de prefijo.

**Infobae, descartado.** Se evaluó como candidato pero no tiene sección RSS separada para Argentina (URL de sección probada da 404; el link RSS que la propia página `/argentina/` publica en su pie apunta al feed general, mezclado con otros países de LatAm). Se verificó en vivo el feed general y **ni el prefijo de país en la URL ni la ausencia de prefijo son señales confiables**: de una muestra de 8 items, ninguno tenía `/argentina/`, y varios sin prefijo de país caían en secciones por *tema* (`/opinion/`, `/america/agencias/`) en vez de país — un filtro por prefijo dejaría pasar demasiado ruido o descartaría contenido argentino real. Se reemplazó por **El Cronista**.

**Página12, Perfil, Ámbito Financiero e iProfesional quedan afuera** — los cuatro solo traen `description` corta (Página12: 60-290 caracteres; Perfil: ~350-380; Ámbito: corta; iProfesional: ~130-140), sin `content:encoded`. Si en el futuro hace falta más cobertura, se reevalúa sumar alguno u otro medio que sí traiga contenido completo.

**Contenido y limpieza:**
- Se usa el HTML de `content:encoded` en vez de scrapear la página del medio.
- Limpieza de ese HTML a texto plano: **BeautifulSoup**, no `trafilatura`/`newspaper4k`. Esas dos últimas están pensadas para extraer el artículo "adivinando" cuál parte de una página completa y ruidosa (nav, ads, comentarios) es el contenido real; `content:encoded` ya viene aislado por el propio medio, así que alcanza con un parser simple (`BeautifulSoup(...).get_text()`).
- `trafilatura` y `newspaper4k` quedan en `requirements.txt` reservados, sin uso activo — solo se usarían si en el futuro se suma un medio que no traiga `content:encoded`.

**Deduplicación:**
- Campo `guid` en `Noticia` (además de `url`, ya único). El `guid` de un item RSS es más estable que la URL entre lecturas del feed (la URL puede cambiar por parámetros de tracking o redirecciones), y no todos los medios usan el link como guid.

**Noticias "en vivo" (liveblogs / minuto a minuto): se descartan.**
Contienen información en desarrollo, no un hecho cerrado — demasiado complejo de seguir bien para esta etapa. Se filtran con un heurístico de palabras clave en el título (case-insensitive):
- La Nación: "en vivo" (minúscula, con ":" después).
- TN: título con "vivo" y/o el emoji 🔴 (punto rojo, referencia a luz de cámara prendida) al inicio del título.
- Clarín: en una muestra de 10 items del feed no apareció ningún indicio de cobertura en vivo. Se decide **no** agregar un filtro específico por ahora — el heurístico genérico ("vivo" case-insensitive) alcanza como red de seguridad si aparece alguno; se revisa si se vuelve un problema real en producción.
- El Cronista: evaluado, sin indicios de cobertura en vivo en la muestra revisada. No se agrega ningún filtro específico — igual que Clarín, queda cubierto por el heurístico genérico si llegara a aparecer alguno.
- Infobae queda fuera de este análisis — se descartó como medio (ver "Medios elegidos").

**Scheduler:**
- APScheduler, un solo job con frecuencia uniforme de **15 minutos** para todos los medios. Motivo: un RSS es una ventana de los últimos ~20-50 items, no un log completo — pollear muy poco seguido arriesga perder artículos que se cayeron de la ventana antes de leerlos.
- **Corre embebido** en el proceso de `uvicorn` (dentro del `lifespan` de FastAPI, junto a `init_db()`). Más simple de operar mientras haya una sola réplica de la API. Riesgo de multi-réplica anotado en la sección de Escalabilidad (punto 4).

**Manejo de errores por medio:**
- Reintentos con backoff dentro del mismo fetch (`tenacity`, 2-3 intentos) antes de dar por fallido ese medio en el ciclo — no debe tumbar la corrida completa ni afectar a los demás medios.
- Si se agotan los reintentos, se loguea **y se envía una alerta por mail** usando `smtplib` (stdlib, sin dependencia nueva) al correo del proyecto: `nsinruido@gmail.com`. Falta definir la cuenta/credenciales *emisoras* (host SMTP, usuario, password) al implementar — el destino ya está decidido, el remitente es un detalle de configuración.
- El propio ciclo de 15 minutos ya actúa como reintento natural entre corridas.
- **Cola de mensajes (RabbitMQ/Celery/SQS): descartada por ahora** — más infraestructura de la que este volumen justifica (4 medios, fetch idempotente, sin necesidad de entrega garantizada entre servicios). Anotada como posible cambio de estrategia si el volumen crece lo suficiente.

**Endpoint manual `POST /ingest`: confirmado.**
Sirve para (a) probar el pipeline a mano durante desarrollo sin esperar el próximo ciclo del scheduler, y (b) como fallback operativo si el scheduler se cae.

**Fuera de alcance de Fase 2 (pasa a Fase 3):**
- Detectar si dos artículos de medios distintos hablan del mismo hecho (clustering semántico). Fase 2 deduplica el mismo artículo consigo mismo (por `guid`/`url`), no compara contenido entre artículos distintos — comparar títulos/snippets de forma literal no funciona bien (un mismo hecho se titula muy distinto según el enfoque editorial), por eso se reserva para embeddings en Fase 3.
- Ciclo de vida del `Cluster` (cuánto tiempo queda `"abierto"` esperando cobertura de otros medios). Boceto preliminar para Fase 3/4: deadline en tiempo real desde `Cluster.fecha_creacion` (no contar ciclos de polling, para no atar la regla a la frecuencia del scheduler) y agregar un tercer estado (ej. `"descartado"`) para clusters que no llegan al umbral mínimo de medios, en vez de dejarlos `"abierto"` indefinidamente. Falta decidir el umbral exacto (¿todos los medios, o algo más laxo tipo 2-3 de 4?).

**Pendiente de resolver antes/durante la implementación:**
- [ ] Cuenta/credenciales SMTP emisoras para el mail de alertas (host, usuario, password) — el destino (`nsinruido@gmail.com`) y la librería (`smtplib`) ya están decididos.
- [ ] Qué hacer cuando un `guid` ya ingerido vuelve a aparecer con contenido distinto (liveblogs que se actualizan) — probablemente moot al haber quedado excluidas las notas "en vivo", revisar si puede pasar en otro caso.

**Ya resuelto:**
- ✅ Seed de los 4 `Medio` reales — `seed_medios.py` (idempotente, corre después de que la API haya inicializado la BD al menos una vez).
- ✅ Frases de "en vivo" relevadas para los 4 medios — La Nación y TN con patrón confirmado, Clarín y El Cronista sin indicios en la muestra (cubiertos por el heurístico genérico como red de seguridad).
- ✅ Infobae evaluado y descartado por no poder filtrarse de forma confiable por país; reemplazado por El Cronista (evaluados y descartados en el camino: Perfil, Ámbito Financiero, iProfesional, A24 — ninguno tiene RSS con `content:encoded`, y A24 no tiene RSS público en absoluto).
- ✅ Canal y librería de alertas: `smtplib` → `nsinruido@gmail.com`.

**Entregables:**
- ✅ `src/services/ingestion.py` — fetch (`httpx` + reintentos `tenacity`), limpieza (`BeautifulSoup`), filtro en vivo, dedup por `guid`, alerta por mail (`smtplib`) al agotar reintentos.
- ✅ Endpoint `POST /ingest` (manual y fallback operativo) y scheduler automático embebido (`APScheduler`, `AsyncIOScheduler`, job cada 15 min en el `lifespan`).
- ✅ `seed_medios.py`
- ✅ Tests de ingesta — `tests/test_ingestion.py` (9 tests: heurístico en vivo, limpieza HTML, dedup, fallo de red con alerta mockeada, filtro por medio activo, items sin `content:encoded`) + test del endpoint en `tests/test_api.py`. 24/24 tests pasando.
- ✅ **Validado contra Postgres real** (Postgres 16 + pgvector vía `docker-compose.yml`, que estaba vacío/sin contenido real hasta esta validación — se creó desde cero). Ver `VALIDACION_FASE2.md` para el paso a paso completo y las queries de chequeo. Extensión `vector` y tablas se crean correctamente contra Postgres real, `POST /ingest` corre de punta a punta contra los feeds reales.
- **Hallazgo real durante la validación — Clarín dio 0 noticias en una corrida**: no es un bug. La ventana del feed de Clarín en ese momento estaba compuesta 100% por contenido sin `content:encoded` (horóscopos diarios, un cable de agencia deportivo) — se confirmó inspeccionando el XML crudo del feed en vivo. El pipeline funcionó como está diseñado: descartó esos items por no tener cuerpo completo. Se agregó un contador `sin_contenido` a las stats de `ingerir_medio` (antes esos items se descartaban en silencio, sin ninguna señal) para que este caso sea diagnosticable sin tener que debuggear a mano — y un log de warning si el 100% de la ventana de un medio quedó sin contenido en un ciclo.

### Fase 3: Vectorización y Clustering (Por Hacer)
- [ ] Vectorización con sentence-transformers
- [ ] Almacenamiento en pgvector (definir índice HNSW/IVFFlat — ver Escalabilidad, punto 3)
- [ ] Clustering por similitud (scikit-learn, DBSCAN)
- [ ] Queries de búsqueda semántica

**Entregables:**
- src/services/vectorization.py
- src/services/clustering.py
- Endpoints GET /search, /clusters
- Tests de vectores

### Fase 4: Síntesis Neutra con IA (Por Hacer)
- [ ] Integración con Google Gemini (google-genai)
- [ ] Generación de síntesis neutral
- [ ] Extracción de comparativa de enfoques
- [ ] Validación de neutralidad
- [ ] Decidir generación on-demand vs precalculada al cerrar el cluster (ver Escalabilidad, punto 5)

**Entrega de síntesis al backend web/mobile — diseño cerrado, implementación pendiente (recién en Fase 4).**
El motor no expone la síntesis vía polling: la empuja por webhook al back-end del producto (web/mobile), que la persiste en su propia BD junto a atributos propios (likes, comentarios, etc.). Decisiones:
- **Sin entidad nueva**: no hace falta una clase `NoticiaProcesada` separada — el estado de entrega se guarda como campos directos en `Sintesis` (`enviado_backend: bool`, `fecha_envio: Optional[datetime]`, `intentos_envio: int`). Se descartó una tabla de log de envíos aparte por sobre-ingeniería: hoy hay un solo backend destino.
- **Reintentos**: `tenacity` en el momento de enviar (mismo patrón que la ingesta). Si se agotan, la `Sintesis` queda con `enviado_backend=False` y un **job periódico sobre el `APScheduler` ya existente** (no una cola de mensajes) barre las síntesis no entregadas y reintenta. Sin reenvío manual — se descartó por depender de que un operario vea una alerta y actúe.
- **Autenticación del webhook**: firma HMAC-SHA256 sobre el cuerpo del request + timestamp en el header (para poder rechazar requests viejos y mitigar replay), con secreto compartido vía variable de entorno en ambos lados. Se prefirió por sobre un token estático porque el secreto nunca viaja en la red (se manda una firma derivada, no el secreto en sí) — defensa en profundidad más allá de lo que ya da TLS.
- **Idempotencia del lado del backend receptor**: queda a resolver por el equipo de backend/mobile, no es una decisión de este repo.

**Entregables:**
- src/services/synthesis.py
- Endpoint POST /synthesize
- src/services/webhook_delivery.py (envío + firma HMAC + reintentos)
- Job periódico de reintento de entregas fallidas
- Tests de síntesis y de entrega por webhook

### Fase 5: Deployment y Escalabilidad (Por Hacer)
- [ ] Kubernetes manifests
- [ ] CI/CD (GitHub Actions)
- [ ] Monitoring (Prometheus, Grafana)
- [ ] Caching (Redis)
- [ ] Rate limiting
- [ ] Resolver los puntos de quiebre pendientes de la sección "Arquitectura y Escalabilidad" (pool de conexiones, scheduler multi-réplica, memoria de embeddings)

---

## 💻 Reglas de Desarrollo

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
   - `services/` — Lógica de negocio (Fase 2+)
   - `routers/` — Endpoints FastAPI (Fase 2+)
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

### BD y Migrations

1. **Alembic para migrations** (pendiente implementar — ver Escalabilidad, punto 7)
   ```bash
   alembic revision --autogenerate -m "Descripción del cambio"
   alembic upgrade head
   ```

2. **Nunca dropear tablas** en producción
3. **Validar constraints** en modelos (unique, indexes)

### Git y Versionado

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

---

## 🐳 Docker y Deployment

### Desarrollo Local

```bash
# 1. Crear venv
python -m venv .venv
.venv\Scripts\activate  # Windows cmd

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Levantar PostgreSQL
docker-compose up -d

# 4. Verificar setup
python verify_setup.py

# 5. Tests
pytest

# 6. Servidor
uvicorn src.main:app --reload
```

### Producción (con Docker)

```bash
# Build imagen
docker build -t sin-ruido:latest .

# Run contenedor
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql+psycopg://... \
  -e ENVIRONMENT=production \
  sin-ruido:latest
```

### Variables de Entorno Críticas

- `DATABASE_URL` — PostgreSQL con pgvector (obligatorio en producción)
- `GEMINI_API_KEY` — Google Gemini (opcional en Fase 1)
- `ENVIRONMENT` — `development` | `production`

---

## 📊 Monitoreo y Logs

### Logs Recomendados

```python
import logging

logger = logging.getLogger(__name__)
logger.info(f"Noticia insertada: {noticia.id}")
logger.error(f"Error al vectorizar: {e}")
```

### Health Checks

- `GET /` — Estado general de la API
- `GET /test-db` — Verificación de conexión a BD (temporal, remover en Fase 2)

### Métricas a Rastrear (Fase 5)

- Latencia de ingesta
- Precisión de clustering
- Cobertura de síntesis
- Errores por fuente
- Ver también sección "Arquitectura y Escalabilidad", punto 8 (alertas sin observabilidad)

---

## 🔐 Seguridad

1. **No commitear `.env`** (está en `.gitignore`)
2. **CORS configurado** en FastAPI (agregar en Fase 2 si hay frontend)
3. **Validación de entrada** automática (Pydantic)
4. **Rate limiting** (agregar en Fase 5)
5. **Autenticación** (agregar si hay API pública en Fase 5)

---

## 📚 Referencias Útiles

### Documentación Oficial
- [FastAPI](https://fastapi.tiangolo.com/)
- [SQLModel](https://sqlmodel.tiangolo.com/)
- [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/)
- [pgvector](https://github.com/pgvector/pgvector)
- [sentence-transformers](https://www.sbert.net/)

### Artículos Técnicos
- [SQLAlchemy 2.0 with Pydantic](https://sqlmodel.tiangolo.com/)
- [pgvector for semantic search](https://github.com/pgvector/pgvector#python)
- [Clustering noticias](https://scikit-learn.org/stable/modules/clustering.html)

### Comunidades
- FastAPI Discord: https://discord.gg/VQjSZaeJmf
- SQLAlchemy: https://sqlalchemy.discourse.group/

---

## ✅ Checklist para Próximas Iteraciones

Antes de empezar cualquier fase nueva:

- [ ] Leer este archivo completo
- [ ] Verificar que el entorno local está en orden (`python verify_setup.py`)
- [ ] Ejecutar tests existentes (`pytest`)
- [ ] Crear branch de feature
- [ ] Escribir tests para nuevas funcionalidades
- [ ] Asegurar cobertura ≥80%
- [ ] Hacer commit atómico y descriptivo
- [ ] Crear PR con descripción clara

---

## 📝 Notas Finales

- **Este proyecto prioriza claridad sobre optimalidad prematura**. No sobre-ingenierices.
- **La documentación es código**. Mantén este archivo y los README actualizados.
- **Pregunta temprano**. Si algo no está claro, pregunta a Claude o al equipo antes de hacer cambios.
- **Aprende del código existente**. Las decisiones están documentadas aquí.

---

**Última actualización:** 7 de agosto de 2026  
**Fase actual:** Fase 2 (Ingesta de Noticias) ✅ completa — implementada, testeada (24/24 tests) y validada contra Postgres + pgvector real y los feeds RSS reales de los 4 medios.  
**Siguiente:** Fase 3 (Vectorización y Clustering)

Bienvenido al equipo. 🚀
