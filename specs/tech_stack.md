# 🛠️ Tech Stack — Sin Ruido

Inventario de tecnologías utilizadas, estructura del proyecto, y límites conocidos del stack a vigilar a medida que escala. Para el *por qué* de cada elección (alternativas evaluadas, hallazgos empíricos), ver `change_logs.md`.

---

## Stack tecnológico

| Componente | Tecnología | Versión |
|---|---|---|
| **Lenguaje** | Python | 3.12 |
| **Framework Web** | FastAPI | ≥0.115.0 |
| **Servidor** | Uvicorn | ≥0.31.0 |
| **ORM** | SQLModel (SQLAlchemy 2.0 + Pydantic v2) | ≥0.0.16 |
| **Base de Datos** | PostgreSQL | 16+ |
| **Vectores** | pgvector | ≥0.2.5 |
| **Embeddings** | sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`, 384 dims) | ≥2.6.0 |
| **NLP** | spacy — solo NER si hace falta, NO para embeddings | ≥3.8.0 |
| **Ingesta** | feedparser, beautifulsoup4, httpx, tenacity (trafilatura/newspaper4k reservados) | Últimas versiones |
| **Scheduler** | APScheduler | ≥3.10.4 |
| **Testing** | pytest | ≥7.4.0 |
| **Containerización** | Docker + docker-compose | Latest |
| **Control de versiones** | Git | Latest |

### Por qué cada pieza clave

1. **SQLModel + SQLAlchemy 2.0** — Combina ORM + validación Pydantic con type hints completos; un solo modelo para BD y API responses. Nota: `Relationship()` en SQLModel 0.0.39 no acepta `default`/`default_factory` (ver fixes de Fase 1 en `change_logs.md`).
2. **PostgreSQL con pgvector** — Búsqueda semántica nativa (HNSW/IVFFlat) sin BD vectorial separada. Solo funciona con PostgreSQL, no con SQLite (tests usan SQLite en memoria, sin vectores reales).
3. **Embeddings 384 dimensiones** — Modelo `paraphrase-multilingual-MiniLM-L12-v2`: multilingüe (las noticias son en español, y el `all-MiniLM-L6-v2` original es esencialmente inglés) y también de 384 dims, así que la columna `Vector(384)` no cambia. Configurable via `EMBEDDING_DIM` en `src/models/noticia.py`.
4. **JSONB para metadata** — Queries eficientes sobre enfoques editoriales en Postgres; `JSON` genérico como variante para SQLite en tests.
5. **FastAPI + Uvicorn** — Async nativo, validación automática, OpenAPI. Nota: hoy usa engine **síncrono** — ver "Arquitectura y Escalabilidad" más abajo.
6. **Docker + docker-compose** — Reproducibilidad; Postgres + pgvector preconfigurados. `.env` real nunca entra a la imagen (`.dockerignore`).
7. **Ingesta vía RSS propio de cada medio** (no scraping de página completa, no APIs pagas) — el canal legalmente más seguro. Detalle completo de la evaluación de medios en `change_logs.md`, Fase 2.

---

## Estructura de directorios

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
│   │   ├── tipos.py            # Tipos de columna compartidos (JSONB/JSON)
│   │   ├── cluster.py          # Modelo: Cluster (agrupación por evento)
│   │   └── sintesis.py         # Modelos: Sintesis (un ángulo) + SintesisNoticia
│   └── services/
│       ├── __init__.py
│       ├── ingestion.py        # Pipeline de ingesta RSS (Fase 2)
│       ├── vectorization.py    # Embeddings de noticias (Fase 3)
│       ├── clustering.py       # Agrupamiento incremental + fusión + cierre (Fase 3)
│       ├── preprocessing.py    # Evidencia (TF-IDF + NER) para el prompt (Fase 4)
│       ├── synthesis.py        # Síntesis por ángulo con Gemini (Fase 4)
│       ├── categorias.py       # Notas sin hecho (horóscopo, recetas): no se agrupan
│       ├── topicos.py          # Taxonomía cerrada + sección declarada por el medio (Fase 4)
│       ├── webhook_delivery.py # Entrega firmada de las síntesis al back-end (Fase 4)
│       ├── alerts.py           # Avisos por mail ante fallo de cualquier paso
│       └── search.py           # Búsqueda semántica y listado de clusters (Fase 3)
│
├── alembic/                    # Migraciones de esquema
│   ├── env.py                  # Toma DATABASE_URL de src.config
│   ├── script.py.mako          # Plantilla (importa sqlmodel y pgvector)
│   └── versions/
├── alembic.ini                 # Sin credenciales: la URL sale del .env
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # Fixtures pytest (session, client)
│   ├── test_db.py              # 9 tests de BD y modelos
│   ├── test_api.py             # 16 tests de endpoints
│   ├── test_ingestion.py       # 9 tests del pipeline de ingesta
│   ├── test_vectorization.py   # 9 tests de vectorización
│   ├── test_clustering.py      # 20 tests de agrupamiento, fusión y cierre
│   └── test_preprocessing.py   # 21 tests de evidencia para la síntesis
│
├── specs/                      # Fuente de verdad del proyecto
│   ├── mission.md
│   ├── roadmap.md
│   ├── change_logs.md
│   └── tech_stack.md           # Este archivo
│
├── .env.example                # Plantilla de configuración
├── .env                        # Credenciales (no commitear)
├── .gitignore                  # Archivos a ignorar
├── .dockerignore                # Archivos a ignorar en Docker
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
├── TESTING.md                  # Guía detallada de tests
├── PRUEBAS_RESUMEN.md          # Resumen ejecutivo de pruebas
├── CLAUDE.md                   # Índice corto — apunta a specs/
└── README.md                   # Punta de entrada del repo
```

