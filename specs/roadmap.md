# 🗺️ Roadmap — Sin Ruido

Estado de las 5 fases del proyecto. El *qué* y *cuándo* vive acá; el *por qué* de cada decisión está en `change_logs.md`.

**Estado: versión 1.1.0** (21/08/2026). Las 5 fases completas y **la entrega al back-end probada punta a punta**: primera corrida real contra el receptor (192 publicaciones entregadas de una, y después el pipeline completo ingesta → entrega sin fallos). **552/552 tests, 96% de cobertura, `alembic check` limpio.**

La 1.1.0 cerró **los puntos 1, 2, 10 y 12** del backlog de abajo: segunda vía de ingesta por URL (con Perfil de alta), motor de IA desacoplado, token de operador y logging. El contrato del webhook **no se movió** —sigue en su versión `1`— así que el back-end no ve ninguna diferencia.

**Es 1.1.0 y no 2.0.0 por decisión, no por descuido.** Para quien consume el motor no cambió nada; lo que rompe es la *configuración* de quien actualiza un despliegue existente (las variables `GEMINI_*` ya no se leen, y la migración deja la fila de `modelo_ia` apagada, así que la síntesis no corre hasta activarla). Eso va avisado en grande en el README, en vez de escondido en un número.

**El punto 3 se cerró el 03/09/2026** (el alta de medios la hace el operador): `GET/POST/PATCH /medios`, con sondeo del feed antes de aceptar y baja reversible. El roster deja de ser del repo — aunque `seed_medios.py` sobrevive como datos de ejemplo.

**Siguiente:** el **punto 14** (la app de escritorio del operador), diseñado el 06/09/2026 y arrancando por los cuatro endpoints que le faltan al motor. Detrás quedan el punto 13 (entidades HTML, barato y visible para el lector), el 11 (la URL del webhook la configura el operador) y el 4 (el cuadrático de `agrupar_pendientes`, que todavía no lo dispara nada).

**Pendiente operativo, fuera del código:** elegir dónde se despliega (VPS pago vs. capa gratuita) y armar el `.env` de producción con la `MODELO_API_KEY` real y la `WEBHOOK_URL` del back-end — hoy apunta a `localhost`.

---

## Fase 1: Persistencia y Modelado de Datos ✅ COMPLETA

- ✅ Configuración de PostgreSQL con pgvector
- ✅ Modelos SQLModel (Medio, Noticia, Cluster, Sintesis)
- ✅ ORM y relaciones (Relationship)
- ✅ Verificación básica (`scripts/verify_setup.py`) — 4/4 checks OK
- ✅ Tests (14/14 passing)

**Entregables:** `src/config.py`, `database.py`, `models/`, `docker-compose.yml`, `scripts/verify_setup.py`, tests básicos.

Fixes de compatibilidad con SQLModel 0.0.39 y decisiones de diseño: ver `change_logs.md`.

---

## Fase 2: Ingesta de Noticias ✅ COMPLETA

- ✅ Fuente de datos: RSS directo de cada medio
- ✅ 6 medios activos: La Nación, TN, El Cronista (generales) + Revista Gente, Revista Paparazzi, Ciudad Magazine (espectáculos). Clarín descartado por no traer `content:encoded`
- ✅ User-Agent propio e identificable en las peticiones
- ✅ Limpieza de contenido, deduplicación por `guid`, filtro de notas "en vivo"
- ✅ Scheduler embebido (15 min) + endpoint manual `POST /ingest`
- ✅ Reintentos (`tenacity`) + alerta por mail (`smtplib`) ante fallo de medio
- ✅ `scripts/seed_medios.py`
- ✅ Tests (`test_ingestion.py` + `test_api.py`) — 24/24 pasando
- ✅ Validado contra Postgres + pgvector real (ver `specs/validacion_manual.md`)

**Entregables:** `src/services/ingestion.py`, `POST /ingest`, scheduler automático, `scripts/seed_medios.py`.

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
- [x] **Tópico por publicación** (`topicos.py`): taxonomía cerrada de 10 categorías. La sección declarada por cada medio entra al prompt como pista y el modelo decide leyendo los textos — los medios discrepan y esa discrepancia es editorial, no ruido a promediar (migración `eb625bff05fc`). **Rediseñado en Fase 5**: `topico`/`topico_secundario` (principal + secundaria) pasaron a `topicos`/`subtopicos` (categorías pares + recorte fino de 16 subtópicos en 5 categorías, con la jerarquía garantizada por código). Ver `change_logs.md`.
- [x] **Entrega al backend por webhook** (`webhook_delivery.py`): firma HMAC-SHA256 sobre `timestamp.cuerpo`, un request por síntesis, `POST /deliver` manual con `forzar`. El paso es un **barrido de todo lo pendiente**, así que el job de reintento planificado no hizo falta
- [x] **Contrato documentado para el equipo de back-end**: `specs/webhook_contract.md`, con payload real, validación de firma y semántica de reintentos
- [x] **Copy para redes sociales** (`AnguloGenerado.relevancia_social` + tabla `PublicacionRedes`): en la misma llamada de síntesis, Gemini marca si el ángulo es de relevancia nacional y, solo en ese caso, redacta un párrafo corto para Twitter/Facebook y hasta 5 hashtags. Tabla aparte (no columnas en `Sintesis`, no es 1:1) y sin llamada extra a la API — el costo de Gemini lo domina la entrada, no la salida. No se congela como título/tópicos (se actualiza en cada resíntesis) pero tampoco se retracta si una resíntesis posterior deja de marcarlo relevante. Viaja como `sintesis.publicacion_redes` (nullable) en el mismo payload del webhook, sin pipeline de entrega aparte. Ver `change_logs.md`
- [ ] Validación de neutralidad de lo que devuelve el modelo — **no es detectable por código de forma confiable**; se ataca con el prompt y revisión manual sobre corridas reales

**Entregables:** `src/services/preprocessing.py`, `synthesis.py`, `categorias.py`, `topicos.py`, `alerts.py`, `webhook_delivery.py`, `POST /synthesize`, `POST /deliver`, `specs/webhook_contract.md`.

**Pendiente de validación con el otro equipo:** el webhook está probado contra un receptor mockeado, no contra el back-end real. Falta acordar la URL y el secreto, y hacer la primera entrega punta a punta.

**A vigilar:** el modelo sobreescribió una vez una señal unánime de tópico (los dos medios dijeron `internacional`, él puso `policiales + internacional`). Se sostiene, pero es una sola observación — mirar si se repite.

**Límite conocido de `publicacion_redes`, decidido no resolver por ahora:** `relevancia_social` solo se evalúa cuando un cluster tiene cobertura nueva. Un hecho que ya cerró no vuelve a pasar por Gemini nunca, así que se queda sin `publicacion_redes` para siempre aunque sea claramente relevante — caso real: el fallecimiento de Jorge Messi (síntesis 23) sigue en `null`. Se evaluó un backfill puntual y se descartó a propósito; documentado para retomar si hace falta de verdad. Ver `change_logs.md`.

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

## Backlog post-1.0 — priorizado

Con Fase 5 cerrada, esta es la lista accionable de próximos pasos, priorizada. La fuente completa de límites conocidos del stack sigue siendo `tech_stack.md`, sección "Arquitectura y Escalabilidad — Puntos de quiebre a vigilar" (11 puntos, la mayoría ya ✅ resueltos); acá solo entra lo que queda abierto y es candidato real a trabajarse a continuación. Kubernetes, Redis, Prometheus/Grafana, rate limiting de la API y autenticación pública **no están en esta lista a propósito** — siguen diferidos sin fecha (ver "Diferido a propósito" en Fase 5, arriba) porque son problemas de tráfico público que el proyecto todavía no tiene, no de sumar medios.

Regla que ordena toda la lista, la misma de siempre (`mission.md`): **medir antes de resolver**. Ningún punto de acá se ataca preventivamente — se retoma cuando el síntoma aparece con datos reales, no antes.

### 1. Segunda vía de ingesta: extracción por URL (Perfil) — el lever directo para sumar medios

Vía `trafilatura` (ya reservado en `requirements.txt` desde Fase 2), para los medios cuyo RSS no trae `content:encoded`. Medido y viable, plan de implementación completo en `change_logs.md`, sección "Segunda vía de ingesta: extracción por URL". Es el ítem de mayor prioridad de la lista porque es literalmente el mecanismo para sumar medios, que es el eje de todo lo demás acá.

**✅ COMPLETO** (18-20/08/2026, branch `mejoras-post-1.0`). Las dos precondiciones quedaron cumplidas:

- *"Se retoma con el back-end integrado y probado"* — la corrida del 18/08 entregó 15/15 síntesis, 221/221 acumulado, cero rechazos.
- *"No entra en la app hasta validar que suma pares reales"* — medido: **14 pares reales por día** (contra un piso de 3 para justificar el trabajo), 30-35% de sus artículos parean con nuestro corpus, y **0 fallos de extracción sobre 120 artículos**. Detalle completo, incluida la auditoría manual de los pares, en `change_logs.md`, "Etapa 0: la medición que levanta el candado".

Etapas: **0 ✅** medición · **1 ✅** esquema (`Medio.extraer_por_url` + migración `b4f1a9d27c30`) · **2 ✅** `services/extraccion.py` con piso de caracteres y política de reintentos propia · **3 ✅** la costura, con la extracción **fuera de la transacción** · **4 ✅** márgenes del scheduler y alerta ante corridas perdidas · **5 ✅** alta de Perfil.

**Clarín quedó afuera, no solo pospuesto.** La revisión de términos de uso del 19-20/08 (ver punto 3 y `change_logs.md`) mostró que retiene el cuerpo del feed a propósito —su licencia cubre solo "títulos y/o links"—, así que extraerlo por URL cruzaría una línea que el medio trazó. Perfil sí licencia "el contenido" y entró: seed con un feed general y `extraer_por_url=True`, verificado en producción (47 nuevas, 0 fallos de extracción, `robots.txt` pedido una vez).

- *Medido en la etapa 4*: una corrida usa el **1,2% del ciclo** sin síntesis pendiente y el **23%** con un backlog de 24 ángulos. **La síntesis es el 91% del costo**; el resto del pipeline es plano en ~10 s. Como el trabajo de síntesis por día lo fija la cantidad de noticias y no el intervalo, **`INGEST_INTERVAL_MINUTES` hay que decidirlo por frescura, no por capacidad**: ya es variable de entorno y cada corrida loguea su utilización.

- *Medido al planificar la etapa 3*: `trafilatura` es el **1,3%** del costo por artículo (16,9 ms) — no es el punto de inflexión al sumar medios. El 75,7% es la pausa de cortesía propia, y ahí está el techo: **~20 medios** con el diseño secuencial de hoy. Paralelizar entre medios (serie dentro de cada uno) lo vuelve independiente de N; disparador para hacerlo: **pasar los ~10 medios**. Detalle en `change_logs.md`.

**Hallazgo que abre un ítem nuevo**: la medición destapó dos clusters mal armados de El Cronista ("el blob de economía" de Fase 3) que hoy no se publican solo porque les falta un segundo medio. No los causa esta vía, pero esta vía los volvería publicables. Ver el hallazgo del centroide en `change_logs.md`.

### 2. Desacoplar el motor de IA — que cada operador elija su modelo

Hoy `synthesis.py` habla Gemini directo. La idea es que quien despliegue el motor elija su proveedor **conociendo sus pros y contras**, incluido correr un modelo local — con lo que los cuerpos de los artículos no salen de la máquina.

**El acoplamiento es chico y está medido**: de las 884 líneas de `synthesis.py`, lo específico de Gemini son `get_cliente()` y `llamar_modelo()`, unas 50 líneas. El contrato ya es prácticamente una interfaz de proveedor: `(prompt: str) -> RespuestaSintesis`. Todo lo demás —prompt, esquema, validaciones anti-alucinación, lógica de clusters— es agnóstico.

