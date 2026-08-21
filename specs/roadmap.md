# 🗺️ Roadmap — Sin Ruido

Estado de las 5 fases del proyecto. El *qué* y *cuándo* vive acá; el *por qué* de cada decisión está en `change_logs.md`.

**Estado: versión 1.0.** Las 5 fases completas y **la entrega al back-end probada punta a punta**: primera corrida real contra el receptor (192 publicaciones entregadas de una, y después el pipeline completo ingesta → entrega sin fallos). Sobre el cierre de Fase 5 se sumaron el rediseño de tópicos (tópicos + subtópicos), el copy para redes sociales (`publicacion_redes`) ya ajustado a los 280 de un tweet, y tres auditorías de llamadas del pipeline (RSS/DB/Gemini) que dejaron 8 fixes de N+1. **268/268 tests, 96% de cobertura, `alembic check` limpio.**

**Siguiente:** el punto 2 del backlog priorizado de abajo (desacoplar el motor de IA). El punto 1 (segunda vía de ingesta) ya cerró: Perfil está de alta y en producción.

**Pendiente operativo, fuera del código:** elegir dónde se despliega (VPS pago vs. capa gratuita) y armar el `.env` de producción con la `GEMINI_API_KEY` real y la `WEBHOOK_URL` del back-end — hoy apunta a `localhost`.

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

**🚧 EN CURSO.** Etapas: **1 ✅** la rebanada vertical completa con el adaptador `openai_compatible`, la tabla `modelo_ia`, el alta con sondeo y `Sintesis.modelo_usado` · **2 ✅** Gemini nativo, la credencial única y las opciones por adaptador · **3 ⬜** Anthropic nativo · **4 ⬜** retirar el camino histórico.

La etapa 2 midió lo que estaba pendiente: **los tres caminos a Gemini son equivalentes** —8,05 s el nativo, 8,55 s el compatible, 8,98 s el histórico sobre el mismo prompt de 18.447 tokens, mismos 4 ángulos en las doce corridas— y la palanca de razonamiento del nativo **funciona y se paga**: de 0 tokens con `LOW` a 6.267 con `HIGH`, con el triple de latencia.

**El hallazgo que cambia la prioridad de la etapa 4**: en seis rondas, el camino histórico tardó **486 segundos** en una y no falló, porque `_llamar_gemini` no tiene timeout. Es la única llamada sin límite de tiempo del pipeline, y `llamar_modelo` la reintenta tres veces sobre un ciclo de 15 minutos. Retirarlo dejó de ser solo higiene.

Detalle de las decisiones en `change_logs.md`. Dos que conviene tener a mano:

- **El camino histórico de Gemini queda intacto y es el default.** Sin filas activas en `modelo_ia`, la síntesis va por donde iba siempre. No es una promesa: es el camino por defecto de una base que no configuró nada.
- **`POST /modelos` no es un CRUD**: sondea antes de aceptar y descubre solo cómo pedirle estructura al proveedor. Medido: la capa de compatibilidad de Anthropic responde 200 e **ignora `response_format` en silencio**.

> ⚠️ **Los tres endpoints de `/modelos` no tienen autenticación**, como el resto de la API, y son los primeros que aceptan una URL arbitraria para que el motor la llame. Hay puesto: enum cerrado de adaptadores, prefijo obligatorio para `api_key_env`, validación de `base_url` (solo http/https, sin credenciales embebidas, sin link-local), respuestas que no publican `api_key_env` ni `base_url`, y errores que no reflejan el cuerpo del proveedor.
>
> **Nada de eso reemplaza la autenticación.** Quien pueda hacer POST puede apuntar `base_url` a su propio servidor con un `api_key_env` válido y quedarse con la key de IA del operador. Hasta que exista auth —punto 9—, esto se despliega en una red donde solo llega el operador.

### 3. El alta de medios la hace el operador, no el repo

Hoy `scripts/seed_medios.py` trae seis medios argentinos hardcodeados. Eso significa que **el repo acepta los términos de uso de esos seis en nombre de quien lo despliegue**, y esa no es una decisión que le corresponda: quien puede aceptarlos es el operador de la instancia.

La revisión de términos del 19-20/08 lo dejó a la vista: varían muchísimo entre medios —Clarín licencia solo títulos y links, Perfil pide links de vuelta, Ámbito no tiene contrato de reuso, La Izquierda Diario bloquea crawlers de IA— y cuál es aceptable depende del uso que le dé cada operador. Ver `change_logs.md`.

**Qué se construye**: endpoints de alta, baja y listado de medios con su lógica de persistencia. El roster deja de venir en el repo.

**La decisión de diseño que importa**: el endpoint **no puede ser un CRUD que registra lo que le mandan**. Todo lo aprendido en la etapa 5 dice que tiene que sondear e informar antes de aceptar:

