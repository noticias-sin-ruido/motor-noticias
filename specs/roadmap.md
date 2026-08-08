# 🗺️ Roadmap — Sin Ruido

Estado de las 5 fases del proyecto. El *qué* y *cuándo* vive acá; el *por qué* de cada decisión está en `change_logs.md`.

**Fase actual:** Fase 3 ✅ completa — vectorización, clustering incremental y endpoints de consulta implementados, testeados (56/56) y validados contra Postgres + pgvector real con 620 noticias.
**Siguiente:** Fase 4 (Síntesis Neutra con IA) — el diseño de entrega por webhook ya está cerrado en `change_logs.md`.

---

## Fase 1: Persistencia y Modelado de Datos ✅ COMPLETA

- ✅ Configuración de PostgreSQL con pgvector
- ✅ Modelos SQLModel (Medio, Noticia, Cluster, Sintesis)
- ✅ ORM y relaciones (Relationship)
- ✅ Verificación básica (`verify_setup.py`) — 4/4 checks OK
- ✅ Tests (14/14 passing)

**Entregables:** `src/config.py`, `database.py`, `models/`, `docker-compose.yml`, `verify_setup.py`, tests básicos.

Fixes de compatibilidad con SQLModel 0.0.39 y decisiones de diseño: ver `change_logs.md`.

---

## Fase 2: Ingesta de Noticias ✅ COMPLETA

- ✅ Fuente de datos: RSS directo de cada medio
- ✅ 6 medios activos: La Nación, TN, El Cronista (generales) + Revista Gente, Revista Paparazzi, Ciudad Magazine (espectáculos). Clarín descartado por no traer `content:encoded`
- ✅ User-Agent propio e identificable en las peticiones
- ✅ Limpieza de contenido, deduplicación por `guid`, filtro de notas "en vivo"
- ✅ Scheduler embebido (15 min) + endpoint manual `POST /ingest`
- ✅ Reintentos (`tenacity`) + alerta por mail (`smtplib`) ante fallo de medio
- ✅ `seed_medios.py`
- ✅ Tests (`test_ingestion.py` + `test_api.py`) — 24/24 pasando
- ✅ Validado contra Postgres + pgvector real (ver `VALIDACION_FASE2.md`)

**Entregables:** `src/services/ingestion.py`, `POST /ingest`, scheduler automático, `seed_medios.py`.

Todo el proceso de evaluación de medios (Infobae descartado, El Cronista confirmado, etc.), el diseño del manejo de errores, y el hallazgo de Clarín durante la validación: ver `change_logs.md`.

---

## Fase 3: Vectorización y Clustering ✅ COMPLETA

Diseño calibrado contra 620 noticias reales — parámetros y razonamiento completo en `change_logs.md`.

- [x] **Alembic configurado** — migración inicial aplicada y base real marcada con `stamp head` sin perder datos; `init_db()` ya no usa `create_all()`
- [x] `vectorization.py`: vectoriza noticias con `embedding IS NULL` usando `paraphrase-multilingual-MiniLM-L12-v2` sobre `título + primeros 500 caracteres`, por lotes y normalizado. Modelo cargado de forma perezosa
- [x] `clustering.py`: asignación **incremental** (no DBSCAN batch) contra el centroide de los clusters abiertos
- [x] Cluster se crea recién con el segundo artículo; la noticia sin match queda con `cluster_id = NULL` y se reevalúa en corridas siguientes
- [x] `cerrar_clusters_vencidos()`: `abierto` → `procesado` si alcanzó el mínimo de medios, `descartado` si no
- [x] Endpoints `POST /vectorize` y `POST /cluster` (disparo manual y fallback, mismo criterio que `/ingest`)
- [x] Pipeline encadenado en el scheduler: ingesta → vectorización → cierre → agrupamiento
- [x] `search.py` + endpoints de consulta `GET /search` (búsqueda semántica, único lugar donde se usa el KNN de pgvector) y `GET /clusters`
- [x] Tests: 32 nuevos (9 de vectorización + 13 de clustering + 10 de endpoints), **56/56 en total**

**Parámetros fijados** (todos en `config.py`, ajustables por `.env`): umbral 0.75 · centroide (no vecino más cercano) · mínimo 2 medios para publicar · ventana abierta 12 h · sin índice HNSW por ahora.

**Validado contra datos reales:** 620 noticias vectorizadas en 14,5 s (384 dims, norma 1.0000 verificada en la BD). Agrupamiento sobre la ventana de 12 h: **37 clusters, 30 publicables (81%)**, con clusters de hasta 4 medios distintos. El más grande quedó en 6 noticias — sin encadenamiento (la simulación con vecino más cercano producía uno de 13). Flujo incremental probado: al ingerir noticias nuevas se sumaron correctamente a clusters ya existentes. `GET /search` verificado contra Postgres: la consulta *"crisis diplomatica con Brasil"* devuelve 4 notas correctas (0.82-0.73) que **no contienen esa frase textual**.

**Entregables:** `src/services/vectorization.py`, `src/services/clustering.py`, `src/services/search.py`, `POST /vectorize`, `POST /cluster`, `GET /search`, `GET /clusters`, tests.

- [x] Removido `GET /test-db`, que era temporal e insertaba un `Medio` de prueba en cada llamada

**Pendiente menor** (no bloquea Fase 4):
- [ ] Las consultas semánticas abstractas (ej. "romance de famosos") dan similitudes bajas (~0.5) frente a las de un hecho concreto (~0.8). No es un bug, pero conviene tenerlo en cuenta al consumir `GET /search` desde el front.

---

## Fase 4: Síntesis Neutra con IA (Por Hacer)

- [ ] Integración con Google Gemini (`google-genai`)
- [ ] Generación de síntesis neutral
- [ ] Extracción de comparativa de enfoques
- [ ] Validación de neutralidad
- [ ] Generación on-demand vs. precalculada al cerrar el cluster (ver `tech_stack.md`, punto 5 de Escalabilidad)
- [ ] Entrega al backend web/mobile por webhook (diseño ya cerrado — ver `change_logs.md`)

**Entregables:** `src/services/synthesis.py`, `POST /synthesize`, `src/services/webhook_delivery.py`, job periódico de reintento de entregas fallidas, tests de síntesis y de entrega por webhook.

---

## Fase 5: Deployment y Escalabilidad (Por Hacer)

- [ ] Kubernetes manifests
- [ ] CI/CD (GitHub Actions)
- [ ] Monitoring (Prometheus, Grafana) — métricas: latencia de ingesta, precisión de clustering, cobertura de síntesis, errores por fuente
- [ ] Caching (Redis)
- [ ] Rate limiting
- [ ] Resolver los puntos de quiebre pendientes de `tech_stack.md` (pool de conexiones, scheduler multi-réplica, memoria de embeddings)