## Diagrama de datos

```
Medio (1) ─────→ (∞) Noticia
                       ↓         ↑
                       ↓         └──── (∞) SintesisNoticia (∞) ────┐
                       ↓                                           │
                       └─→ Cluster (1) ─────→ (∞) Sintesis ────────┘
```

- **Medio**: Fuente de noticias. 6 activos: La Nación, TN, El Cronista (generales) + Revista Gente, Revista Paparazzi, Ciudad Magazine (espectáculos)
- **Noticia**: Artículo individual con `embedding` (Vector 384 dims)
- **Cluster**: El hecho y toda su cobertura. Agrupa por similitud semántica buscando no perder cobertura; **no** es la unidad que se publica
- **Sintesis**: Un **ángulo** del cluster (el hecho, sus consecuencias, las reacciones) con su resumen neutro y comparativa de enfoques. Un cluster produce varias, y separarlas requiere leer los textos — lo hace el modelo en Fase 4
- **SintesisNoticia**: Qué noticias respaldan cada ángulo. Es muchos-a-muchos porque una nota puede sostener varios ángulos, y es tabla (y no una lista JSON) porque de este join sale el `count(distinct medio_id)` que decide si el ángulo se publica

---

## Arquitectura y Escalabilidad — Puntos de quiebre a vigilar

Lista viva de límites conocidos del stack actual. No son bugs ni deuda técnica hoy — son decisiones válidas a la escala actual (4 medios, uso interno) que hay que revisar **antes** de escalar. Cada punto indica en qué fase conviene resolverlo (ver `roadmap.md`).

