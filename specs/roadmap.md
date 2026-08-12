# 🗺️ Roadmap — Sin Ruido

Estado de las 5 fases del proyecto. El *qué* y *cuándo* vive acá; el *por qué* de cada decisión está en `change_logs.md`.

**Fase actual:** Fase 5 (Deployment y Escalabilidad) ✅ **completa** en su alcance mínimo — VPS único con Docker Compose, CI, las 3 consultas que no escalaban resueltas, pool de conexiones y healthcheck real. 221/221 tests, 95,5% de cobertura.
**Siguiente:** operar el motor con el back-end real y retomar el backlog post-1.0 cuando haya tráfico que lo justifique.

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

## Fase 4: Síntesis Neutra con IA ✅ COMPLETA

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
- [x] **Tópico por publicación** (`topicos.py`): taxonomía cerrada de 10 categorías, principal + secundario opcional. La sección declarada por cada medio entra al prompt como pista y el modelo decide leyendo los textos — los medios discrepan y esa discrepancia es editorial, no ruido a promediar (migración `eb625bff05fc`)
- [x] **Entrega al backend por webhook** (`webhook_delivery.py`): firma HMAC-SHA256 sobre `timestamp.cuerpo`, un request por síntesis, `POST /deliver` manual con `forzar`. El paso es un **barrido de todo lo pendiente**, así que el job de reintento planificado no hizo falta
- [x] **Contrato documentado para el equipo de back-end**: `specs/webhook_contract.md`, con payload real, validación de firma y semántica de reintentos
- [ ] Validación de neutralidad de lo que devuelve el modelo — **no es detectable por código de forma confiable**; se ataca con el prompt y revisión manual sobre corridas reales

**Entregables:** `src/services/preprocessing.py`, `synthesis.py`, `categorias.py`, `topicos.py`, `alerts.py`, `webhook_delivery.py`, `POST /synthesize`, `POST /deliver`, `specs/webhook_contract.md`.

**Pendiente de validación con el otro equipo:** el webhook está probado contra un receptor mockeado, no contra el back-end real. Falta acordar la URL y el secreto, y hacer la primera entrega punta a punta.

**A vigilar:** el modelo sobreescribió una vez una señal unánime de tópico (los dos medios dijeron `internacional`, él puso `policiales + internacional`). Se sostiene, pero es una sola observación — mirar si se repite.

**Medido en la primera corrida completa con la fase cerrada** (1.354 noticias, 39 s punta a punta, US$ 0,0034): el embudo es angosto —15,5% de las notas entra a un cluster y 3,7% respalda una publicación— y **13 de 17 publicaciones tienen exactamente 2 medios**. No es falla del clustering: 1.116 notas no tienen par en ningún otro medio. La palanca es **sumar medios**, no bajar el umbral. Detalle en `change_logs.md`.

**Cola pendiente de decisión de producto:** El Cronista agrupa solo el 5,7% de sus notas y Ciudad Magazine no participó de ninguna publicación. Con 6 medios el producto es, en los hechos, La Nación contra TN.

**Pendiente de observación** (no bloquea): un cluster de economía juntó inflación y mora, dos hechos distintos pegados por vocabulario compartido. El modelo los separó bien en dos ángulos, pero el agrupamiento no debió unirlos. Una sola observación — mirar si se repite antes de tocar nada.

---

## Fase 5: Deployment y Escalabilidad ✅ COMPLETA (alcance mínimo)

Alcance decidido tras calibración con el usuario: **VPS único con Docker Compose** (no Kubernetes) y **stack mínimo viable**, dado que el proyecto es de desarrollo propio con límite de costos duro y `mission.md` pide explícitamente no resolver problemas de escala que todavía no existen. El roadmap original listaba Kubernetes, Prometheus/Grafana, Redis y rate limiting — una plantilla genérica escrita al arrancar el proyecto, sin relación con el volumen real de uso (interno, sin tráfico externo). Detalle completo de la decisión y de cada elección técnica en `change_logs.md`.