Lo difícil no es la estructura sino cuatro detalles: la **salida estructurada** se pide distinto en cada proveedor (`response_schema` en Gemini, `json_schema` en OpenAI, tool-use en Anthropic, `format` en Ollama; `model_json_schema()` sirve de denominador común); el **bloqueo por filtros** llega en campos distintos y cada adaptador tiene que normalizarlo a `SintesisBloqueada`; **`thinking_config` no tiene equivalente** fuera de Gemini; y sobre todo **el prompt está calibrado contra Gemini**, así que cada proveedor necesita su propia corrida de validación de calidad. Eso último es medición, no programación, y es el grueso del trabajo.

**✅ COMPLETO.** Etapas: **1 ✅** la rebanada vertical completa con el adaptador `openai_compatible`, la tabla `modelo_ia`, el alta con sondeo y `Sintesis.modelo_usado` · **2 ✅** Gemini nativo, la credencial única y las opciones por adaptador · **3 ❌ no se implementa** (ver abajo) · **4 ✅** retirado el camino histórico.

Con la etapa 4, **el motor ya no tiene proveedor preferido**: sin fila activa en `modelo_ia` la síntesis no corre y lo dice.

**Al actualizar hay un paso manual, y es a propósito.** La migración `5f80e67d5404` convierte la configuración de entorno del despliegue en una fila, pero **la deja apagada**: la regla es que ninguna migración elige proveedor. Hay que prenderla con `PATCH /modelos/{id}?activo=true`, y **renombrar la credencial en el `.env`**: `GEMINI_API_KEY` pasa a `MODELO_API_KEY`, mismo valor. Si falta, el motor no sintetiza y lo dice — no adivina.

**Prender un modelo apaga a los demás.** Con una sola credencial, dos proveedores prendidos es un estado que no se puede usar; y sin exclusividad dos filas empataban en `prioridad` y desempataba el `id`, o sea ganaba la más vieja en silencio.

### La etapa 3 (Anthropic nativo) no se implementa — decidido el 21/08/2026

**Anthropic se usa con el adaptador `openai_compatible`** y `base_url=https://api.anthropic.com/v1`. El valor `Adaptador.ANTHROPIC` queda reservado en el enum y `construir` lo rechaza con un mensaje que explica esto mismo.

**⚠️ El supuesto que sostiene esto no está verificado.** Está comprobado que la capa de compatibilidad de Anthropic **ignora `response_format` en silencio**, así que el sondeo tiene que caer a `tools`. Que `tools` funcione ahí **nunca se probó**: el proyecto no tiene una credencial de Anthropic con crédito. Si alguien la consigue, esa medición cuesta menos de un centavo y es lo primero que hay que hacer — si `tools` no sirve, Anthropic no entra por ningún lado y la etapa 3 pasa de opcional a necesaria.

**Lo que se pierde**, y es más de lo que decía la versión anterior de este documento:

- **Salida estructurada nativa** vía `output_config.format`, que da JSON válido por construcción igual que `response_schema` en Gemini. Por `tools` el esquema es una guía, no una garantía.
- **`output_config.effort`** (`low` a `max`) como palanca de costo.

> Corrección: este roadmap decía que el nativo aportaría `thinking.budget_tokens`. **Eso ya no existe** — está removido en los modelos actuales de Anthropic y devuelve 400. Lo reemplazaron `thinking: {type: "adaptive"}` y `output_config.effort`.

**Por qué no se construye igual.** No es solo que no haya key para testear: **no hay presupuesto para correrlo.** Con el prompt real medido (18.447 tokens de entrada, ~2.200 de salida) y unas 20 síntesis por día, Anthropic sale entre **US$18 y US$88 por mes** según el modelo, contra **US$0** del tier gratuito de Gemini que corre hoy. Construir un adaptador que el proyecto no puede pagar es agregar una dependencia y ~250 líneas que nadie va a ejecutar.

**Cuándo se revisa**: si el proyecto consigue una credencial de Anthropic, o si alguien que despliega el motor la tiene y reporta que el compatible no le alcanza. Hasta entonces es una limitación documentada, no una tarea pendiente.

La etapa 2 midió lo que estaba pendiente: **los tres caminos a Gemini son equivalentes** —8,05 s el nativo, 8,55 s el compatible, 8,98 s el histórico sobre el mismo prompt de 18.447 tokens, mismos 4 ángulos en las doce corridas— y la palanca de razonamiento del nativo **funciona y se paga**: de 0 tokens con `LOW` a 6.267 con `HIGH`, con el triple de latencia.

**El hallazgo que reencuadró la etapa 4**: en seis rondas, el camino histórico tardó **486 segundos** en una y no falló, porque `_llamar_gemini` **no tenía timeout** — era la única llamada sin límite de tiempo del pipeline, y `llamar_modelo` la reintentaba tres veces sobre un ciclo de 15 minutos. Retirarlo dejó de ser higiene: ahora toda llamada pasa por un adaptador, y todo adaptador tiene techo.

Detalle de las decisiones en `change_logs.md`. Dos que conviene tener a mano:

- **La credencial es una sola variable, `MODELO_API_KEY`.** Cambiar de proveedor es cambiar su valor, no agregar otra. Tener dos proveedores vivos a la vez es el punto 6-bis. Como el nombre ya no distingue proveedores, **activar un modelo sondea contra él** en vez de mirar el entorno.
- **`POST /modelos` no es un CRUD**: sondea antes de aceptar y descubre solo cómo pedirle estructura al proveedor. Medido: la capa de compatibilidad de Anthropic responde 200 e **ignora `response_format` en silencio**.

> ⚠️ **Los tres endpoints de `/modelos` son los primeros que aceptan una URL arbitraria para que el motor la llame.** Además de la autenticación del punto 10, hay puesto: enum cerrado de adaptadores, prefijo obligatorio para `api_key_env`, validación de `base_url` (solo http/https, sin credenciales embebidas, sin link-local), respuestas que no publican `api_key_env` ni `base_url`, y errores que no reflejan el cuerpo del proveedor.
>
> **Nada de eso reemplaza la autenticación**, que llegó en el punto 10: con `API_TOKEN` definido, estos endpoints la exigen. **Sin token la API queda abierta a propósito** —es la elección de quien despliega— y ahí sigue valiendo que quien pueda hacer POST puede apuntar `base_url` a su propio servidor y quedarse con la key de IA del operador.

### 3. El alta de medios la hace el operador, no el repo ✅ COMPLETO (03/09/2026)

**Cerrado el 03/09/2026.** Hasta acá `scripts/seed_medios.py` traía siete medios argentinos hardcodeados, o sea que **el repo aceptaba sus términos de uso en nombre de quien lo desplegara**. La revisión del 19-20/08 lo dejó a la vista: los términos varían muchísimo entre medios —Clarín licencia solo títulos y links, Perfil pide links de vuelta, Ámbito no tiene contrato de reuso, La Izquierda Diario reserva TDM en su `robots.txt`— y cuál es aceptable depende del uso que le dé cada operador.

**Qué se construyó.** Tres endpoints: `GET /medios`, `POST /medios` y `PATCH /medios/{id}?activo=`. El modelo `Medio` sumó `idioma`, `pais` y `logo_url`, y su `nombre` pasó a ser único.

**El alta no es un CRUD.** Antes de guardar nada sondea los feeds e informa qué hay del otro lado: cuántos items traen, cuántos con `content:encoded`, qué ventana temporal cubren, y qué dice el `robots.txt`. Así el alta pasa de *"registrá esto"* a *"esto es lo que encontramos, decidí vos"*.

Y **bloquea lo inservible pero informa lo opinable**, que fue la decisión de diseño principal. Un feed que no responde, no parsea o no trae un item utilizable devuelve 422: está roto y ninguna decisión lo arregla. Que el medio retenga el cuerpo, que su `robots.txt` sea restrictivo o que la ventana parezca archivo viajan en `avisos` y **no impiden el alta**, porque son cosas sobre las que el operador tiene algo que decir — y dictaminar por él sería volver a cometer el error que este punto vino a corregir.

**`extraer_por_url` la sigue decidiendo el operador.** El sondeo detecta si el feed trae el cuerpo y lo informa, pero no prende la bandera solo: marca los medios donde el motor va a buscar a la página el cuerpo que el medio *eligió no publicar*, y cruzar esa línea es justamente la decisión que este backlog devuelve.

**Deshabilitar no es borrar, y no existe DELETE.** `PATCH /medios/{id}?activo=false` deja el medio con todo lo suyo —noticias, clusters, síntesis entregadas— y solo hace que la ingesta deje de traer sus feeds; se puede volver a habilitar cuando sea. No hay borrado porque `Noticia.medio_id` es `NOT NULL` con clave foránea sin cascada: borrar un medio que ya ingirió violaría la restricción, y forzarlo se llevaría puestas noticias que quizá ya se entregaron al back-end.

Hallazgo que achicó el punto: **`Medio.activo` existía desde la Fase 1 y `ingerir_todos_los_medios` ya filtraba por él**. La semántica de la baja estaba implementada en el pipeline desde el principio; lo único que faltaba era el endpoint que diera vuelta la bandera.

**El SSRF se cerró revisando, no copiando.** El patrón estaba en `proveedores.base.validar_base_url`, pero ese bloquea *solo* link-local a propósito —un modelo de IA en `localhost:11434` es el caso que aquel backlog existe para habilitar—. Un medio de noticias en `127.0.0.1` no tiene uso legítimo, así que `medios.REDES_PROHIBIDAS` suma loopback y los rangos privados. Hay un test que fija la divergencia para que nadie "unifique" los dos validadores. `url_base` pasa por el mismo filtro: el sondeo le pide su `robots.txt`.

**Verificado**: 629 tests (77 nuevos), `ruff` limpio, `alembic check` contra Postgres, **14 mutaciones y 13 detectadas** (la restante es capa redundante, anotada como tal), y una prueba punta a punta contra la red real —el 404 de `/feed/internacionales` de Perfil rechazado, los intentos de SSRF rechazados, un alta real con su sondeo (20 items, 0 con cuerpo, ventana 30,9 h) y el ciclo completo de deshabilitar y rehabilitar—.

**Lo que quedó afuera, a propósito**: retirar el roster del repo (obliga a resolver la migración de las instancias que ya corren con siete medios cargados) y editar los datos de un medio ya cargado (no hay `PATCH` de metadatos, así que los siete existentes se quedan con `pais` en `None`). `scripts/seed_medios.py` sobrevive como **datos de ejemplo**, con su bloque de comentarios reescrito para conservar el conocimiento medido que el alta por API no puede redescubrir sola.

**Se atacaron los endpoints antes de commitear**, en vez de revisarlos de a ojo. Aparecieron tres agujeros reales y se cerraron: SSRF por redirect (`follow_redirects=True` dejaba a httpx irse sola al `Location` sin revalidar), el mismo agujero en `leer_robots`, y una IPv4 escondida en IPv6 (`::ffff:127.0.0.1`) que atravesaba el filtro — verificado en `python:3.12-slim` porque en Windows no reproducía.

Después se atacaron **los catorce endpoints**, no solo los nuevos, y de ahí salieron tres tandas más de arreglos. La 1 definió `API_TOKEN`, que cerró de una el CSRF sobre los endpoints caros. La 2 acotó a dónde puede apuntar `base_url`, que le entregaba la credencial de IA a cualquier host que le nombraran. La 3 cerró los cuatro hallazgos menores que quedaban: la amplificación por `feeds_rss` sin techo ni dedup (500 copias de una URL daban 501 pedidos reales), los campos sin cota con `logo_url` aceptando `javascript:` —XSS almacenado esperando a la aplicación de escritorio—, el 500 por `id` fuera de rango en los dos `PATCH`, y el `limite` negativo que `/vectorize` informaba como si lo hubiera medido. **No queda ningún hallazgo del ataque sin cerrar**; el detalle de cada tanda está en `change_logs.md`.

Ver `change_logs.md`, "Backlog punto 3".

### 4. `agrupar_pendientes` cuadrático + índice de pgvector — el primer síntoma real al sumar medios

`tech_stack.md`, puntos 9 y 3. Compara cada noticia suelta contra todas las demás de la ventana abierta; medido: 3,6 s con ~200 sueltas, proyectado ~14 s con 400 y cerca de un minuto con 800. Con 7 medios no se nota. La salida es acotar candidatos con el índice HNSW/IVFFlat de pgvector (hoy inexistente a propósito, porque nada lo necesita) en vez de comparar contra todos — los dos puntos van juntos porque uno es la causa y el otro la solución.