1. **Engine de BD síncrono** (revisar en Fase 3+): `database.py` usa `create_engine` síncrono. Bajo carga real (ingesta corriendo a la vez que se atienden queries de usuarios) esto bloquea el event loop de FastAPI. Migrar a `AsyncEngine`/`asyncpg` si la latencia se vuelve un problema medible.
2. ~~**Pool de conexiones sin configurar**~~ ✅ **RESUELTO en Fase 5**: `DB_POOL_SIZE=5` / `DB_MAX_OVERFLOW=10` / `DB_POOL_TIMEOUT=30` / `DB_POOL_RECYCLE=1800`, configurables por `.env`. Calibrados contra un solo proceso Uvicorn (el único que hay: Dockerfile sin `--workers`). El razonamiento completo, incluida la cuenta de qué pasa si se agregan réplicas, está en `config.py` (comentario largo, como el resto del archivo) y en `change_logs.md`.
3. **Índice de pgvector** (revisar cuando crezca el volumen): sigue sin haber índice HNSW/IVFFlat sobre `Noticia.embedding`, **a propósito**. A la escala actual el scan secuencial se resuelve en milisegundos, y con un `WHERE` restrictivo el índice ANN pierde parte de su ventaja igual. Además el clustering ni siquiera usa el KNN de la base: compara centroides en memoria. El único consumidor real del KNN es `GET /search`. Crear el índice cuando la tabla `noticia` llegue al orden de las decenas de miles de filas.
4. **Scheduler embebido, un solo proceso** (diferido tras Fase 5 — ver roadmap.md, "Diferido a propósito"): si en el futuro se escala la API a varias réplicas, cada una levantaría su propio scheduler y pollearía los feeds por separado (N veces el mismo trabajo). Fase 5 fijó el despliegue en una sola réplica a propósito, así que esto sigue sin resolverse. Separar en proceso propio si eso llega a pasar.
5. **Costo y rate limit del LLM de síntesis** (Fase 4): ✅ **decidido** — la síntesis se precalcula al alcanzar 2 medios, nunca on-demand por request, así que un pico de tráfico no dispara costo. Queda vigente el **rate limit**: en una corrida se sintetizan todos los clusters publicables de una (medido: 21), y la capa gratuita de Gemini limita por minuto. Necesita backoff con `tenacity`, igual que la ingesta.
6. **Modelo de embeddings en memoria** (Fase 3/5): `sentence-transformers` carga el modelo en RAM por proceso; con varios workers de Uvicorn eso multiplica el consumo de memoria. Decidir si se comparte un único proceso de embeddings o se acepta el costo por worker.
7. ~~**Migraciones con Alembic sin implementar**~~ ✅ **RESUELTO**: Alembic configurado, migración inicial aplicada y base real marcada con `alembic stamp head` sin perder las noticias ya ingeridas. `init_db()` dejó de usar `create_all()` y ahora solo habilita la extensión y verifica que el esquema esté migrado. Ver `mission.md`, "BD y migraciones".
8. ~~**El pipeline se auto-repara pero es mudo**~~ ✅ **RESUELTO**: `services/alerts.py` avisa por mail ante el fallo de cualquier paso, con cooldown para no inundar la casilla, y en el scheduler cada paso corre aislado (la fusión es la única que corta la cadena, para no publicar duplicados). La contingencia de fondo sigue siendo la idempotencia de todos los pasos: un crash a mitad se recupera solo en la corrida siguiente. Queda pendiente el logging estructurado y las métricas, que cubre Monitoring en Fase 5.

9. **`agrupar_pendientes` es cuadrático** (queda fuera de Fase 5 a propósito — es volumen de noticias, no deployment): compara cada noticia suelta contra todas las demás de la ventana. Medido: 3,6 s con ~200 sueltas; proyectado, ~14 s con 400 y cerca de un minuto con 800. Con 6 medios y ventana de 12 h no se llega ni cerca, pero es el primer lugar que se va a poner lento al sumar medios. La salida sería acotar los candidatos con el KNN de pgvector en vez de comparar contra todo (ver punto 3, que hoy es el único motivo por el que el índice no hace falta).

10. **Los eventos de varios días generan clusters sucesivos** (Fase 4): un cluster cierra a las 12 h de creado, así que la cobertura del día siguiente arma uno nuevo, y la fusión solo toca clusters abiertos. Una historia larga (la muerte de Jorge Messi cubrió varios días) produce un segundo conjunto de publicaciones. En parte es correcto —el velorio es otro hecho— pero puede haber solapamiento. Revisar con síntesis reales a la vista.

11. ~~**3 consultas que no escalaban**~~ ✅ **RESUELTO en Fase 5**: `synthesis.clusters_pendientes` (cargaba `SintesisNoticia` entera sin filtrar + N+1 sobre `Noticia`), `search.listar_clusters` (N+1 con join manual, hasta 101 queries con `limite=100`) y `synthesis.descartar_vencidos_sin_sintetizar` (N+1 vía relationship lazy-loaded). Los tres resueltos con `selectinload` acotado por `IN`. Apareció un cuarto caso no anticipado: en `descartar_vencidos_sin_sintetizar`, `session.commit()` expira los atributos por defecto (`expire_on_commit=True`), y el código volvía a leer `c.id`/`c.noticias` después del commit — mismo N+1, corriendo después en vez de antes. Se resolvió capturando esos valores antes de commitear. Detalle y las queries antes/después en `change_logs.md`.

---

## Docker y deployment

### Desarrollo local

```bash
# 1. Crear venv
python -m venv .venv
.venv\Scripts\activate  # Windows cmd

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Levantar PostgreSQL + pgvector
docker compose up -d

# 4. Aplicar el esquema (obligatorio: init_db ya no crea tablas)
alembic upgrade head

# 5. Verificar setup
python verify_setup.py

# 6. Tests
pytest

# 7. Servidor
uvicorn src.main:app --reload
```

