# 🗺️ Roadmap — Sin Ruido

Estado de las 5 fases del proyecto. El *qué* y *cuándo* vive acá; el *por qué* de cada decisión está en `change_logs.md`.

**Fase actual:** Fase 4 (Síntesis Neutra con IA), **en curso**. El motor ya produce publicaciones reales de punta a punta —ingesta → vectorización → clustering → fusión → síntesis por ángulo con Gemini— validado contra 1.296 noticias y 138/138 tests.
**Falta para cerrarla:** la entrega por webhook al back-end. Hoy las síntesis se generan y quedan en la base; nadie las consume.

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
- [x] `fusionar_clusters_duplicados()`: une los clusters abiertos que quedaron cubriendo el mismo hecho, iterando hasta el punto fijo. Corrige el artefacto por el que una sola muerte de alta cobertura dejaba 20 clusters con centroides a 0.94 entre sí
- [x] Endpoints `POST /vectorize` y `POST /cluster` (disparo manual y fallback, mismo criterio que `/ingest`)
- [x] Pipeline encadenado en el scheduler: ingesta → vectorización → cierre → agrupamiento → fusión
- [x] `search.py` + endpoints de consulta `GET /search` (búsqueda semántica, único lugar donde se usa el KNN de pgvector) y `GET /clusters`
- [x] Tests: 37 nuevos (9 de vectorización + 20 de clustering + 8 de endpoints), **61/61 en total**

**Parámetros fijados** (todos en `config.py`, ajustables por `.env`): umbral de asignación 0.75 · umbral de fusión 0.90 · centroide (no vecino más cercano) · mínimo 2 medios para publicar · ventana abierta 12 h · sin índice HNSW por ahora.

**Reparto con Fase 4** (decidido al cerrar la fase): el clustering agrupa **el hecho y su cobertura** optimizando recall; la separación por **ángulo** —y con ella la unidad que se publica— le corresponde a Fase 4, que lee los textos. La similitud coseno mide de qué habla una nota, no qué ángulo toma, así que ningún umbral puede hacer ese trabajo. Detalle y evidencia en `change_logs.md`.

**Validado contra datos reales:** 620 noticias vectorizadas en 14,5 s (384 dims, norma 1.0000 verificada en la BD). Agrupamiento sobre la ventana de 12 h: **37 clusters, 30 publicables (81%)**, con clusters de hasta 4 medios distintos. El más grande quedó en 6 noticias — sin encadenamiento (la simulación con vecino más cercano producía uno de 13). Flujo incremental probado: al ingerir noticias nuevas se sumaron correctamente a clusters ya existentes. `GET /search` verificado contra Postgres: la consulta *"crisis diplomatica con Brasil"* devuelve 4 notas correctas (0.82-0.73) que **no contienen esa frase textual**.

**Entregables:** `src/services/vectorization.py`, `src/services/clustering.py`, `src/services/search.py`, `POST /vectorize`, `POST /cluster`, `GET /search`, `GET /clusters`, tests.

- [x] Removido `GET /test-db`, que era temporal e insertaba un `Medio` de prueba en cada llamada

**Pendiente menor** (no bloquea Fase 4):
- [ ] Las consultas semánticas abstractas (ej. "romance de famosos") dan similitudes bajas (~0.5) frente a las de un hecho concreto (~0.8). No es un bug, pero conviene tenerlo en cuenta al consumir `GET /search` desde el front.

---

## Fase 4: Síntesis Neutra con IA (En curso — falta la entrega por webhook)

- [x] **Preproceso de evidencia** (`preprocessing.py`): núcleo compartido, vocabulario propio por medio (TF-IDF con IDF de corpus) y entidades exclusivas/omitidas (spaCy NER). Entra al prompt como pistas a verificar, no como conclusiones
- [x] **Esquema de `Sintesis` por ángulo** + `SintesisNoticia` + campos de entrega (migración `979689aeb928`)
- [x] Qué se le manda al modelo: cuerpo completo de las `SINTESIS_NOTAS_POR_MEDIO` notas más representativas **de cada medio**. Medido: 68.534 tokens de entrada para 21 clusters publicables, US$0,007-0,021 por corrida
- [x] **Cuándo se dispara**: al alcanzar 2 medios, no al cerrar el cluster (esperar el cierre publicaba a las ~13 h). Marca `Cluster.noticias_al_sintetizar` + noticias sin ángulo vía `SintesisNoticia` (migraciones `98c48e2dc7b1` y `faa5d6fc466e`)
- [x] **La descomposición en ángulos se congela** en la primera síntesis: las re-síntesis actualizan o agregan, nunca reparten de nuevo. `Sintesis.id` es la clave de idempotencia del webhook
- [x] `synthesis.py`: integración con Gemini (`google-genai`) con salida estructurada, separación en ángulos, filtro de cobertura por ángulo, y manejo diferenciado del bloqueo por filtros de contenido
- [x] `POST /synthesize` (disparo manual y fallback, mismo criterio que `/ingest` y `/cluster`)
- [x] `alerts.py` + aislamiento de pasos en el scheduler: un paso que falla avisa y no frena a los siguientes; la fusión es la única que corta la cadena
- [x] **Probado contra Gemini real** con `gemini-3.5-flash-lite`: 6.747 tokens de entrada, 879 de salida, 0 de razonamiento; separó un cluster en dos ángulos correctos con comparativa citada. Detalle y correcciones en `change_logs.md`
- [x] **Categorías sin hecho** (`categorias.py`): horóscopos, recetas y quiniela quedan fuera del agrupamiento — no hay enfoques que comparar. Qué se hace con ellas es del back-end
- [x] **Validado de punta a punta con datos reales**: 11 publicaciones sobre 8 hechos, tres de ellos con dos ángulos cada uno
- [ ] **Entrega al backend por webhook** + firma HMAC + job de reintento de las no entregadas (diseño cerrado en `change_logs.md`; `Sintesis.id` es la clave de idempotencia)
- [ ] Validación de neutralidad de lo que devuelve el modelo — **no es detectable por código de forma confiable**; se ataca con el prompt y revisión manual sobre corridas reales

**Entregables:** `src/services/preprocessing.py`, `synthesis.py`, `categorias.py`, `alerts.py`, `POST /synthesize`, `src/services/webhook_delivery.py` (pendiente), job periódico de reintento (pendiente).

**Pendiente de observación** (no bloquea): un cluster de economía juntó inflación y mora, dos hechos distintos pegados por vocabulario compartido. El modelo los separó bien en dos ángulos, pero el agrupamiento no debió unirlos. Una sola observación — mirar si se repite antes de tocar nada.

---

## Fase 5: Deployment y Escalabilidad (Por Hacer)

- [ ] Kubernetes manifests
- [ ] CI/CD (GitHub Actions)
- [ ] Monitoring (Prometheus, Grafana) — métricas: latencia de ingesta, precisión de clustering, cobertura de síntesis, errores por fuente
- [ ] Caching (Redis)
- [ ] Rate limiting
- [ ] Resolver los puntos de quiebre pendientes de `tech_stack.md` (pool de conexiones, scheduler multi-réplica, memoria de embeddings)