- [x] **CI con GitHub Actions** (`.github/workflows/ci.yml`): job `tests` corre `pytest` con cobertura ≥80% en cada push/PR a `main`, sin servicio de Postgres — verificado que la suite entera (221 tests) pasa contra SQLite en memoria, sin tocar la base real. Job `migraciones`, separado, aplica `alembic upgrade head` contra Postgres+pgvector real (`pgvector/pgvector:pg16`, el mismo que usa `docker-compose.yml`) — es el riesgo real no cubierto por los tests unitarios, y ya mordió una vez en Fase 4.
- [x] **`docker-compose.yml` completo**: servicio `app` agregado junto a `db`, con `depends_on: condition: service_healthy` y migración de Alembic al arrancar (`alembic upgrade head && uvicorn ...` en el mismo `command`). Validado en vivo: build exitoso, `app` esperó a que `db` estuviera healthy, `GET /` respondió `200` con `database: ok`, y al cortar `db` a mano el contenedor pasó a `unhealthy` y `GET /` devolvió `503` — y se recuperó solo al reiniciar `db`, sin reiniciar `app`.
- [x] **Las 3 consultas que no escalaban** (detectadas al cerrar Fase 4): `synthesis.clusters_pendientes`, `search.listar_clusters` y `synthesis.descartar_vencidos_sin_sintetizar` — resueltas con `selectinload` en vez de N+1 o carga de tabla completa. Apareció un cuarto punto no previsto en el diseño original: `descartar_vencidos_sin_sintetizar` seguía siendo N+1 después del `session.commit()`, porque SQLAlchemy expira los atributos de los objetos al commitear y el código volvía a leer `c.id`/`c.noticias` después — se resolvió capturando esos valores antes del commit. Los tres fixes tienen test de no-escalamiento (N chico vs. N grande da el mismo número de queries).
- [x] **Pool de conexiones configurado**: `DB_POOL_SIZE=5` / `DB_MAX_OVERFLOW=10` / `DB_POOL_TIMEOUT=30` / `DB_POOL_RECYCLE=1800`, nuevos en `config.py`, calibrados contra un solo proceso Uvicorn (Dockerfile sin `--workers`).
- [x] **Healthcheck real**: `GET /` verifica conectividad a la base (`SELECT 1` vía `verificar_conexion`) y devuelve `503` si falla, en vez de solo confirmar que Uvicorn responde. Es lo que usa el `HEALTHCHECK` del Dockerfile para que Compose pueda reiniciar el contenedor.

**Validado**: 221/221 tests, 95,5% de cobertura. `docker compose up --build` probado de punta a punta contra Postgres real, incluida la caída y recuperación de la base.

**Diferido a propósito** (no se implementó en esta fase; se retoma cuando haya tráfico real que lo justifique — ver `mission.md`, "no resolver problemas de escala que todavía no existen"):
- **Kubernetes**: un VPS único con Docker Compose alcanza para el volumen de uso actual (interno, sin usuarios externos).
- **Redis / caching**: no hay endpoint con carga de lectura que lo justifique hoy.
- **Prometheus / Grafana**: sin operación 24/7 con guardia, las métricas no tienen quién las mire todavía; los logs + las alertas por mail (`alerts.py`) cubren el caso de uso actual.
- **Rate limiting**: la API no es pública. Se retoma si eso cambia (ver `mission.md`, sección Seguridad).
- **Autenticación pública de la API**: mismo motivo.
- **Scheduler multi-réplica, memoria de embeddings por worker, engine async** (`tech_stack.md`, puntos 1, 4 y 6 de Escalabilidad): quedan abiertos porque siguen sin resolverse — son problemas de *más de una réplica*, y esta fase fija una sola.
- **`agrupar_pendientes` cuadrático** (`tech_stack.md`, punto 9): no entra en esta fase — es un problema de volumen de noticias, no de deployment, y no se observó todavía con los 6 medios actuales.

---

## Backlog post-1.0 (rama nueva, no bloquea Fase 5)

**Segunda vía de ingesta: extracción de artículo por URL para Clarín y Perfil**, vía `trafilatura` (ya reservado en `requirements.txt` desde Fase 2), para los medios cuyo RSS no trae `content:encoded`. Medido y viable — plan de implementación completo en `change_logs.md`, sección "Segunda vía de ingesta: extracción por URL". Diferido a propósito: se retoma recién con Fase 5 cerrada, el back-end integrado y probado, y una versión 1.0 estable etiquetada. No se toca la ingesta mientras compite por atención con cerrar la comunicación real con el back-end.