- ¿El feed responde? (`/feed/internacionales` de Perfil da 404, y está publicado en su propia página de RSS)
- ¿Trae `content:encoded`? Determina si hace falta `extraer_por_url` — la bandera que marca los medios donde vamos a buscar el cuerpo que ellos eligieron no publicar
- ¿Qué dice su `robots.txt`? `crawl-delay`, bloqueos de crawlers de IA, reservas de TDM
- ¿Cuántos ítems trae y qué ventana temporal cubren? Distingue una ventana móvil de un archivo

Así el alta pasa de *"registrá esto"* a *"esto es lo que encontramos, decidí vos"*, que es la delegación informada. Un CRUD pelado dejaría dar de alta un medio con extracción sin que el operador se entere de que ese medio licencia solo títulos y links.

**Tres cuidados:**

1. **El roster actual carga conocimiento medido** que no se puede perder: la trampa de los feeds de sección de TN (responden 200 pero ignoran la sección), el `?outputType=xml` obligatorio de Ciudad Magazine, y la lección de que el feed general ya cubre lo fresco. Pasa a documentación de ejemplos; no se borra.
2. **Migración**: la instancia que corre hoy tiene seis medios cargados. Sacar el seed no puede dejarla vacía.
3. **Seguridad**: sería el primer endpoint que hace que el motor **busque una URL arbitraria a pedido de quien llame**, y hoy la API no tiene autenticación en ninguno de sus 8 endpoints. Un alta abierta es un vector de SSRF y de uso del motor como proxy hacia terceros. Necesita autenticación y validación del destino desde el diseño, no después.

**Va después del punto 2**, por decisión del usuario.

### 4. `agrupar_pendientes` cuadrático + índice de pgvector — el primer síntoma real al sumar medios

`tech_stack.md`, puntos 9 y 3. Compara cada noticia suelta contra todas las demás de la ventana abierta; medido: 3,6 s con ~200 sueltas, proyectado ~14 s con 400 y cerca de un minuto con 800. Con 6 medios no se nota. La salida es acotar candidatos con el índice HNSW/IVFFlat de pgvector (hoy inexistente a propósito, porque nada lo necesita) en vez de comparar contra todos — los dos puntos van juntos porque uno es la causa y el otro la solución.

**No se implementa todavía.** Se vigila el tiempo de la corrida de agrupamiento (ya logueado) a medida que se sumen medios vía el punto 1, y se ataca cuando el número se acerque a los segundos que empiezan a competir con el ciclo de 15 minutos del scheduler — no antes.

### 5. Eventos de varios días generan clusters sucesivos — decisión de producto, no de escala técnica

`tech_stack.md`, punto 10. Un cluster cierra a las 12 h; una historia larga (la muerte de Jorge Messi cubrió varios días) produce publicaciones sucesivas que pueden solaparse. Con más medios hay más cobertura y más eventos largos, así que el riesgo crece con el volumen — pero la solución no es técnica (¿son hechos distintos de verdad, o el mismo hecho que el producto debería seguir mostrando junto?), es una conversación con el equipo de producto/back-end sobre qué comportamiento quieren. Revisar con síntesis reales a la vista antes de proponer un diseño.

### 6. Rate limit del proveedor al sumar medios

`tech_stack.md`, punto 5 (el costo ya está resuelto — precálculo, no on-demand — pero el rate limit sigue vigente). Hoy una corrida sintetiza todos los clusters publicables de una vez (medido: hasta 26) y el proveedor limita por minuto; ya hay backoff con `tenacity`. Con más medios, más clusters publicables por corrida, más chance de pegarle al límite seguido en vez de ocasionalmente. Vigilar la tasa de reintentos por rate limit en los logs a medida que se sumen medios; si se vuelve frecuente, ahí se decide entre escalonar la síntesis o pasar a un tier con más cupo.

**Se cruza con el punto 6-bis**: la cadena de fallback es una de las salidas posibles a este problema, y la otra mitad de la respuesta.

### 6-bis. Multimodelo: varios proveedores vivos a la vez, y a cuál mandarle qué

**Abierto el 21/08/2026, al decidir la credencial única de la etapa 2 del punto 2.** Ver `change_logs.md`.

El motor puede tener varias filas en `modelo_ia`, pero **una sola credencial configurada por vez**: `MODELO_API_KEY` tiene un solo valor, y una key de Groq no sirve en Gemini. Cambiar de proveedor es cambiar ese valor. Alcanza de sobra mientras cambiar de modelo sea algo raro, que es el caso.