**No se implementa todavía.** Se vigila el tiempo de la corrida de agrupamiento (ya logueado) a medida que se sumen medios vía el punto 1, y se ataca cuando el número se acerque a los segundos que empiezan a competir con el ciclo de 15 minutos del scheduler — no antes.

### 5. Eventos de varios días generan clusters sucesivos — decisión de producto, no de escala técnica

`tech_stack.md`, punto 10. Un cluster cierra a las 12 h; una historia larga (la muerte de Jorge Messi cubrió varios días) produce publicaciones sucesivas que pueden solaparse. Con más medios hay más cobertura y más eventos largos, así que el riesgo crece con el volumen — pero la solución no es técnica (¿son hechos distintos de verdad, o el mismo hecho que el producto debería seguir mostrando junto?), es una conversación con el equipo de producto/back-end sobre qué comportamiento quieren. Revisar con síntesis reales a la vista antes de proponer un diseño.

### 6. Rate limit del proveedor al sumar medios

`tech_stack.md`, punto 5 (el costo ya está resuelto — precálculo, no on-demand — pero el rate limit sigue vigente). Hoy una corrida sintetiza todos los clusters publicables de una vez (medido: hasta 26) y el proveedor limita por minuto; ya hay backoff con `tenacity`. Con más medios, más clusters publicables por corrida, más chance de pegarle al límite seguido en vez de ocasionalmente. Vigilar la tasa de reintentos por rate limit en los logs a medida que se sumen medios; si se vuelve frecuente, ahí se decide entre escalonar la síntesis o pasar a un tier con más cupo.

**Se cruza con el punto 6-bis**: la cadena de fallback es una de las salidas posibles a este problema, y la otra mitad de la respuesta.

### 6-bis. Multimodelo: un modelo por cluster, y una cadena que no pierde la corrida ✅ COMPLETO, PARCIAL (05/09/2026)

**Abierto el 21/08/2026** al decidir la credencial única de la etapa 2 del punto 2; cerrado en su mitad reactiva. Detalle completo en `change_logs.md`.

**El reencuadre que lo destrabó.** Este punto quedó frenado por un argumento correcto pero de alcance más chico del que parecía: *"para que la cadena sirva hay que decidir cuánto mandarle a cada proveedor según los créditos que le queden"*. Eso es cierto del **reparto proactivo** de carga, no del **fallback reactivo** — caer al siguiente cuando el primero falla no necesita saber cuánto crédito queda. Se hizo lo reactivo, y el bloqueo no aplicaba.

**Qué quedó andando:**

- `POST /modelos` acepta `api_key_env`, acotado a `MODELO_API_KEY` o la forma con sufijo. Es el "exponer un campo en el alta" que este punto anticipaba, y la restricción sigue cerrando la primitiva de exfiltración de la tanda 2.
- `modelos.cadena_de_modelos`: el activo encabeza y detrás van los suplentes **con credencial propia**. Compartir la variable del titular es compartir su cuota, así que un suplente que la comparte no serviría de suplente.
- `sintetizar_pendientes` recorre esa cadena por cluster, con **cortocircuito**: dos fallos seguidos del mismo modelo lo sacan por lo que queda de la corrida. Sin eso, con la cuota agotada cada cluster paga sus 3 reintentos de `tenacity` antes de caer al suplente.
- `POST /synthesize?modelo_id=` y `POST /clusters/{id}/synthesize?modelo_id=`. **El scheduler no cambió**: sigue llamando sin parámetros.

**Lo que queda deliberadamente afuera**: el reparto proactivo de cupo (la mitad pesada, y sigue sin hacer falta), los hilos por modelo (ninguno de los dos modos de uso corre dos modelos a la vez, y la síntesis usa el 23% del ciclo), y la interfaz de escritorio, que se construye cuando exista la app.

**Sin probar contra dos proveedores reales, y ahora se sabe por qué** (05/09/2026). La cadena está verificada contra mocks y con mutación. Se intentó probarla de verdad y los dos candidatos fallaron por motivos ajenos al motor:

- **`groq-qwen`**: credencial válida y host declarado, pero el tier gratuito topea en **1000 tokens de salida por minuto** para `qwen/qwen3.6-27b`. Ni siquiera el pedido mínimo del sondeo entra. Requeriría plan pago.
- **`gemini-3.8-flash`**: `503 UNAVAILABLE` en 3 de 3 intentos con dos credenciales distintas. Aislado con un control —la misma credencial nueva sondeó bien contra `gemini-3.5-flash-lite`—, así que es el modelo y no la cuenta. Quedó apuntando a `MODELO_API_KEY_GEMINI2`, listo para cuando Google libere capacidad.

**Lo que quedó abierto de la verificación de hallazgos** (05/09/2026), confirmado con sondas pero sin arreglar:

- ~~**Amplificación de costo**~~ ✅ **cerrada el 05/09/2026**: `POST /clusters/{id}/synthesize` exige material nuevo salvo `?forzar=true`. N llamadas idénticas pasaron de N a 1 llamada al proveedor. Detalle en `change_logs.md`. **Quedan sus dos efectos colaterales**, que son decisiones aparte:
  - ~~**El reset de `enviado_backend`/`intentos_envio`**~~ ✅ **evaluado y cerrado sin cambios el 05/09/2026**. Es cierto que reinicia el presupuesto de reintentos, pero **está acotado a las 12 h de `HORAS_CLUSTER_ABIERTO`**: cerrado el cluster no entran noticias nuevas, no hay re-síntesis, el contador llega al techo y la alerta sale. Además la alarma es poblacional, el costo es cero (son requests al back-end propio, no al proveedor de IA) y darle presupuesto nuevo a un payload nuevo es lo correcto. Lo único que se pierde es observabilidad. Razonamiento completo en `change_logs.md`.
  - ~~**No mira el estado del cluster**~~ ✅ **cerrado el 05/09/2026**, y con una corrección: la afirmación original —que un `descartado` podía publicarse contradiciendo la regla de las dos voces— **era falsa**, y lo era por una sonda que construía un estado imposible (un descartado con dos medios). Con uno realista las defensas aguantan y no se crea ninguna fila. Lo que sí quedaba era que se pagaba una llamada al proveedor para no obtener nada; ahora el endpoint corta antes si el cluster no llega a `MIN_MEDIOS_CLUSTER` medios distintos, incondicionalmente. Detalle en `change_logs.md`.
- **Qué significa `activo=False` con multimodelo**: un modelo que el operador apaga —reemplazándolo por otro— vuelve solo a la cadena como suplente si tiene credencial propia, contradiciendo el docstring de `activar_modelo`. Es una decisión de producto, no un parche.
- **No hay forma de cambiar `api_key_env` de una fila ya creada**: el `PATCH` solo acepta `activo`. Hoy se resuelve por SQL directo.
- **Una precondición no escrita**: `sintetizar_pendientes(session, modelo)` no tolera un `ModeloIA` cargado antes de pasos que commitean (queda expirado, y el `expunge` interno lo desprende sin valores). Ningún camino de producción la pisa hoy, **pero la app de escritorio de este mismo punto sí la pisaría**.

**Lo que sí habría que revisar**: `PATCH ?activo=true` sondea contra el proveedor justamente porque con credencial única la variable no dice de quién es la key. Con nombres por proveedor esa comprobación vuelve a poder ser barata — pero conviene medir antes de aflojarla.

### 7. Solo si se suma más de una réplica — no lo dispara sumar medios por sí solo

Tres puntos de `tech_stack.md` (1, 4 y 6: engine de BD síncrono, scheduler embebido, modelo de embeddings en memoria por worker) comparten la misma condición: importan si el despliegue pasa de una réplica a varias, no por la cantidad de medios que se ingieran con la réplica única actual. Fase 5 fijó a propósito un solo proceso Uvicorn sin `--workers`; mientras eso no cambie, estos tres quedan correctamente diferidos.

### 8. Purga de cuerpos — borrar el texto ajeno una vez que cumplió su función ✅ COMPLETO, PARCIAL (04/09/2026)

**Cerrado para las noticias huérfanas; la población agrupada queda afuera a propósito.** `services/purga.py` borra `contenido_limpio` —nunca la noticia— de lo que nunca formó cluster y ya venció su ventana. Corrido de verdad contra la base real: **22 MB de texto ajeno → 6,58 MB**, 4.083 noticias purgadas (el MB liberado está subestimado: la primera versión medía caracteres y no bytes, corregido después — ver más abajo). Detalle completo, incluida la medición que decidió el corpus de TF-IDF y la verificación en vivo con backup previo, en `change_logs.md`.

**Revisión independiente antes de commitear** (mismo día): encontró que la condición de purga no exigía que la noticia estuviera vectorizada —cerrado, sin costo sobre lo ya purgado— y que 238 de las 4.083 filas purgadas eran notas sin hecho (opinión, recetas, horóscopo) que `categorias.py` promete mantener disponibles para el back-end. Se decidió no revertirlas —el back-end nunca leyó ese campo— pero queda anotado como una decisión de alcance que se tomó por default y no a propósito. Detalle completo en `change_logs.md`.

Sobrevive todo lo demás —título, URL, fecha, medio y el `embedding`—, así que `GET /search` no se entera y las noticias siguen existiendo como registro.

**Lo que queda deliberadamente afuera de esta tanda**: los clusters cerrados, sintetizados y entregados, que son candidatos por el mismo argumento de antigüedad. Es una segunda población con su propia condición de seguridad —la re-síntesis: `clusters_pendientes` puede reabrir un cluster viejo si llega material nuevo de un medio que ya estaba— y mezclarla acá habría resuelto dos problemas con una sola comprobación. Queda como tanda aparte, sin medir todavía cuánto libera.

**El costo real de purgar** no es perder el texto para el producto —ya cumplió— sino **no poder revectorizar si algún día se cambia `EMBEDDING_MODEL`**: habría que re-ingerir, y lo que salió de la ventana del feed no vuelve.

Vale anotar el otro motivo por el que existe este punto, que no es técnico: reduce cuánto texto de terceros conservamos y por cuánto tiempo. Ver la revisión de términos de uso en `change_logs.md`.

### 9. El mail de alertas lo elige el operador, no el `.env` del repo ✅ COMPLETO (12/09/2026)

**Y encontró algo que no estaba buscando: las alertas del motor no le llegaban a nadie desde hacía meses.**

El punto se abrió para que la casilla de destino se pudiera cambiar sin redeploy. Antes de escribir nada se revisó el estado real y apareció el hallazgo: **cero líneas de envío en todo el log del contenedor**, ni exitosas ni fallidas. El motor tiene **nueve puntos de llamada** a `enviar_alerta` —feeds caídos, síntesis vencidas, entregas agotadas, corridas largas— y ninguno llegaba. La casilla que se usaba la había deshabilitado su proveedor, y un envío fallido sólo deja un `logger.error` que nadie mira.

Eso reordenó el punto. Lo importante no era *quién elige la casilla* sino **si el mail sale**: configurar un destino que no funciona no arregla nada, sólo mueve dónde se escribe la dirección.

**Qué quedó construido:**

- **`ConfiguracionAlertas`**, una fila con la lista de destinos, con la misma forma que `ConfiguracionEntrega` del punto 11.
- **`GET`/`PATCH /alertas` y `POST /alertas/probar`**, los tres con **token siempre**.
- **`enviar_alerta` lee la fila**, con `ALERT_EMAIL_TO` como red.
- **Tarjeta en Ajustes** que muestra *"Último envío"* **arriba de las direcciones**, porque es el dato que faltaba.
- La migración `d4e81a37c209` **siembra desde el `.env`**, así que un despliegue existente no se queda sin avisos al actualizar.

**Los tres cuidados del backlog, resueltos:**