Guía completa y queries de verificación contra Postgres real: `VALIDACION_FASE2.md`.

### Producción (Fase 5: VPS único con Docker Compose)

`docker-compose.yml` ya define los dos servicios, `db` y `app`. `app` espera a que `db` esté healthy, aplica las migraciones de Alembic y recién ahí levanta Uvicorn — todo en un solo `command`, sin un service `migrate` aparte (ver `change_logs.md` para por qué).

```bash
# Build + levantar los dos servicios
docker compose up -d --build

# Ver que app terminó de migrar y arrancó
docker logs sin_ruido_app

# Healthcheck real: devuelve 503 si la base no responde, no solo "el proceso vive"
curl http://localhost:8000/
```

El `.env` real (nunca commiteado) se inyecta al contenedor `app` vía `env_file`; `DATABASE_URL` se pisa dentro del compose para que apunte a `db` (el nombre del servicio en la red interna de Docker) en vez de `localhost`.

### CI (GitHub Actions)

`.github/workflows/ci.yml` corre en cada push/PR a `main`, con dos jobs de propósito distinto:
- **`tests`**: `pytest` con cobertura ≥80%, sin Postgres — la suite completa (221 tests) pasa contra SQLite en memoria.
- **`migraciones`**: `alembic upgrade head` contra un servicio `pgvector/pgvector:pg16` real, para atrapar una migración autogenerada que se rompe contra un esquema con datos (ya pasó una vez en Fase 4).

### Manejo del tiempo

**Se guarda en UTC, se muestra en UTC-3.** La decisión vive en `src/tiempo.py`, que es el único lugar donde se toca una zona horaria.

El almacenamiento no puede pasarse a hora local por tres razones concretas: `fecha_publicacion` llega del RSS en UTC y se compara contra "ahora" para la ventana de 12 h del cluster (con una punta en local la ventana se correría 3 h sin que nada falle a la vista); `webhook_contract.md` ya le prometió fechas UTC con `Z` al back-end; y ya nos costó un bug real — la firma del webhook usaba `datetime.utcnow().timestamp()`, que lee un naive como hora local, y el receptor rechazaba todo con 401.

Lo que sí es local es todo lo que lee una persona: la API devuelve ISO con el offset explícito (`2026-08-09T18:48:35-03:00`) y el pipeline loguea su arranque y cierre en hora argentina. `GET /` expone `hora_local` para verificar el reloj del contenedor.

En el código no se usa `datetime.utcnow()` —deprecado desde Python 3.12— sino `tiempo.ahora_utc()`, que devuelve exactamente lo mismo (UTC naive) sin el warning.

### Variables de entorno críticas

- `DATABASE_URL` — PostgreSQL con pgvector (obligatorio en producción)
- `GEMINI_API_KEY` — Google Gemini (usada desde Fase 4)
- `ENVIRONMENT` — `development` | `production`
- `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `ALERT_EMAIL_TO` — alertas de fallo de ingesta (ver `change_logs.md`, Fase 2)
- `WEBHOOK_URL` / `WEBHOOK_SECRET` — entrega de síntesis al back-end. **Sin ellas la entrega no corre**: las síntesis se acumulan en la base con `enviado_backend=False` y salen cuando se configuran (ver `webhook_contract.md`)
- `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` / `DB_POOL_RECYCLE` — pool de conexiones (Fase 5), calibrados contra un solo proceso Uvicorn. Tienen default razonable en `config.py`, no son obligatorias para arrancar.

---

## Referencias útiles

### Documentación oficial
- [FastAPI](https://fastapi.tiangolo.com/)
- [SQLModel](https://sqlmodel.tiangolo.com/)
- [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/)
- [pgvector](https://github.com/pgvector/pgvector)
- [sentence-transformers](https://www.sbert.net/)

### Artículos técnicos
- [SQLAlchemy 2.0 with Pydantic](https://sqlmodel.tiangolo.com/)
- [pgvector for semantic search](https://github.com/pgvector/pgvector#python)
- [Clustering noticias](https://scikit-learn.org/stable/modules/clustering.html)

### Comunidades
- FastAPI Discord: https://discord.gg/VQjSZaeJmf
- SQLAlchemy: https://sqlalchemy.discourse.group/