Lo que ese diseño deja afuera es tener **dos proveedores distintos vivos en el mismo instante**, que es lo que necesita la cadena de fallback: si el de `prioridad` 1 pega contra su rate limit, caer al 2 sin perder el trabajo de la corrida.

**Por qué no se hizo ahora, y el argumento es del usuario**: la cadena no termina en "si falla, probá el siguiente". Para que sirva de verdad hay que decidir *cuánto* mandarle a cada proveedor según los créditos que le queden — cuántos clusters van a uno y cuántos al otro—, y eso es lógica nueva de peso que no corresponde arrastrar dentro del desacoplamiento.

**Lo que ya está puesto y no hay que rehacer**: la columna `prioridad` (hoy solo desempata), la columna `api_key_env` con su default, y `leer_api_key` aceptando la forma con sufijo (`MODELO_API_KEY_GROQ`). El día que se implemente, es **exponer un campo en el alta**, no migrar la tabla ni rehacer la validación. Las instancias que hoy tengan filas con nombres sufijados siguen funcionando sin tocar nada.

**Lo que sí habría que revisar**: `PATCH ?activo=true` sondea contra el proveedor justamente porque con credencial única la variable no dice de quién es la key. Con nombres por proveedor esa comprobación vuelve a poder ser barata — pero conviene medir antes de aflojarla.

### 7. Solo si se suma más de una réplica — no lo dispara sumar medios por sí solo

Tres puntos de `tech_stack.md` (1, 4 y 6: engine de BD síncrono, scheduler embebido, modelo de embeddings en memoria por worker) comparten la misma condición: importan si el despliegue pasa de una réplica a varias, no por la cantidad de medios que se ingieran con la réplica única actual. Fase 5 fijó a propósito un solo proceso Uvicorn sin `--workers`; mientras eso no cambie, estos tres quedan correctamente diferidos.

### 8. Purga de cuerpos — borrar el texto ajeno una vez que cumplió su función

**Último en la lista a propósito: hoy no genera inconvenientes.** No son tantas noticias y el espacio que ocupan tampoco aprieta. Se hace cuando alguna de las dos cosas cambie.

Borrar **solo `contenido_limpio`** —no la noticia— cuando su cluster ya cerró, se sintetizó y se entregó, más un margen. Medido al 20/08/2026: **17,5 MB de texto ajeno**, del cual el **71% (3.186 de 4.485) es de artículos que nunca formaron cluster y ya no pueden formarlo**, porque quedaron fuera de la ventana.

Sobrevive todo lo demás —título, URL, fecha, categoría y el `embedding`—, así que `GET /search` no se entera y las noticias siguen existiendo como registro.

**Rompe un solo consumidor**: el corpus de TF-IDF ajusta sobre `select(Noticia)` sin filtro ([preprocessing.py:105](src/services/preprocessing.py#L105)). Hay que decidir entre incluir las purgadas solo con título o excluirlas con una ventana móvil.

**El costo real de purgar** no es perder el texto para el producto —ya cumplió— sino **no poder revectorizar si algún día se cambia `EMBEDDING_MODEL`**: habría que re-ingerir, y lo que salió de la ventana del feed no vuelve.

Vale anotar el otro motivo por el que existe este punto, que no es técnico: reduce cuánto texto de terceros conservamos y por cuánto tiempo. Ver la revisión de términos de uso en `change_logs.md`.

### 9. El mail de alertas lo elige el operador, no el `.env` del repo

Hoy `ALERT_EMAIL_TO` es una variable de entorno única: quien despliegue el motor recibe las alertas en la casilla que quedó configurada al armar el contenedor, y cambiarla exige tocar el `.env` y reiniciar. Para una instancia propia alcanza; para el escenario que abren los puntos 2 y 3 —cada operador con su modelo y con su roster de medios— no, porque **las alertas son suyas: hablan de sus medios, sus feeds y sus corridas**.

**Qué se construye**: que la casilla de destino se pueda leer y cambiar en caliente, sin redeploy.

**Los cuidados, que acá pesan más que en otros puntos**:

- **La API no tiene autenticación en ninguno de sus endpoints.** Un endpoint que cambie el destino de las alertas deja que cualquiera **desvíe los avisos** —y quien los desvía, los apaga— o **los apunte a un tercero**, convirtiendo al motor en un emisor de mails no solicitados con nuestras credenciales SMTP. Es el mismo problema de fondo que el SSRF del punto 3 y probablemente se resuelvan juntos.
- **Verificar la dirección antes de usarla**, o un typo silencia las alertas sin que nadie se entere hasta que algo se rompa y no llegue el aviso.
- Conviene decidir a la vez si el destino es **uno o varios**, y si `SMTP_*` sigue siendo del repo o también pasa al operador: hoy las credenciales de envío y la casilla de destino están en el mismo lugar, y esto las separa.