1. **Token siempre**: sí, y el motivo es más fuerte que en el punto 11. Desviar las alertas es **apagarlas** —quien las recibe deja de recibirlas y no se entera— y convierte al motor en un emisor de mails con las credenciales SMTP del operador.
2. **Verificar la dirección**: se decidió **no** validar contra el RFC 5322. Ninguna regexp corta lo describe, y un validador que rechaza direcciones legítimas es el peor error posible acá — dejar sin avisos a quien la escribió bien. Se ataja lo que seguro no es una dirección (sin arroba, con espacios, sin punto en el dominio) y el resto lo dictamina el único juez que importa: el servidor de correo, vía el botón de probar.
3. **`SMTP_*` no pasa al operador.** No era una pregunta abierta sino la regla del proyecto: las credenciales viven en el entorno, como `WEBHOOK_SECRET` y `ModeloIA.api_key_env`. Cifrarlas en la base se evaluó y se descartó — la clave de cifrado terminaría en el `.env` igual, o sea que mueve el problema. La división sigue la frecuencia de uso: la cuenta emisora se configura una vez al instalar, los destinatarios cambian seguido.

**Dos cosas que el backlog no pedía y son las que dieron valor:**

- **Varios destinos**, por el caso que ya ocurrió: si la única casilla se da de baja, te quedás ciego sin enterarte. Tope de 5 — esto son las alertas de un motor, no una lista de difusión.
- **El botón de probar**, que es lo que convierte "hay un mail configurado" en "el mail sale". Detectó el `535 BadCredentials` **en dos segundos**. Sin él, la única forma de saberlo era esperar a que algo se rompiera y notar que no llegó el aviso — o sea, enterarse de que las alertas no andan justo cuando hacían falta.

**El STARTTLS era un bloqueo real, no sólo de testing.** `alerts.py` llamaba a `smtp.starttls()` **siempre**, así que el motor no podía hablar con un servidor plano — ni un capturador de prueba ni un relay SMTP en localhost, que es una configuración normal. Ahora son tres ramas, y la del medio es la que importa: **sin TLS y con credenciales, el motor se niega a mandar**. Perder una alerta es malo; poner usuario y contraseña en claro en la red es peor y no se deshace. Cuatro mutaciones, todas cazadas.

**Verificado punta a punta, y desde afuera del motor.** Con un capturador local (`docker-compose.correo-de-prueba.yml`, que va aparte para que no se arrastre a producción) se comprobó que el mensaje **llega completo** —remitente, destinatario, asunto y cuerpo— mirando el buzón, no preguntándole al motor si le fue bien.

**Lo que queda afuera y no es código:** contra Gmail las credenciales siguen rechazadas. Se comprobó que la contraseña puesta **no tiene forma de contraseña de aplicación** (16 caracteres, pero no 16 letras minúsculas). Hace falta generar una en `myaccount.google.com/apppasswords`. La tarjeta de Ajustes ahora lo explica cuando una prueba falla, y aclara lo que más confunde: **el rechazo es de la cuenta que envía, no de la que recibe** — el motor se autentica antes de que el destinatario entre en juego.

### 10. Token de operador para la API ✅ COMPLETO (21/08/2026)

**Cerrado el 21/08/2026.** Estaba en tres lugares como prerrequisito y en ninguno como tarea: el punto 3 lo pedía "desde el diseño", el punto 9 decía que se resolvería junto con el 3, y los comentarios del código apuntaban a "el punto 9" — que es el mail de alertas. Una deuda sin acreedor.

La confusión era de vocabulario. Lo que este roadmap difirió (arriba, en Fase 5) es **autenticación pública**: usuarios, roles, rate limiting — y eso sigue diferido, porque es un problema de tráfico que el proyecto no tiene. Lo que hacía falta es otra cosa y mucho más chica: **un token de operador**.

**Qué se construyó.** `API_TOKEN` en el entorno; con la variable definida los once endpoints piden `Authorization: Bearer`, menos `GET /` (el healthcheck de Docker) y la documentación. Sin la variable la API queda abierta y el motor lo avisa en cada arranque.

**Que sea opcional es la decisión de diseño**, no una concesión: el motor es software libre que otros despliegan, y cómo lo exponen es asunto suyo. Un solo interruptor, del lado del operador, sin comportamiento que dependa del entorno.

**Lo que el inventario destapó**, y que ninguna nota anterior decía:

- **`POST /synthesize` es un ataque financiero.** Cuesta plata por invocación, y el gasto en APIs es el límite duro del proyecto.
- **`POST /ingest` es reputacional.** Hace que el motor golpee todos los feeds **con la IP y el User-Agent del operador**, contra medios cuyos términos de uso revisamos con cuidado.
- **El `docker-compose.yml` contradecía la mitigación documentada**: ligaba `8000:8000`, o sea `0.0.0.0`. La nota decía "desplegala donde solo llegue el operador" mientras el archivo publicaba la API entera. Ahora liga a `127.0.0.1`.
- **Nada externo consume esta API.** El back-end recibe por push y no hace polling, así que proteger todo no rompió ninguna integración — eso hizo la decisión barata.

**Lo que NO cubre**: no es TLS ni firewall. El token viaja en claro si la API se expone por HTTP sin proxy adelante.

### 11. La URL del webhook la configura el operador, no el `.env` ✅ COMPLETO (08/09/2026)

**Cerrado el 08/09/2026**, dentro del punto 14: la pantalla de Ajustes de la cabina no podía existir sin esto. El destino vive en la tabla `configuracion_entrega` (una fila, migración `a1f27c93b8e0`) y se cambia con `GET`/`PATCH /entrega`. `WEBHOOK_URL` **dejó de ser un campo de `Settings`**: el `.env` sólo lo sembró una vez, en la migración, para no romperle la entrega a un despliegue que ya funcionaba.

El `WEBHOOK_SECRET` se quedó en el entorno, como estaba previsto. La distinción tiene contenido y no es prolijidad: la URL es *a dónde* va el producto, el secreto es *lo que prueba que el producto es nuestro*. Robar la primera desvía; robar el segundo permite falsificar.

Los tres cuidados, uno por uno:

1. **Exige token siempre** ✅. `auth.exigir_token_estricto`, declarado en el decorador de las dos rutas y no en una segunda lista al lado de `RUTAS_ABIERTAS` — una lista más obligaría a leer dos para saber qué protege qué, y el modo de fallo sería que una ruta nueva naciera laxa. Sin `API_TOKEN` el endpoint contesta **503** y dice qué configurar; no 403, porque lo que falta es configuración del servidor y no permiso de quien llama.

2. **Validación del destino** ⚠️ **cambiada, y este punto la tenía mal**. Decía «acá no hay caso de uso legítimo para un destino en la red interna». **Es falso, y se comprobó antes de escribir código**: el destino real de este despliegue es `http://localhost:3011`. Con la regla que este punto pedía, lo primero que hace el operador en la pantalla nueva —guardar la URL que ya usa— habría sido rechazado, y la migración habría sembrado una fila que el endpoint nunca aceptaría volver a guardar. Un estado alcanzable y no reingresable.

   El **razonamiento** —no el mecanismo— ya estaba escrito en el repo, en `proveedores/base._exigir_host_declarado`: *«la regla queda invertida respecto de `services/medios.py` —allá lo interno es lo sospechoso— porque lo que se protege es otra cosa»*. Allá, que no nos usen de escáner de la red; acá, que el producto firmado no se vaya lejos. Así que `services/entrega.REDES_PROHIBIDAS` bloquea link-local y NAT64 —donde viven los metadata de las nubes— y **permite loopback y red privada**, que es donde vive el back-end del operador y el único escenario en que las síntesis no salen de la máquina. **El mecanismo de aquella función —lista blanca para los destinos públicos— se evaluó aparte y se descartó**: protege más, y obligaría a editar el `.env` y reiniciar el contenedor para apuntar a un dominio público, que es la fricción que este punto existe para sacar.

   Lo que eso deja abierto queda dicho y no escondido: quien tenga el token puede apuntar la entrega a la red interna y usar `POST /deliver` como sonda ciega, porque el conteo de `rechazadas` contra `fallidas` distingue "hay algo escuchando" de "no hay nada". Lo que sostiene la defensa es el cuidado 1, que no se movió.

3. **Cambiar la URL no re-entrega nada** ✅. El punto lo dejaba abierto («hay que decidir»); se decidió **nunca reenviar**. Son 519 síntesis firmadas saliendo de golpe hacia un back-end que quizá recién se levanta, disparadas por lo que para quien lo hace es corregir un tipeo. El reenvío masivo tiene que ser una acción con ese nombre, y ya existe: `POST /deliver?forzar=true`. Verificado contra la base real: tres cambios de URL, 388 entregadas y 131 pendientes antes y después.

**Lo que este punto NO cerró**: la pantalla de Ajustes que consume estos endpoints. Está en el bloque siguiente del punto 14, y hasta entonces `GET /entrega` y `PATCH /entrega` figuran en `app/CONTRATO.md` como **no consumidas**.

### 12. El motor tenía logging pero no salida ✅ COMPLETO (21/08/2026)

**Cerrado el 21/08/2026, antes de pasar las mejoras post-1.0 a `main`.** Apareció verificando el pipeline, no buscándolo: los 16 módulos de `src/` llaman a `logging`, y **no había un solo `basicConfig` ni handler en todo el proyecto**. Estaba anotado como "persistir los logs" en notas viejas y nunca llegó a ser un punto del backlog.

Sin handler en la raíz, la consecuencia no es "los logs salen feos". Es que **todo `logger.info` se descarta sin dejar rastro** y todo `WARNING` para arriba cae en `logging.lastResort` —el handler de emergencia de la stdlib— sin fecha, sin nivel y sin nombre de módulo. Uvicorn no lo tapa: configura solo sus propios loggers y deja la raíz intacta a propósito, para no pisarle la configuración a la app que hospeda.

Lo que se perdía no era ruido:

- el resultado de cada paso del pipeline,
- con qué modelo se sintetizó y cuántos tokens costó,
- **el porcentaje del ciclo que consumió la corrida** — que es, según este mismo roadmap, el número con el que se calibra `INGEST_INTERVAL_MINUTES`. El motor medía su propia utilización y tiraba la medición.

**Qué se construyó.** `src/logging_config.py`, llamado como primera cosa del `lifespan`. `LOG_LEVEL` (INFO por defecto, y un valor inválido no tumba el arranque) y `LOG_SQL` aparte, porque el SQL de un ciclo son miles de líneas. Techo en WARNING para las librerías ruidosas —`httpx` sola emite una línea por request de artículo— dejando `apscheduler` afuera a propósito, que sus dos líneas por ciclo son la prueba de vida del scheduler. Las líneas de uvicorn pasan por el mismo handler, así que el log entero tiene un solo formato y todas las líneas llevan hora.

**Dos hallazgos que no se buscaban:**

- **`echo=(ENVIRONMENT == "development")` en el engine tenía que morir.** Con un echo verdadero SQLAlchemy le cuelga un handler propio al logger de la Engine y no le apaga la propagación: en cuanto la raíz tuvo handler, cada sentencia habría salido **dos veces con dos formatos**. El SQL ahora se enciende con `LOG_SQL`, subiendo el logger.
- **En Windows se perdían líneas enteras.** `sys.stdout` sale en cp1252, y una línea con un carácter que ese codec no tiene —un titular con `北京`, un apellido en cirílico— hace que `emit` levante `UnicodeEncodeError`; `logging` no propaga la excepción, escribe un `--- Logging error ---` y **descarta el mensaje**. Verificado con una corrida real. La salida se fuerza a UTF-8.

**Persistencia y rotación viven en el `docker-compose.yml`, no en el código**: el motor escribe a stdout y Docker lo persiste, pero el driver `json-file` **no rota por defecto** y un disco lleno también tumba a Postgres. Techo de 10 MB × 5 archivos ≈ más de tres meses medidos.

### 13. Entidades HTML sin decodificar en el cuerpo de las noticias

Lo destapó el punto 12 en su primera corrida con logs: una síntesis tituló *"Reforma previsional en Entre R&iacute;os para reducir el d&eacute;ficit"*. Medido sobre la base real: **38 de 5.390 `contenido_limpio` (0,7%) contienen entidades HTML sin decodificar; `titulo` no tiene ninguna** — o sea que el problema está en una sola de las dos vías de limpieza del cuerpo, no en la ingesta en general.

Es chico en volumen pero **sale publicado**: entra al prompt como evidencia, el modelo lo copia tal cual al título del ángulo, y de ahí va al back-end. Vale medir primero cuál de las dos vías (el `content:encoded` del feed o `trafilatura`) lo deja pasar, antes de agregar un `html.unescape` a ciegas en los dos lados.

Prioridad baja frente a los puntos 3 y 11, pero es barato y es visible para el lector final.

### 14. La app de escritorio del operador — la cabina del motor

**Diseñada el 06/09/2026** en una sesión de grillado completa. **Fases 0 a 3 construidas el 06/09/2026** — ver el estado abajo. Las decisiones, con lo que se evaluó y se descartó, están en `change_logs.md`.

**Qué es.** Una app Windows, para un solo operador, que **maneja el motor en vez de empaquetarlo**: prende y apaga los contenedores que ya existen y le habla a `localhost:8000`. Sin hosting, sin nada siempre activo, sin tocar pgvector ni empaquetar los 1,8 GB del `.venv`.

**Por qué existe, más allá de 6-bis.** Los puntos **2**, **3**, **9** y **11** tienen todos la misma forma — *"esto lo decide el operador, no el `.env`"*. Hoy decidir significa editar un archivo y reiniciar un contenedor. La app es lo que vuelve usable esa tesis, y es el consumidor natural de los dos puntos que siguen abiertos.

**La v1 hace cuatro cosas**, y sacar cualquiera deja de ser una sala de control: ver los clusters con su estado y si ya tienen síntesis · sintetizar uno eligiendo modelo · leer lo que salió · ver en qué anda el pipeline. Dos pantallas cohesivas: la lista de trabajo y el feed de lectura.

**Stack**: Tauri (UI en React, shell mínimo en Rust), solo Windows. Vive **dentro de este repo**, con tres condiciones que **se cumplen al construir la app y no antes** —hoy no hay `app/` ni workflow que filtrar—: CI separada por paths, docs de la app en `app/` y no en `specs/`, y el contrato de endpoints documentado y sostenido por un test. Quedan acá anotadas para que el checklist de los endpoints, ya cerrado, no las dé por hechas.

**Ciclo de vida**: minimizar deja el pipeline vivo; cerrar lo detiene. Con la consecuencia medida escrita al lado — **apagado más de ~4 h se empieza a perder La Nación de forma permanente**, porque su feed se da vuelta en ese plazo.

#### Lo que hay que construir en el motor primero, y sin lo cual la app no existe

- [x] **`GET /sintesis`** ✅ — lista resumida, con **paginación por cursor** sobre `(fecha_generacion, id)`, más `?cluster_id=` y `?entregado=`. El cursor es opaco a propósito, y un cursor mal formado es 422.
- [x] **`GET /sintesis/{id}`** ✅ — el detalle completo, con la comparativa y las fuentes.
- [x] **Tabla `corrida`** ✅ — una fila por corrida, pasos en `jsonb`, migración `b963fe84825f` aplicada contra la base real. La fila se abre antes del primer paso, así una corrida que muere deja **rastro de que existió** — `inicio` puesto y `fin` en `None`. El detalle de los pasos **no** sobrevive a una muerte de proceso: `pasos` se persiste recién al cerrar.
- [x] **`GET /pipeline`** ✅ — última corrida, si hay una en curso y las previas. `corriendo` no sale solo de `fin IS NULL`: una corrida abierta y vieja se informa como `huerfana` en vez de mentir.

**Los cuatro cerrados el 06/09/2026**, con 18 tests nuevos y 6 mutaciones detectadas. Detalle en `change_logs.md`. El motor pasó de 16 a 19 endpoints y ya sabe devolver lo que produce y decir en qué anda.

#### Estado de la app — dónde retomar

Construidas las **fases 0 a 8** del plan de nueve. Verde en `cargo fmt`, `clippy -D warnings`, 57 tests + 5 de integración contra el motor real, `tsc --noEmit`, `npm run build` y `bindings:check`; del lado del motor, **844 tests** y `ruff`.

- **Fase 0 · toolchain** ✅ — Node, rustup con toolchain MSVC y Build Tools instalados.
- **Fase 1 · esqueleto** ✅ — la app abre, pide el token una vez y lo guarda en el Credential Manager, y muestra el `GET /` real.
- **Fase 2 · control del motor** ✅ — levanta y para los contenedores (`up -d --build` y `stop`, nunca `down`), con la máquina de estados `reconstruyendo → arrancando → migrando → listo` sondeada contra el 503. Detecta que Docker Desktop no está corriendo y lo dice.
- **Fase 3 · cliente tipado** ✅ — 16 structs derivados de fixtures capturados del motor real, los tipos de TypeScript generados desde Rust con `ts-rs`, y siete comandos, uno por endpoint. `404` y `422` dejaron de colapsar en un número.
- **Fase 4 · lista de trabajo** ✅ — la pantalla con sus cuatro componentes, el puente `invoke()` cruzado, el punto flojo del `Set<cluster_id>` medido y resuelto con un campo del motor, y la CSP prendida y comprobada en el ejecutable de release. **El andamio se mantuvo hasta el bloque E** como banco de pruebas del puente, porque cubría comandos que ninguna pantalla ejercitaba y medía lo que sólo se mide con la ventana abierta. Se borró el 09/09/2026, cuando las pantallas reales pasaron a cubrirlos.
- **Fase 5 · feed de lectura** ✅ — la segunda pantalla: los ángulos paginados por cursor, el detalle con la comparativa como una columna por medio, y el 422 de un cursor caducado reseteando la lista en vez de trabarla. La cabecera pasó a ser pegajosa y la barra del pipeline dejó de mostrar `[object Object]`.
- **Fase 6 · bandeja y ciclo de vida** ✅ — el ícono con las dos salidas nombradas, y la cruz que pregunta en vez de cerrar. Verificados los tres caminos con `docker ps`, incluido el que **deja el motor corriendo**. Minimizar va a la barra de tareas y no a la bandeja, al revés de lo que pedía el plan: esconderla dejaría una sola forma de volver. Los íconos pasaron a ser los de Sin Ruido el 08/09/2026, generados desde `app/public/isotipo.svg`.
- **Fase 7 · test de contrato** ✅ — `app/CONTRATO.md` con las 19 rutas y `tests/test_contrato_api.py` con 22 tests, del lado del motor. Subconjunto y no igualdad, sin mocks, y un guardián que compara el contrato contra los bindings para que no derive de lo que la app exige. Encontró dos rutas `PATCH` que el inventario manual se había comido.
- **Fase 8 · CI separada por rutas** ✅ — `ci.yml` con filtro por **inclusión** —para que un cambio en los bindings despierte al motor— y `app.yml` nuevo en `windows-latest`, forzado por `keyring`. Verificada provocando cada caso: `specs/` no dispara nada, `tests/` sólo el motor, y `tipos.rs` con su binding **los dos**. El caché baja la corrida de la app de 445 s a 148 s.

#### Antes de la fase 9 — los huecos del checklist del operador

Auditada contra siete puntos, la app cubría tres (leer clusters sin sintetizar, ver los ángulos con sus noticias, sintetizar y resintetizar) y no cubría cuatro. El punto 2 quedó descartado por el usuario —«no es problema ni ahora ni a futuro»—, así que se planificaron 1, 5, 6 y 7 más sacar el andamio.

- [x] **Bloque A1 · `GET /` suma `exige_token` y `entrega_configurada`** ✅ (08/09/2026) — aditivo, así que el contrato no se rompe. Arrastró el struct `Salud`, su binding, el fixture recapturado del motor real y el diccionario del contrato. Tres mutaciones cazadas. De paso destapó que los tests de `secretos.rs` eran intermitentes: 2 fallos de 12 corridas en paralelo, arreglado con un candado propio y verificado 0 de 20.
- [x] **Bloque A2 · el destino de entrega sale del `.env`** ✅ (08/09/2026) — cierra el **punto 11** del backlog entero, con su tabla, su migración corrida contra la base real y sus dos endpoints con token obligatorio. Siete mutaciones cazadas. La validación del destino **se apartó del plan a propósito** y el motivo está en el punto 11.
> ✅ **A1 y A2 pasaron por `/revisar`** (08/09/2026): dos ejes más el tercer par
> de ojos, **nueve hallazgos y ninguno falso**. Los dos defectos reales —`GET /`
> devolviendo 500 en vez de 503 con la base caída, y `{"url": ""}` apagando la
> entrega con un 200— están arreglados, con test y mutación.
>
> ✅ **B, C, D y E cerrados el 09/09/2026**, con el checklist manual corrido
> entero contra la app real. **Encontró cinco defectos que ninguna suite veía**,
> entre ellos que la ventana no se podía cerrar en las pantallas tempranas y que
> contra un motor con la API abierta no funcionaba una sola pantalla. Y la
> entrega al back-end quedó probada punta a punta: **569 síntesis, cero
> pendientes**. Todo el detalle en `change_logs.md`.
>
> **Quedan el bloque F —los medios— y la fase 9**, el empaquetado. Ver abajo.

- [x] **Bloque B · configuración y credenciales** ✅ (09/09/2026) (puntos 1 y 7 del checklist) — "Olvidar token" pasó a llamar a `token_borrar`, que estaba en Rust desde la fase 1 sin que lo llamara nadie: antes sólo ponía en `false` un estado de React y la credencial se quedaba viva. Pantalla de Ajustes nueva, que **funciona con el motor apagado** porque es donde se arregla que el motor no arranque. La ruta del repo se revalida al usarla y no sólo al guardarla, con categoría propia (`RutaInvalida`). Y el orden del arranque se invirtió: primero la carpeta, después el token y sólo si el motor lo exige.
- [x] **Bloque C · modelos** ✅ (09/09/2026) (punto 5) — pestaña propia con lista, activar/apagar (`PATCH`) y alta (`POST`). Relee la lista entera al activar, porque prender uno apaga a los demás del lado del motor. El aviso dice que la credencial va al `.env` y hay que reiniciar el contenedor, y **no dice cuál variable**: esa regla se cerró después de una fuga.
- [x] **Bloque D · la entrega** ✅ (09/09/2026) (punto 6, mitad app) — Ajustes edita la URL, con un modal previo que deja asentado que el secreto tiene que coincidir con ese back-end. `TarjetaAngulo` dejó de mostrar "sin entregar" sin destino, pero **sigue mostrando "entregado"**: lo segundo es un hecho del pasado y sigue siendo cierto. Las dos rutas pasaron a `CONTRATO` con la marca `solo_forma`.
- [x] **Bloque E · limpieza** ✅ (09/09/2026) — el andamio se borró entero, y eso apretó la guarda del puente: `PERMITIDOS` pasó de cuatro archivos a **dos**. `App.tsx` también salió, y mientras estuvo en la lista un `invoke` suelto en la pantalla principal pasaba sin que nada dijera. La sonda de la CSP quedó como lista de verificación de release en `app/README.md`; `motor_salud` se resucitó en Ajustes.

#### Bloque F — los medios, y el cierre de la superficie que la cabina necesita (11/09/2026)

**Va ANTES de la fase 9, y el motivo es la decisión del updater.** Sin updater, cada versión que se reparte hay que volver a repartirla a mano. `POST /medios` y `PATCH /medios/{id}` existen desde el punto 3 (03/09/2026) y **no los consume nadie**: sacar el instalador sin la pantalla es distribuir una cabina que sabemos incompleta, y la corrección sería una ronda entera de reinstalar. Entra antes.

**Sobre «no tocar más el motor»:** no se puede cerrar para siempre —los puntos 4, 6, 13 y 16 son todos trabajo de motor—, pero sí se puede cerrar de una vez **la superficie del motor que la cabina necesita**, que es una lista corta y conocida. Es lo que hace este bloque.

**F1 — motor, lo que falta de la superficie.** Tres cosas y ninguna más:

1. **El agregado de composición de clusters** (ver F3). Endpoint propio, calculado en SQL, y no campos extra en `GET /medios`: aquella ruta es la lista y esto es un informe, con otro costo y otra frecuencia de uso. Queda además disponible para el back-end si alguna vez lo quiere.
2. **Modificar un medio.** Hoy `PATCH /medios/{medio_id}` sólo acepta `activo`: cambiar el nombre o un feed no se puede. Cambiar un feed **tiene que re-sondear**, por el mismo camino que el alta — un feed que no responde guardado en silencio es un medio que deja de ingerir sin que nadie se entere.
3. **`version` en `GET /`**, que es la etapa 1 del punto 17. Si esta tanda es la que cierra la superficie, va acá: después de la 1.2.0 la ventana y el motor viajan juntos y hace falta que alguien verifique que el par no derivó.

**F2 — app, la pantalla de Medios.** Lista, alta con el sondeo a la vista, activar/desactivar y modificar. La baja **es el desactivar y no hay `DELETE`**, por el mismo motivo que en modelos: un medio deshabilitado conserva sus noticias, sus clusters y sus síntesis ya entregadas, y se puede volver a habilitar.

**F3 — app, el panel de composición.** Por medio: en cuántos clusters está, en cuántos **solo**, en cuántos **con exactamente 2 medios** y en cuántos **con más de 2**; y de los clusters donde está solo, el tópico que predomina. Sirve para decidir qué medio sumar.

**El obstáculo que parecía bloquearlo, y por qué no lo hace.** Los `topicos` viven en `Sintesis` y no en `Cluster`, así que un cluster de un solo medio **no tiene tópico**: nunca se sintetizó, porque `MIN_MEDIOS_CLUSTER = 2`. Justo los clusters que interesan son los que no tienen el dato. Pero `services/topicos.py` ya trae `topico_declarado(url)`, que lo deriva de la sección declarada en la URL contra una taxonomía cerrada. Ese módulo documenta que el método es flojo **porque los medios se contradicen entre sí**; en un cluster de un medio solo no hay con quién contradecirse, así que la debilidad conocida no aplica. Verificado corriendo la función contra la base real, no deducido.

**Medido el 11/09/2026 contra la base real** — 688 clusters, 130 solos, 398 con 2 medios, 160 con más de 2, sobre 8 medios cargados:

| medio | activo | clusters | solo | con 2 | >2 | % solo |
|---|---|---|---|---|---|---|
| La Nación | sí | 423 | **65** | 238 | 120 | 15% |
| TN | sí | 475 | 29 | 296 | 150 | 6% |
| Perfil | sí | 136 | 17 | 58 | 61 | 13% |
| El Cronista | sí | 120 | 10 | 54 | 56 | 8% |
| Ciudad Magazine | sí | 143 | 5 | 77 | 61 | 3,5% |
| Revista Gente | sí | 84 | 2 | 34 | 48 | 2,4% |
| Revista Paparazzi | sí | 89 | 2 | 39 | 48 | 2,2% |
| Clarín | **no** | 0 | 0 | 0 | 0 | — |

Tópico de los 130 clusters solos: **sociedad 28, economía 26, deportes 20, internacional 16**, espectáculos 10, lifestyle 5, política 3, policiales 2, ciencia 1, y 19 sin tópico derivable.

**Y el dato refuta la hipótesis que motivó el panel**, que era que el material huérfano lo producen los medios de nicho. Es al revés: las tres revistas de espectáculos son las que **menos** generan (2-3,5%), porque se cubren entre ellas. Los huérfanos salen de los **generalistas por amplitud** —La Nación sola aporta 65 de 130, en sociedad, economía e internacional, que es lo que los otros siete no siguen—. Eso cambia la respuesta a «qué medio agregar»: otro generalista fuerte en sociedad y economía, no uno de nicho. Vale anotar que el panel ya se ganó el lugar antes de existir: su primera corrida dio vuelta la premisa con la que se lo pidió.

**Decisiones, cerradas el 11/09/2026:**

- **Los términos de uso del medio: un modal de advertencia, no una declaración.** Se descartó registrar que el operador «leyó y acepta» los términos, y el motivo es que sería mentira útil: **no podemos obligar a nadie a leerlos**, así que una marca en la base sólo fabricaría una constancia de algo que no ocurrió. Va un modal antes del alta que advierta los problemas que puede traer usar un canal RSS **para fines que no sean de uso personal**, que es el riesgo real. Misma forma y mismo motivo que el modal previo a guardar el destino de entrega (bloque D): deja la responsabilidad asentada en quien decide, explicando por qué, sin inventar un consentimiento.

- **Modificar es cambiar la URL, por si el medio la cambió.** Y trae un riesgo propio que **no es el SSRF**: apuntar un medio existente a una URL que no es de ese medio. El daño no es técnico sino de atribución — las síntesis dirían que La Nación publicó algo que publicó otro, **firmado**, y el back-end lo recibiría como legítimo. Es la misma familia que el destino de entrega: redirigir el producto, no filtrar una credencial. La guarda se decide abajo; el re-sondeo del feed es obligatorio en cualquier caso, porque un feed que no responde guardado en silencio es un medio que deja de ingerir sin que nadie se entere.

- **Clarín queda apagado: se desactivó por su política de RSS**, no por accidente. O sea que **no es el generalista que el panel pide**, aunque los números lo señalen: la restricción es de política y no de datos. Si hace falta un medio de prueba para ejercitar la pantalla, conviene uno sin ese problema —o un feed de prueba— antes que prender justo el que está apagado por ese motivo, porque prenderlo es ingerir bajo la política que lo apagó.

#### Fase 9 — el empaquetado. DECIDIDA el 10/09/2026, sin updater y sin firma

**Se retoma el viernes 11/09/2026.** El plan de tareas está abajo; la decisión que lo ordena, primero.

**El updater no entra, y el motivo no es el costo.** Motor y app son **una unidad con un solo número de versión** (punto 17, decisión 3): la app no aplica sobre ninguna otra cosa que el motor, y enriquecerla hasta volverla un producto de lectura sería duplicar lo que el equipo de back-end ya construye. De ahí sale el argumento que cierra la discusión, en tres pasos:

1. Un solo número quiere decir que **cualquier versión nueva incluye al motor**.
2. El updater de Tauri entrega el `.exe` de la ventana; **no puede entregar el motor**, que Docker construye desde la carpeta del repo (`docker.rs:139`).
3. Entonces **no puede entregar una versión nueva**. Lo único que lograría es dejar la ventana adelantada respecto del motor: fabricar el par desparejo que la decisión de la unidad declara estado inválido.

Esto **revierte la recomendación del 09/09/2026**, que era meter el updater. Aquella se apoyaba en dos premisas que después se cayeron: que hubiera una población de instalaciones inalcanzable a mano —es el equipo—, y el argumento de irreversibilidad —que la clave pública se compila en el binario, así que agregarlo tarde obliga a reinstalar todo—. El segundo sigue siendo cierto y ya no importa: **no hay nada coherente que el updater pueda entregar** bajo el modelo de unidad.

La única forma de que tenga sentido es que el instalador **lleve el motor adentro** en vez de construirlo desde una carpeta. Eso es un rediseño del empaquetado entero, no una fase 9, y queda anotado como la forma de largo plazo si alguna vez esto se reparte fuera del equipo.

**La firma de código tampoco entra**, por lo de siempre: son cientos de dólares al año y el gasto es un límite duro. El costo de no firmarla es el aviso «Windows protegió su PC» en la primera instalación — un clic, explicado en el README, hasta que haya a quién repartirle.

**Las tareas, en orden:**

1. **Unificar el número en 1.2.0.** `app/package.json:4`, `app/src-tauri/tauri.conf.json:4` y `app/src-tauri/Cargo.toml:3` pasan de `0.1.0` a la versión del motor, que ya vive en `src/main.py:436`. Es 1.2.0 y no 2.0.0 por el mismo criterio que la 1.1.0: para quien consume el motor no cambió nada —el payload es idéntico y la API es retrocompatible— y esta vez **tampoco hay paso manual al actualizar**, porque la migración de la entrega se auto-siembra desde el `.env`. Lo que cambió es que ahora hay cabina.
2. **`CHANGELOG.md` en la raíz, escrito para el operador.** Una línea por arreglo, en términos de lo que cambia para quien usa esto. No es `specs/change_logs.md`, que es el registro de decisiones de diseño y tiene otro lector. Las entradas que exijan tocar el `.env` van marcadas aparte.
3. **El bundle NSIS.** Los íconos ya están (ver deudas, abajo).
4. **`app/README.md`: cómo se actualiza.** Hoy no lo dice nadie. Traer el repo y reabrir la app, que reconstruye sola con `up -d --build`.
5. **El job de CI que arma el instalador**, disparado por tag. `app.yml` ya lo tiene anotado como trabajo de esta fase, aparte del job de verificación.

**El guardián de versión** —que `GET /` devuelva `version` y la ventana avise si no coincide con la suya— es la etapa 1 del punto 17 y es lo único que hace que «son una unidad» lo verifique alguien. Arrastra el contrato, el binding y el fixture, igual que `exige_token`: trabajo de forma conocida, hecho dos veces este mes. **Entra si hay tiempo; si no, es lo primero de la 1.2.1.**

---

*Lo que sigue es el planteo original de la decisión, del 08/09/2026, que quedó resuelto arriba.*

**Una decisión que la fase 9 tiene que tomar ANTES de empaquetar (08/09/2026).**

El plan dice «sin firma y con updater deshabilitado en la v1: **un operador, una
máquina**». Esa premisa **ya no se cumple**: el objetivo pasó a ser un producto
usable por cualquier usuario, y con eso reempaquetar deja de ser un trámite y se
vuelve una campaña de avisar y esperar que cada uno reinstale.

Son dos cosas independientes y conviene no confundirlas:

- **El updater** resuelve *cómo llega la versión nueva*. Tauri lo trae: la app
  consulta un JSON al arrancar y aplica la actualización sola. Necesita un par
  de claves propias de Tauri —gratis, se generan con un comando— y un lugar
  donde publicar el manifiesto; GitHub Releases alcanza y no cuesta en un repo
  público. Estaba anotado como «fase 10 opcional» y con el objetivo nuevo pasa a
  ser bastante menos opcional.
- **La firma de código** resuelve *por qué confiar en el archivo*. Sin ella,
  Windows muestra «Windows protegió su PC» a todo el que no sea el autor. Un
  certificado son cientos de dólares al año, y el gasto es un límite duro acá.

Se puede tener **updater sin firma**: el updater funciona igual, y lo que queda
es la advertencia en la primera instalación.

**Por qué antes y no después**: quien instale una versión sin updater no se
entera nunca de las siguientes. Hay que volver a buscarlo a mano — justo lo que
el updater viene a evitar. Si entra, tiene que estar en el primer instalador que
se distribuya.

**Deudas anotadas, ninguna bloquea:**

- **Los íconos ya son los de Sin Ruido (08/09/2026).** Se generaron con
  `tauri icon` desde `app/public/isotipo.svg`, un cuadrado de 512×512 con los
  tres colores de marca. Hacía falta el **isotipo** y no el logotipo: éste es
  1,84:1 y en un cuadrado deja el nombre en 8,7 píxeles a los 16×16 de la
  bandeja. **Son dos archivos con usos distintos y no intercambiables**:
  `isotipo.svg` es multicolor y va a los íconos; `logo-svg.svg` es un trazo
  monocromo apaisado y va a la barra, donde entra como máscara CSS y toma el
  color del tema. Se verificó midiendo el resultado —el de 32px da 65% azul de
  marca, 16% blanco y cero negro—, porque el SVG lleva su CSS en un `<style>`
  interno y un rasterizador que lo ignorara habría producido formas negras.
  Se borraron las carpetas `android/` e `ios/` que `tauri icon` genera de yapa:
  35 archivos de plataformas que este proyecto excluye.
- **La deuda de `secretos.rs` era falsa, y se cerró midiéndola (08/09/2026).**
  Acá decía que sus tests «van a romper en CI en la fase 8, donde no hay almacén
  de credenciales». **Pasaron los cinco** en el runner de Windows. La afirmación
  se había deducido de que tocan el Credential Manager, sin mirar que lo hacen
  contra un servicio propio (`sin-ruido-motor--test`) con limpieza, y sin
  preguntarse en qué sistema operativo iba a correr la CI — que tiene que ser
  Windows porque `keyring` usa la feature `windows-native`. De haber seguido esta
  nota se habría perdido la cobertura del único código que habla con el almacén
  real, para arreglar un problema inexistente.
- **Nadie cierra las corridas huérfanas.** Reiniciar los contenedores a mitad de
  ciclo deja la fila abierta para siempre; la pantalla lo informa bien, pero el
  motor no las limpia al arrancar. Había tres al 07/09.
- **El camino `hecha` de `sintetizar_cluster` nunca se ejercitó de punta a
  punta**: es el único que gasta plata. Los dos desenlaces que cortan sí.
- **Sin probar**: bandeja → "salir dejando el motor corriendo". Va por el mismo
  despachador que sí se probó, cambiando qué pedido emite.
- **`groq-qwen` quedó con `max_tokens=900`** de una prueba vieja.

#### Lo que queda deliberadamente afuera de la v1

La consola completa del operador —medios, modelos, alertas, webhook— que es lo que absorbería los puntos 9 y 11. Llega cuando la v1 demuestre que se usa. Y el instalador que empaquete el motor entero, que costaría sacar pgvector: si algún día hace falta, la UI ya va a estar hecha.
### 15. Lector de voz para las síntesis — escuchar en vez de leer

**Anotado el 08/09/2026, posterior a las nueve fases de la app.** No se construye hasta que la cabina esté terminada.

Escuchar es el modo natural de consumir noticias mientras se hace otra cosa —manejar, cocinar, viajar—, y es justo el momento en que nadie va a abrir una pantalla a leer la comparativa de enfoques. El feed de lectura de la fase 5 ya tiene el texto en la mano: `resumen_neutro` es un párrafo neutro escrito para ser leído de corrido, que es exactamente lo que un lector de voz necesita.

**Qué se construiría**: un botón por ángulo que lea en voz alta, y probablemente una cola para encadenar varios.

**La decisión que hay que tomar primero es de dónde sale la voz**, y no es menor:

- **`speechSynthesis` del webview** (la voz de Windows). Es **gratis, local y sin red**: nada del texto sale de la máquina, no toca la CSP y no hay proveedor que facturar. La contra es la calidad — depende de qué voces en español tenga instaladas Windows, y suelen sonar robóticas.
- **Un proveedor de TTS en la nube**. Suena mucho mejor y **cobra por carácter**. Con ~28 síntesis por día activo, leer todo sería un costo recurrente nuevo, y este proyecto ya tiene el gasto de IA como límite duro. Además mandaría el texto a un tercero más.

**Recomendación anticipada**: empezar por la voz local, que cuesta cero y se prueba en una tarde. Si la calidad no alcanza, ahí sí **medir cuántos caracteres por día** implicaría lo otro antes de evaluar un proveedor — no al revés.

**Cuidados anotados desde ahora**:

- **Qué se lee y qué no.** `resumen_neutro` sí. La comparativa por medio es una tabla y leída en voz corrida se vuelve incomprensible; si se incluye, hay que redactarla distinta para el oído.
- **Esto no reemplaza un lector de pantalla.** Es una función de consumo, no de accesibilidad: quien usa NVDA o Narrador ya tiene el texto, y agregar una segunda voz encima estorba. Los dos usos conviven pero no son el mismo.
- **Empezar y cortar tienen que ser evidentes.** Una voz que arranca sola, o que no se puede parar rápido, es peor que no tenerla.

### 16. Un back-end caído no puede costar material — separar "no está" de "no le gusta"

**El motor tiene que servir sin back-end**, y esa es la decisión de arquitectura que ordena este punto: el sistema va a vivir en un sitio y el motor en otro, así que la entrega es un extra que ocurre **sólo si hay webhook configurado**, no una dependencia. Un back-end apagado es un estado normal, no un incidente.

Hoy el motor no se comporta así, y está medido.

`WEBHOOK_MAX_INTENTOS` son **5 barridos de 15 minutos: 75 minutos**. Pasado ese punto la síntesis sale del barrido y **no se reintenta nunca más** salvo que alguien llame a mano a `POST /deliver?forzar=true`. O sea que un back-end en otro sitio, caído poco más de una hora —lo que dura un deploy con problemas—, cuesta material de forma permanente.

Lo medido el 08/09/2026, con el back-end apagado desde el 06/09:

| corridas | entregadas | fallidas por corrida | agotadas |
|---|---|---|---|
| últimas 10 | **0** | ~32 | **40 quemadas** |

Y 124 acumuladas en total. Cada corrida sumaba más, y encima gastaba ~32 requests condenados de antemano.

**Mitigación aplicada, que no es el arreglo**: se borró el destino con `PATCH /entrega`. Sin destino el barrido corta antes de contar intentos, así que lo nuevo se acumula entregable en vez de quemarse — el mismo `POST /deliver` que se colgaba 60 s pasó a contestar en 0,3 s. Es exactamente el modelo declarado ("se envía sólo si el webhook está establecido") y se revierte con otro `PATCH`.

**El arreglo de fondo: distinguir las dos cosas que hoy cuentan igual.**

- **"El back-end no está"** (falla de red, timeout, conexión rechazada). No es información sobre esta síntesis. Corresponde **cortar el barrido en la primera** —no tiene sentido intentar las otras 31 contra un servidor que no contesta— y **no contar el intento**. Se reintenta la corrida siguiente, indefinidamente, que es lo que "el motor es independiente" significa.
- **"Esta síntesis no le gusta"** (un 4xx, que ya tiene su propia excepción, `EntregaRechazada`). Eso sí es información sobre la fila: cuenta el intento, agota y avisa, como hoy.

Se descartó **subir `WEBHOOK_MAX_INTENTOS`**: es una línea, pero deja el mismo diseño contra el reloj —el contador sigue siendo por síntesis— así que un back-end caído tres días quema todo igual, sólo que más tarde.

**Cuidados:**

- **El aviso tiene que seguir sirviendo.** Si nada agota nunca por caída, el mail de "síntesis sin entregar" deja de dispararse; hace falta que alguien avise que hace N corridas que no se entrega nada, sin mandar un mail por hora. `alerts.enviar_alerta` ya tiene cooldown por clave.
- **Las 124 quemadas se recuperan con `POST /deliver?forzar=true`** cuando haya back-end real. Es seguro: el contrato con el otro equipo dice que el receptor hace *upsert* por `sintesis.id`, así que reenviar no duplica.
- **`specs/webhook_contract.md` no se toca**: esto no cambia el payload ni la firma, sólo cuándo se reintenta.


### 17. Que el operador vea qué versión tiene y qué trae la nueva

**Anotado el 09/09/2026**, decidiendo si la app lleva updater (fase 9 del punto 14). El updater resuelve la ventana; **el motor queda afuera**, porque no viaja en el instalador: la app lo construye con `docker compose up -d --build` desde la carpeta del repo que eligió el operador (`app/src-tauri/src/docker.rs:139`). Un arreglo del motor llega a esa máquina sólo si alguien actualiza esa carpeta, y hoy **nada en la app lo hace y nada en la documentación lo explica**.

El problema de fondo no es que no se actualice solo: es que **nadie se entera de que está viejo**. Este punto ataca eso y nada más — mostrar y explicar, nunca actualizar. Que la app actualice el motor es el punto 18, y tiene su propia lista de dificultades.

**Lo que ya existe, comprobado:**

- **El motor ya sabe su versión.** `src/main.py:436`, `version="1.1.0"` en el constructor de `FastAPI`, publicada en `/docs` y en el esquema OpenAPI. Lo que no hace es decirla en `GET /`, que es lo único que la cabina lee sin token.
- **La app está en `0.1.0`**, declarada tres veces: `app/package.json:4`, `app/src-tauri/tauri.conf.json:4`, `app/src-tauri/Cargo.toml:3`.
- Hay tags `v1.0.0` y `v1.1.0`, los dos sobre el motor.
- **No hay una lista de cambios escrita para el operador.** `specs/change_logs.md` existe, pero es el registro de decisiones de diseño: mide en párrafos por qué se descartó una alternativa. No es «se arregló que la ventana no se podía cerrar».

#### Etapa 1 — decir qué tenés

`GET /` suma `version`. Es aditivo y la regla del contrato es subconjunto, igual que `exige_token` y `entrega_configurada` del bloque A1. Arrastra lo mismo que aquel: `Salud` en `tipos.rs`, el binding, el fixture `capturados/salud.json` recapturado del motor real, y el diccionario de `tests/test_contrato_api.py`.

**Y el campo tiene un uso mejor que mostrarlo: comparar.** Como motor y app son una unidad (ver abajo), la ventana puede contrastar su propia versión contra la que informa `GET /` y **avisar si no coinciden**, que es la única forma de que el par no derive en silencio. Ajustes muestra el número; el guardián es lo que hace que el número sirva para algo.

Cuesta un campo y una comparación. **No sale a la red y no toca la base de nadie.**

#### Etapa 2 — decir qué hay, y qué cambia

Un manifiesto publicado con la última versión y la lista de arreglos, y el aviso en Ajustes cuando lo instalado quedó atrás. Acá están las decisiones abiertas.

**1. De dónde sale la lista de arreglos.** Hace falta un `CHANGELOG.md` en la raíz **escrito para el operador**: una línea por arreglo, en términos de lo que cambia para quien usa esto. `change_logs.md` no sirve para eso y no hay que forzarlo — son dos documentos con dos lectores distintos. Los cinco defectos de la prueba manual son el ejemplo del tono: *«la ventana no se podía cerrar en la pantalla del token»* es lo que el operador necesita leer; por qué se arregló con un único `return` es asunto nuestro.

**2. Dónde vive el manifiesto, y si la app sale a internet.** Hoy la app habla **sólo** con `127.0.0.1:8000`. Consultar si hay versión nueva sería su primer pedido a la red: un host más en el que confiar, la CSP a revisar, y un fallo de conexión que no puede romper la pantalla. Dos caminos:

- **Reusar el manifiesto del updater**, si el updater entra en la fase 9. La superficie de red ya está pagada, el archivo ya existe y ya lleva notas de versión: el motor se suma como un campo más.
- **Un JSON propio** (`versiones.json` leído del repo por HTTPS), si el updater no entra. Es infraestructura nueva sólo para un aviso.

**Recomendación: que esta etapa dependa de la fase 9.** Con updater es un campo en algo que ya se publica; sin updater conviene quedarse en la etapa 1, que ya elimina el peor caso —no saber qué se está corriendo— por casi nada.

**3. Cómo se numeran los dos artefactos. DECIDIDO el 10/09/2026: un solo número para los dos.**

Se había anotado que separarlos era «más honesto» porque el motor puede quedarse quieto mientras la ventana se arregla tres veces. **Eso es falso en este proyecto**, y el motivo es de producto y no de versionado: la app no es un producto que se venda aparte, es el panel de control del motor y no aplica sobre ninguna otra cosa. Enriquecerla hasta volverla una app de lectura sería duplicar lo que el equipo de back-end ya está construyendo. Van juntas o no van.

Consecuencias, todas simplificadoras: **un tag por versión** (los que ya hay, `v1.0.0` y `v1.1.0`, siguen sirviendo), **un `CHANGELOG.md`** en vez de dos, **una entrada por versión** en el manifiesto si alguna vez hay manifiesto, y `app/package.json`, `app/src-tauri/tauri.conf.json` y `app/src-tauri/Cargo.toml` pasan de `0.1.0` a la versión del motor en la primera release conjunta.

Y una que no simplifica y hay que mirar de frente: **un par desparejo es un estado inválido**, no una molestia. Por eso la etapa 1 lo detecta.

#### Cuidados

- **El aviso no puede convertirse en un botón que actualice.** Ese es el punto 18. Acá se muestra y se explica; actualizar sigue siendo un paso manual del operador.
- **Una versión nueva puede exigir un paso manual en el `.env`.** Ya pasó al 1.1.0 (ver arriba: `GEMINI_API_KEY` → `MODELO_API_KEY` y prender la fila de `modelo_ia`). Si el aviso no lo marca, el operador actualiza y el motor deja de sintetizar sin explicación. Esas entradas del `CHANGELOG.md` van señaladas aparte, no mezcladas con los arreglos.
- **Sin red, Ajustes tiene que seguir sirviendo.** Es la pantalla donde se arregla que el motor no arranque: si la consulta de versión falla, se calla y muestra lo de la etapa 1.
- **La versión no es información sensible** —ya sale por `/openapi.json`—, así que sumarla a `GET /`, que es ruta abierta, no cambia nada de lo que un atacante podía averiguar igual.

### 18. Que la app actualice el motor sola — la continuación del 17

**Anotado el 09/09/2026 y no se implementa todavía.** Se evaluó hacerlo directamente, en vez del punto 17, y se descartó por cinco dificultades que no son del mecanismo sino de lo que hay alrededor. El `git pull` es la parte fácil.

**Redimensionado el 10/09/2026 por la decisión de la unidad** (punto 17, decisión 3): si motor y app son un solo artefacto con un solo número, entonces «actualizar» es una sola cosa —traer el repo y reabrir la app, que reconstruye sola— y no dos mecanismos que hay que mantener sincronizados. Este punto deja de ser una pieza de arquitectura y pasa a ser **una comodidad**: ahorrarle al operador un `git pull` en una terminal. Las cinco dificultades de abajo siguen intactas y siguen sin valer la pena por eso.

1. **Actualizar el motor *es* correr Alembic contra la base del operador.** `docker-compose.yml:135` arranca con `alembic upgrade head && uvicorn ...`: traer código y reconstruir aplica las migraciones, automáticamente y sin nadie mirando. Y **no tiene vuelta atrás**: si una versión de la ventana sale mal se reinstala la anterior, pero una migración con datos cargados se deshace restaurando un backup que el operador puede no tener. El modo de falla ya está documentado en `app/src-tauri/src/docker.rs`: código y base desfasados dejan el contenedor en bucle de reinicio, y desde la app eso se ve como «el motor no responde», sin ninguna pista. Ya pasó una vez.
2. **El `.env` no viaja en el repo.** Una versión puede pedir una variable nueva o renombrada, y hasta que el operador la edite el motor no sintetiza. O sea que esto **nunca puede ser «se actualiza solo»**: en el mejor caso es «se actualizó y ahora andá a editar un archivo». Choca además con la regla de que los `.env` los maneja el operador y nadie más.
3. **No hay canal, ni forma definida de que el repo llegue a una máquina.** ¿Se trae la punta de una rama de desarrollo? ¿Un tag? Y si el operador bajó un ZIP no hay `.git`; si tocó un archivo rastreado, el `pull` se planta con un conflicto adentro de una ventana que no tiene dónde mostrarlo. El punto 17 resuelve la mitad de esto al obligar a que exista un manifiesto y una numeración.
4. **Actualizar en el momento equivocado cuesta plata.** El scheduler corre cada 15 minutos y la síntesis es el 91% del costo del ciclo. Un `up -d --build` en el medio mata la corrida viva. Se resuelve —`GET /pipeline` ya dice si hay una— pero es una guarda más.
5. **El build.** Con caché caliente son 2 segundos (medido, ver el docstring de `arrancar`); después de tocar `requirements.txt` son minutos. Es lo único de la lista que ya está resuelto: el estado `reconstruyendo` existe en la interfaz desde la fase 2.

**Condición para retomarlo**: que el punto 17 esté cerrado —o sea, que exista numeración, manifiesto y una lista de cambios legible— y que se haya decidido qué hacer con el backup antes de una migración disparada por un botón.


### 19. Un panel de eventos en la cabina ✅ COMPLETO (12/09/2026)

**Anotado el 12/09/2026**, y la idea es del usuario. Sale de descubrir, ese mismo día, que **las nueve alertas del motor no le llegaban a nadie desde hacía meses**: la casilla estaba deshabilitada y el único rastro de cada fallo era un `logger.error` en un log que nadie mira. Ver el punto 9.

**El problema no es que falte información, es que está donde nadie la ve.** Para saber por qué algo no anda hay que hacer `docker logs` desde una terminal. Un panel no depende de SMTP, ni de Google, ni de que una casilla siga existiendo — y hoy el mail es el único canal, y falla en silencio.

#### La mitad que ya está construida

**`Corrida` guarda todo lo que hace falta para "una corrida empezó y terminó"**: `inicio`, `fin`, `duracion_segundos`, `utilizacion` y un `pasos` con el detalle por medio (nuevas, duplicadas, extraídas, errores). Medido el 12/09/2026: **91 corridas** desde el 06/09, 153 s de promedio.

Y **`GET /pipeline?historial=` ya devuelve hasta 50**, con la última aparte. La app ya lo consume en `BarraPipeline`, pero sólo muestra la última. O sea que **esa mitad del panel no necesita una línea de motor**: es una pantalla que lista lo que el endpoint ya trae.

#### La mitad que falta, y el embudo que ya existe

Los **eventos** —modelo que falló, back-end que rechazó, feed caído— viven sólo en el log de Docker, que rota a los 50 MB y no se puede consultar.

**Lo que abarata esto es que el embudo ya existe.** Los nueve `enviar_alerta` son exactamente los puntos donde el motor decide *"esto amerita avisar"*, y ya vienen con clave y texto:

| clave | qué avisa |
|---|---|
| `ingesta:{medio}` | un feed dejó de responder |
| `extraccion:{medio}` | falló bajar el cuerpo de la página |
| `robots:{base}` | el `robots.txt` cambió y ahora bloquea |
| `sintesis:vencidos` | clusters que vencieron sin publicarse |
| `webhook:rechazo` | el back-end rechazó una entrega |
| `webhook:agotadas` | síntesis abandonadas tras N intentos |
| `pipeline:{paso}` | un paso del ciclo falló |
| `scheduler:corrida-larga` | la corrida se pasó del intervalo |

No hay que instrumentar nada: hay que hacer que esa función, además de mandar el mail, **guarde una fila**. El prefijo antes del `:` ya es una categoría natural.

#### El detalle que decide si el panel sirve

**El evento se guarda aunque el cooldown silencie el mail.** `enviar_alerta` calla los repetidos para no inundar la casilla — eso está bien para el mail y está **mal** para el panel: si un feed falla cuarenta veces en una hora, el panel tiene que decir cuarenta, no una.

O sea que la fila se escribe **antes** de la comprobación de cooldown, y conviene que lleve un contador por clave en vez de cuarenta filas idénticas. Es el mismo dato con dos consumidores que necesitan cosas opuestas, y hay que resolverlo a propósito.

#### Costos, medidos

**El volumen no es un problema.** El motor loguea poquísimo: en 24 h hubo **40 líneas, y 29 eran ruido de acceso HTTP**. Los eventos que importan son unos pocos por día. Con retención de 90 días son cientos de filas, no millones.

| pieza | costo |
|---|---|
| `enviar_alerta` persiste el evento | ~30 líneas + migración. **Es el 80% del valor** |
| `GET /eventos` con filtro por categoría y cursor | endpoint chico, molde de `/sintesis` |
| Pestaña con las dos listas (corridas y eventos) | una pantalla, tamaño Modelos |
| Purga de eventos viejos | ~10 líneas, reusa `services/purga` |

Del tamaño del bloque C del punto 14: uno o dos días.

#### Decisiones, cerradas el 12/09/2026

- **Qué se guarda: sólo lo que pasa por `enviar_alerta`.** Son eventos que alguien ya decidió que ameritan aviso, vienen con clave y texto, y no hay ruido que filtrar. Guardar el log entero convierte esto en un agregador de logs, que es otro producto.
- **Una fila por clave, con contador**, no una por ocurrencia. Un feed caído toda la noche produciría 96 filas idénticas que tapan todo lo demás — el mismo problema que el cooldown evita en el mail. La fila lleva `veces` y `ultima_vez`, y **se escribe aunque el cooldown silencie el envío**: el mail se calla, el contador sigue subiendo. Lo que se pierde es el texto de las ocurrencias intermedias; se guarda el último, que en la práctica es el que describe el estado actual.
- **Retención: 90 días.** Alcanza para «esto viene pasando desde el mes pasado», que es la pregunta que un panel de eventos contesta. Con el volumen medido son cientos de filas.
- **Token siempre**, como `/entrega` y `/alertas`. Los eventos nombran medios, dicen cuándo falló qué, y son información operativa del despliegue.
- **Cómo escribe `enviar_alerta`, que no recibe sesión.** Mismo problema y misma salida que `_destinos`: abre la suya y tolera el fallo. **Nunca puede tirar el proceso** — fallar al registrar un fallo no puede ser lo que rompa la corrida.
- **Severidad**: los tres que pasan `ignorar_cooldown=True` son los terminales y ya se marcan solos; no hace falta un campo nuevo por ahora.

#### Lo que quedó, y lo que se corrigió construyéndolo

Los cuatro pasos, hechos. Y tres cosas que no estaban en el plan:

- **`Problemas` terminó siendo pestaña propia, con contador**, y no una lista adentro de Actividad. El motivo es de función: separada, la pestaña puede llevar el número — y así **te enterás de que algo se rompió sin ir a buscarlo**, que es literalmente lo que este punto vino a resolver. Una lista adentro de otra pestaña no puede hacer eso.
- **La burbuja cuenta lo que pasó desde la última revisión**, no todo. La marca de «ya lo vi» vive en el `ajustes.json` de la app y no en el motor, y eso es semántica antes que costo: *¿lo vi yo?* es del operador y de su máquina; si dos personas usan el mismo motor, que una lo marque no puede apagarle el contador a la otra. Y como se compara contra `ultima_vez`, **un evento que vuelve a ocurrir vuelve a contar** — un «descartar» escondería un problema vivo.
- **La suite escribía en la base de producción.** `registrar_sin_romper` abre su propia sesión —tiene que hacerlo, `enviar_alerta` no recibe una— y los tests corren fuera del contenedor con el mismo `DATABASE_URL`. Había 37 filas de `paso:x` mezcladas con los eventos reales. Lo encontró mirar `GET /eventos` contra la base, no un test. Cerrado con un *fixture* autouse en `conftest`.

#### Y una cadena de tres fallas de zona horaria

Vale anotarla porque las tres eran el mismo problema:

1. Los dos endpoints nuevos devolvían el `isoformat` **crudo** en vez de pasar por `a_local`. La regla de `src/tiempo.py` —guardar en UTC, mostrar en UTC-3 con offset— estaba escrita desde antes, con tres motivos y un incidente adentro, y se la salteó igual. La pantalla mostraba todo tres horas adelantado.
2. Arreglar eso **rompió** una comparación escrita veinte minutos antes, que comparaba fechas **como texto**. Con el offset, `"17:16:08-03:00" > "21:00:21"` da falso siempre: la burbuja quedaba apagada para siempre, sin ruido y sin test en rojo.
3. Y el test del arreglo destapó lo de fondo: **JavaScript interpreta un ISO sin zona como hora local**. El formato viejo ya estaba mal para cualquier consumidor — no era feo, era ambiguo.

**Queda anotado y sin hacer**: `a_local` es una convención que se respeta a mano, y se violó en tres campos el mismo día. Lo que la haría cumplir es un test que recorra el `openapi.json` y verifique que todo campo de fecha trae offset, igual que el guardián que obliga a documentar cada ruta nueva.

#### El orden, y por qué

1. **La pestaña «Actividad» con las corridas.** No necesita una línea de motor: `GET /pipeline?historial=` ya devuelve hasta 50. Usable el mismo día.
2. **La tabla `evento` y que `enviar_alerta` la escriba.** Migración y ~30 líneas.
3. **`GET /eventos` y la segunda lista** en la misma pestaña.
4. **La purga.**

Se empieza por lo que **no** motivó el punto, y es a propósito: entrega algo usable antes de pagar la migración, y la pantalla que se arma en el paso 1 es la misma que recibe los eventos en el 3.

#### Cuidado

**Un evento no puede filtrar lo que el log sí puede decir.** El log del motor es privado y ahí van los mensajes crudos del proveedor; el panel se lee desde la cabina, que es otra superficie. Vale la misma regla que ya se aplicó dos veces: el detalle del proveedor no se echoa. Ver el punto 9 y `POST /modelos`.
