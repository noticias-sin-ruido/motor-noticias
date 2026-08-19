# 📜 Change Log — Decisiones de diseño

Historial de decisiones tomadas por fase: qué se evaluó, qué se descartó y por qué. El estado general de cada fase vive en `roadmap.md`; acá está el razonamiento detrás.

---

## Fase 1 — Persistencia y Modelado de Datos

**Fixes aplicados para dejar Fase 1 funcional** (SQLModel 0.0.39):
- **Relaciones** (`medio.py`, `noticia.py`, `cluster.py`): se sacó `from __future__ import annotations` y `Mapped[...]`. Con anotaciones diferidas (PEP 563), SQLModel no logra resolver la clase destino de la relación (la trata como string literal). Se usa `List["X"] = Relationship(...)` simple, como recomienda la propia doc de SQLModel.
- **`Sintesis` (JSONB)**: `puntos_clave` y `comparativa_enfoques` usan `JSONB().with_variant(JSON(), "sqlite")` — JSONB real en PostgreSQL, JSON genérico en SQLite (los tests en memoria no soportan JSONB).
- **`database.py`**: engine perezoso vía `get_engine()`, no se crea al importar el módulo — así `DATABASE_URL` solo es obligatoria cuando se usa la BD real, y los tests corren sin necesitar Postgres levantado.

---

## Fase 2 — Ingesta de Noticias

### Fuente de datos
Se descartaron: scraping de páginas completas, APIs de noticias comerciales (NewsAPI, GNews, Mediastack) y Google News RSS. Motivo principal: riesgo legal/ToS al extraer contenido completo de la página de un medio sin permiso explícito. Las APIs comerciales tampoco resuelven esto del todo: casi ninguna da texto completo (solo título/snippet) por acuerdos de licencia con los medios, así que igual habría que terminar visitando la página original. Se optó por **RSS directo de cada medio** (el modelo `Medio.feed_rss` ya lo asumía) — es el canal legalmente más seguro, porque el medio lo publica a propósito para sindicación de terceros.

### Medios elegidos — 6 activos (3 generales + 3 de espectáculos)
Criterio de admisión: el feed debe traer el artículo completo vía el tag `content:encoded`. Sin cuerpo completo no hay enfoque editorial que comparar, que es todo el valor del producto. Seed en `scripts/seed_medios.py`:

**Generales:**
- La Nación — `https://www.lanacion.com.ar/arc/outboundfeeds/rss/`
- TN — `https://tn.com.ar/feed/`
- El Cronista — `https://www.cronista.com/files/rss/news.xml` (~1.200 a 8.500 caracteres por nota)

**Espectáculos / sociedad:**
- Revista Gente — `https://www.gente.com.ar/feed/` (los artículos viven en `revistagente.com`; ~2.500-5.500 caracteres)
- Revista Paparazzi — `https://www.paparazzi.com.ar/feed/` (~3.300-5.300 caracteres)
- Ciudad Magazine — `https://www.ciudad.com.ar/arc/outboundfeeds/rss/?outputType=xml` (100 items, 97 con contenido; el parámetro `?outputType=xml` es obligatorio, sin él da 404)

**Por qué se sumaron medios de espectáculos.** Con solo 3 medios generales, ningún cluster llegaba a 4 medios y solo 5 llegaban a 3. Se temía que el vertical de farándula formara clusters aislados sin cruce con las noticias generales, pero **los datos desmintieron esa hipótesis**: el cluster más grande de la medición resultó ser la muerte del representante de modelos Leandro Rud, con 11 noticias de **5 medios distintos**, mezclando La Nación, TN y Revista Gente. Los eventos de sociedad los cubren ambos tipos de medio, y ahí la comparativa de enfoques es más rica.

### Medios evaluados y descartados
Alrededor de 20 medios argentinos evaluados; solo 6 pasaron el filtro. El `content:encoded` con cuerpo completo es raro en la prensa argentina.

- **Sin `content:encoded`** (solo `description` corta): **Clarín** (feed general + 5 feeds por sección probados, ~99-269 caracteres), Página12 (60-290), Perfil (~350-530), Ámbito Financiero (~212-237), iProfesional (~130-140), La Gaceta (112 items, ninguno con cuerpo), El Economista (104 items, sin cuerpo y además sin `guid`).
- **Sin RSS público** (solo sitemaps, que no sirven porque no traen cuerpo): A24, El Destape, Los Andes, BAE Negocios, La Política Online, Minuto Uno, C5N, El Litoral, Crónica, Pronto.
- **Bloqueo de acceso automatizado**: **La Voz del Interior** — usa Arc XP (el mismo CMS que La Nación, así que técnicamente el feed debería existir), pero devuelve 403/530 en todas las rutas, **incluido el sitemap que su propia `robots.txt` declara**, desde dos vías de red distintas y con o sin User-Agent de navegador. Se decidió **no forzarlo**: toda la Fase 2 se apoya en que el RSS es seguro porque el medio lo publica a propósito para sindicación; si bloquea activamente el acceso programático está señalando lo contrario, y sortear ese bloqueo sería el mismo terreno que descartamos al rechazar el scraping.
- **Mezcla de países sin filtro confiable**: **Infobae** — no tiene sección RSS separada para Argentina (URL de sección da 404; el link que su propia página `/argentina/` publica apunta al feed general). Ni el prefijo de país en la URL ni su ausencia son señales confiables: de una muestra de 8 items, ninguno tenía `/argentina/`, y varios sin prefijo caían en secciones por *tema* (`/opinion/`, `/america/agencias/`). Un filtro por prefijo dejaría pasar demasiado ruido o descartaría contenido argentino real.

### User-Agent propio en las peticiones
`_descargar_feed` manda un User-Agent que identifica al proyecto: `SinRuido/1.0 (+URL del repo) feed-reader`. Motivo: Paparazzi rechaza con 403 el User-Agent por defecto de `httpx` (`python-httpx/...`), y varios medios hacen lo mismo como higiene básica. **A propósito no se imita un navegador** — identificarse con nombre y URL de contacto es la práctica estándar de un lector de feeds y es lo opuesto a esconderse; hacerse pasar por Chrome para sortear un bloqueo sí sería evasión. Verificado que el UA honesto funciona con los 6 medios.

### Contenido y limpieza
- Se usa el HTML de `content:encoded` en vez de scrapear la página del medio.
- Limpieza de ese HTML a texto plano: **BeautifulSoup**, no `trafilatura`/`newspaper4k`. Esas dos últimas están pensadas para extraer el artículo "adivinando" cuál parte de una página completa y ruidosa (nav, ads, comentarios) es el contenido real; `content:encoded` ya viene aislado por el propio medio, así que alcanza con un parser simple (`BeautifulSoup(...).get_text()`).
- `trafilatura` y `newspaper4k` quedan en `requirements.txt` reservados, sin uso activo — solo se usarían si en el futuro se suma un medio que no traiga `content:encoded`.

### Deduplicación
Campo `guid` en `Noticia` (además de `url`, ya único). El `guid` de un item RSS es más estable que la URL entre lecturas del feed (la URL puede cambiar por parámetros de tracking o redirecciones), y no todos los medios usan el link como guid.

### Noticias "en vivo" (liveblogs / minuto a minuto): se descartan
Contienen información en desarrollo, no un hecho cerrado — demasiado complejo de seguir bien para esta etapa. Se filtran con un heurístico de palabras clave en el título (case-insensitive):
- La Nación: "en vivo" (minúscula, con ":" después).
- TN: título con "vivo" y/o el emoji 🔴 (punto rojo, referencia a luz de cámara prendida) al inicio del título.
- El Cronista y los 3 medios de espectáculos: sin indicios de cobertura en vivo en las muestras revisadas. No se agrega filtro específico — quedan cubiertos por el heurístico genérico si llegara a aparecer alguno.

El heurístico está funcionando en producción: en las corridas reales filtró notas en vivo de La Nación, TN y Ciudad Magazine.

### Scheduler
- APScheduler, un solo job con frecuencia uniforme de **15 minutos** para todos los medios. Motivo: un RSS es una ventana de los últimos ~20-50 items, no un log completo — pollear muy poco seguido arriesga perder artículos que se cayeron de la ventana antes de leerlos.
- **Corre embebido** en el proceso de `uvicorn` (dentro del `lifespan` de FastAPI, junto a `init_db()`). Más simple de operar mientras haya una sola réplica de la API. Riesgo de multi-réplica anotado en `tech_stack.md`, punto 4 de Escalabilidad.

### Manejo de errores por medio
- Reintentos con backoff dentro del mismo fetch (`tenacity`, 2-3 intentos) antes de dar por fallido ese medio en el ciclo — no debe tumbar la corrida completa ni afectar a los demás medios.
- Si se agotan los reintentos, se loguea **y se envía una alerta por mail** usando `smtplib` (stdlib, sin dependencia nueva) al correo del proyecto: `nsinruido@gmail.com`.
- El propio ciclo de 15 minutos ya actúa como reintento natural entre corridas.
- **Cola de mensajes (RabbitMQ/Celery/SQS): descartada por ahora** — más infraestructura de la que este volumen justifica (4 medios, fetch idempotente, sin necesidad de entrega garantizada entre servicios).

### Endpoint manual `POST /ingest`
Sirve para (a) probar el pipeline a mano durante desarrollo sin esperar el próximo ciclo del scheduler, y (b) como fallback operativo si el scheduler se cae.

### Fuera de alcance de Fase 2 (pasa a Fase 3)
- Detectar si dos artículos de medios distintos hablan del mismo hecho (clustering semántico). Fase 2 deduplica el mismo artículo consigo mismo (por `guid`/`url`), no compara contenido entre artículos distintos — comparar títulos/snippets de forma literal no funciona bien (un mismo hecho se titula muy distinto según el enfoque editorial), por eso se reserva para embeddings en Fase 3.
- Ciclo de vida del `Cluster` (cuánto tiempo queda `"abierto"` esperando cobertura de otros medios). Boceto preliminar para Fase 3/4: deadline en tiempo real desde `Cluster.fecha_creacion` (no contar ciclos de polling, para no atar la regla a la frecuencia del scheduler) y agregar un tercer estado (ej. `"descartado"`) para clusters que no llegan al umbral mínimo de medios, en vez de dejarlos `"abierto"` indefinidamente. Falta decidir el umbral exacto (¿todos los medios, o algo más laxo tipo 2-3 de 4?).

### Pendientes
- [ ] Cuenta/credenciales SMTP emisoras para el mail de alertas (host, usuario, password) — el destino (`nsinruido@gmail.com`) y la librería (`smtplib`) ya están decididos.
- [ ] Qué hacer cuando un `guid` ya ingerido vuelve a aparecer con contenido distinto (liveblogs que se actualizan) — probablemente moot al haber quedado excluidas las notas "en vivo".

### Validación contra Postgres real
Validado contra Postgres 16 + pgvector real vía `docker-compose.yml` (que estaba vacío/sin contenido real hasta esta validación — se creó desde cero). Ver `specs/validacion_manual.md` para el paso a paso completo y las queries de chequeo. Extensión `vector` y tablas se crean correctamente, `POST /ingest` corre de punta a punta contra los feeds reales.

**Hallazgo — Clarín dio 0 noticias.** El pipeline funcionó como está diseñado (descartó los items por no tener cuerpo completo), y a raíz de esto se agregó un contador `sin_contenido` a las stats de `ingerir_medio` — antes esos items se descartaban en silencio, sin ninguna señal — más un log de warning si el 100% de la ventana de un medio queda sin contenido en un ciclo.

> ⚠️ **Corrección de un diagnóstico equivocado.** La primera explicación registrada acá fue que *"la ventana del feed estaba compuesta 100% por horóscopos"*, es decir, mala suerte puntual. **Eso era incorrecto.** Verificaciones posteriores (3 corridas en días distintos, más los 5 feeds por sección de Clarín) mostraron que el feed de Clarín **no tiene `content:encoded` en absoluto**: en una corrida el contenido era periodismo real (Milei en Colombia, Stanford, Mamdani) y aun así vino sin cuerpo, solo `description` de 99-269 caracteres. Los horóscopos de la primera observación fueron una coincidencia que llevó a la conclusión errónea. Clarín quedó descartado del line-up. **Lección de método: no diagnosticar sobre una sola observación**, sobre todo cuando la explicación disponible es cómoda.

---

## Fase 3 — Vectorización y Clustering (diseño cerrado, implementación pendiente)

Todas las decisiones de abajo se calibraron contra noticias reales ya ingeridas (277 → 494 → 620 noticias en tres mediciones sucesivas), no sobre intuición.

### spaCy NO se usa para los embeddings
Los vectores de spaCy son **estáticos por palabra** (tipo GloVe) y el vector del documento es el promedio de sus palabras: se pierde el orden y el contexto ("el juez procesó al empresario" ≈ "el empresario procesó al juez"). Para similitud semántica entre textos, los *sentence transformers* codifican la oración entera con atención y son sustancialmente mejores. spaCy queda disponible para **NER** (entidades nombradas) como señal complementaria si hace falta desempatar, no como motor de similitud.

### Modelo de embeddings: multilingüe, no el inglés
El comentario original en `noticia.py` nombraba `all-MiniLM-L6-v2`, que está entrenado esencialmente **en inglés** — con noticias en español eso degrada la calidad de forma silenciosa. Se usa **`paraphrase-multilingual-MiniLM-L12-v2`**, que también es de **384 dimensiones**, así que el esquema y la columna `Vector(384)` quedan intactos. Alternativa equivalente si hiciera falta cambiar: `intfloat/multilingual-e5-small` (también 384).

Verificado empíricamente: vectorizar 620 noticias en CPU toma segundos. El costo de cómputo de Fase 3 es despreciable — el gasto real del proyecto aparece recién en Fase 4 con el LLM.

### Qué texto se vectoriza: título + primeros 500 caracteres
No el `contenido_limpio` completo, por dos razones: (a) estos modelos truncan a ~256-512 tokens, así que un artículo de 8.000 caracteres se corta solo y no se controla dónde; (b) el periodismo usa pirámide invertida — el qué/quién/dónde está en el título y el primer párrafo, mientras que el resto es contexto, declaraciones y color, que es justamente lo que *diferencia* editorialmente a dos notas del mismo hecho y por lo tanto agrega ruido al agrupar.

### Umbral de similitud: 0.75 (coseno)
Calibrado sobre 38.226 pares reales. Distribución: mediana 0.20, percentil 95 en 0.49, percentil 99 en 0.64, percentil 99.9 en 0.83, máximo 0.96 — buena separación entre el grueso de pares no relacionados y la cola de coincidencias reales.

Qué hay en cada banda (pares entre medios distintos):
- **0.85+**: aciertos limpios (Venezuela/diálogo chavismo-oposición, debate del Senado por propiedad privada).
- **0.75-0.85**: mayormente aciertos (visita del Papa, Milei-Noboa, detenidos frente al Congreso). Cerca de 0.82 empiezan falsos positivos *temáticos*: dos encuestas económicas distintas dieron 0.8261, dos columnas de opinión política distintas 0.8197 — mismo tema, no el mismo hecho.
- **0.65-0.75**: zona ambigua, "misma historia en desarrollo, distinto sub-evento".
- **Falso negativo conocido**: el mismo partido de fútbol (Unión-Lanús) titulado por dos medios quedó en 0.6462 — los resultados deportivos se redactan de formas muy distintas. Sugiere que un umbral por sección sería mejor (deportes ~0.65), pero se descarta por ahora como complejidad prematura.

### Centroide, no vecino más cercano
Con linkage `single` (vecino más cercano) apareció **encadenamiento** real: un cluster de 13 noticias pegoteaba por transitividad seis notas de opinión económica distintas ("La cadena de garantías", "Los pesos no van a crecer", "El Banco Central no es el lugar para discutir empleo", "cuánto cobran los empleados de comercio"). Con linkage `average` (centroide) los clusters quedan coherentes. **Se compara contra el centroide del cluster**, no contra su miembro más parecido.

### El cluster se crea recién con el segundo artículo
El 62-69% de las noticias no tiene ningún par en ninguna medición — la mayoría de lo que publica un medio no lo cubre nadie más. Si cada noticia creara su cluster al llegar, habría cientos de clusters de un solo miembro para después descartar. En cambio la noticia queda con `cluster_id = NULL` (el modelo ya lo permite) y el `Cluster` se crea solo cuando aparece el segundo artículo. Así `Cluster` existe únicamente para eventos con cobertura múltiple.

### Cobertura mínima: 2 medios
Sobre 620 noticias con umbral 0.75 y centroide: 84 clusters con 2+ noticias, de los cuales 46 tienen 2 medios distintos, 8 tienen 3, 2 tienen 4 y 1 tiene 5. Exigir 3 medios dejaría 11 clusters publicables; exigir 4 dejaría 3. **Con mínimo 2 medios salen 57 clusters publicables**, que es el volumen que el producto necesita. (Con solo los 3 medios generales, exigir 4 daba literalmente cero — fue uno de los motivos para sumar los medios de espectáculos.)

### El alcance de búsqueda son los clusters abiertos, no una ventana de minutos
Se evaluó una ventana de 30 min a 2 h para buscar vecinos, razonando que el scheduler pollea cada 15 min. **Eso confunde dos cosas independientes**: cada cuánto *descubrimos* una noticia (15 min) no tiene relación con cuánto puede tardar otro medio en cubrir el mismo hecho (La Nación publica a las 10:00, El Cronista su análisis a las 15:00). Con ventana de 30 min esas dos notas nunca se ven y se generan dos clusters del mismo evento.

Por eso el parámetro real es **cuánto tiempo un cluster permanece abierto** (6-12 h desde `fecha_creacion`), y la búsqueda se hace sobre las noticias de clusters abiertos más las noticias sueltas recientes. Un solo parámetro en vez de dos, consistentes por construcción.

### Publicar y cerrar son momentos distintos
Objeción válida: si el cluster queda abierto 6-12 h, la noticia llega tardísimo al usuario final. Se resuelve **desacoplando los dos disparadores**:
- **Publicación**: apenas el cluster alcanza 2 medios (puede ser a los 20 minutos).
- **Cierre**: 6-12 h después, cuando ya no se aceptan más aportes.

Si más tarde se suma otro medio, se regenera la síntesis y se reenvía por el webhook (el backend receptor ya maneja idempotencia por su cuenta, ver Fase 4). Para acotar el costo del LLM, la regla es **regenerar solo cuando entra un medio nuevo al cluster**, no con cada artículo: un segundo artículo de un medio ya presente no aporta un enfoque editorial nuevo, que es el valor del producto. Con 6 medios eso acota las regeneraciones a 5 como máximo por cluster.

### Sin índice HNSW por ahora
A esta escala (~400-600 noticias/día, unos cientos de vectores en juego dentro de la ventana abierta) un scan secuencial sobre vectores de 384 dimensiones se resuelve en milisegundos. Además, cuando se agrega un `WHERE` restrictivo (ej. `cluster.estado = 'abierto'`) el índice ANN pierde parte de su ventaja. El índice se vuelve necesario en el orden de las decenas de miles de vectores — ver `tech_stack.md`, punto 3 de Escalabilidad.

### scikit-learn queda sin uso en producción
Se usó `AgglomerativeClustering` para las simulaciones offline de calibración, pero el pipeline real hace **asignación incremental** (una noticia nueva contra los centroides existentes), no clustering batch. `scikit-learn` queda en `requirements.txt` sin uso activo, igual que `trafilatura`/`newspaper4k`.

### Decisiones tomadas durante la implementación

**No hizo falta migración para el estado `"descartado"`.** Se había anticipado como bloqueante, pero `Cluster.estado` es un `str` plano sin `CHECK` ni tipo `Enum` en la base, así que agregar un valor nuevo es solo cambiar el comentario del modelo. Alembic se implementó igual porque Fase 4 sí necesita columnas nuevas en `Sintesis` y ya había 620 noticias reales que no convenía perder.

**Adopción de Alembic sobre una base con datos.** El primer `--autogenerate` salió vacío: compara los modelos contra la base real, que ya tenía las tablas creadas por el viejo `create_all()`. La migración inicial se generó contra una base temporal vacía (creada y borrada para eso), se probó aplicándola desde cero ahí, y recién después se marcó la base real con `alembic stamp head` — sin perder las noticias.

**`init_db()` dejó de crear tablas.** Ahora solo habilita la extensión `vector` y falla con un mensaje explícito si falta migrar. Si hubiera quedado el `create_all()`, las tablas nuevas se crearían por fuera del control de Alembic y los `ALTER` nunca se aplicarían: el esquema real y las migraciones se irían separando en silencio.

**Orden del pipeline: cierre antes que agrupamiento.** En el job del scheduler, `cerrar_clusters_vencidos()` corre antes que `agrupar_pendientes()` para que un cluster ya vencido no capture noticias nuevas en la misma pasada.

**Las noticias sueltas se reevalúan en cada corrida.** No se agregó ningún campo del tipo `procesado_clustering`: una noticia que hoy no matcheó con nada puede matchear más tarde, cuando otro medio cubra el mismo hecho. Reevaluarlas es barato (unos cientos de vectores por ventana) y es justamente lo que permite capturar coberturas tardías.

**El modelo se carga de forma perezosa.** `get_modelo()` cachea la instancia y solo la crea en el primer uso: son cientos de MB de RAM que los tests y cualquier código que no vectorice no deberían pagar. Los tests mockean `vectorizar_textos` (la frontera con la librería externa) en vez del modelo entero, y corren en menos de medio segundo sin descargar nada.

### Validación con datos reales
- Vectorización: 620 noticias en **14,5 s** en CPU. Verificado en la base que quedaron con 384 dimensiones y norma exactamente `1.0000` (la normalización es lo que permite usar el producto punto como similitud coseno).
- Agrupamiento sobre la ventana de 12 h: **37 clusters, 30 publicables (81%)**, con uno de 4 medios distintos. El cluster más grande quedó en 6 noticias, sin encadenamiento.
- Flujo incremental probado de punta a punta: tras ingerir 29 noticias nuevas, 2 se sumaron a clusters ya existentes y se crearon 3 nuevos.
- `GET /search`: la consulta *"crisis diplomatica con Brasil"* devolvió 4 notas correctas (0.82-0.73) que **no contienen esa frase textual** — confirma que la búsqueda es por significado.
- **Falsos positivos observados, y por qué no preocupan tanto**: aparecieron clusters temáticos incorrectos (por ejemplo, dos recetas distintas de Ciudad Magazine agrupadas juntas), pero se concentran en clusters de **un solo medio**, que se cierran como `descartado` y nunca llegan al usuario. Coincide con lo previsto en el análisis de umbral: el daño queda contenido donde no se ve.

---

## Fase 3 — Cierre del análisis: fusión de clusters y reparto de responsabilidades con Fase 4

Resuelto el 8/8/2026 sobre una segunda corrida de datos reales. **Las dos recomendaciones del análisis anterior quedaron descartadas** y el problema de fondo resultó ser otro. El análisis original se conserva más abajo porque la corrección solo se entiende con él a la vista.

### La corrida
191 noticias nuevas (840 en total, 64 clusters, 185 agrupadas). El día trajo dos muertes de alta cobertura — Jorge Messi y Leandro Rud — que resultaron un caso de prueba mucho más exigente que el anterior.

### ❌ Descartado: exclusión por género
De 185 noticias agrupadas, la lista de segmentos propuesta (`columnistas`, `cocina`, `opinion`, `lifestyle`…) atrapa **3**, y 1 de los 14 clusters de un solo medio. El motivo es que **el segmento de URL identifica el tópico, no el género**: Ciudad Magazine publica recetas bajo `espectaculos` y La Nación entrevistas de psicología bajo `sociedad`. Los falsos positivos reales quedaban todos afuera (dos recetas distintas, dos entrevistas a Gabriel Rolón, el cronograma de las Leonas junto al de los Leones, dos cotizaciones del dólar, el horario y el canal de River-Tigre).

Además el único cluster multi-medio que la lista tocaría es el de la crisis con Brasil, y le sacaría justo la columna de opinión que mejor explicaba el conflicto.

**Y no hace falta**: los cinco casos son de un solo medio, así que `MIN_MEDIOS_CLUSTER` ya los descarta. El periodismo de servicio y la opinión no se replican entre medios, se autofiltran. La regla que ya existía cubre el problema que esta lista venía a resolver.

### ❌ Descartado: umbrales por tópico
Los dos falsos positivos multi-medio entraron con similitudes de **0.8420** (el blob de la crisis con Brasil) y **0.8857** (la muerte de Ignomiriello colada entre dos notas sobre Tagliafico). Pero hay clusters correctos cuyo peor miembro está en **0.8173** (los mensajes de despedida a Messi). **Los rangos se superponen: ningún umbral los separa**, ni global ni por tópico.

Y se cayó el ejemplo que sostenía la idea: el análisis anterior decía que deportes necesitaba un umbral *más bajo* por el caso Unión-Lanús (0.6462). Acá deportes produjo un falso positivo a 0.8857. Bajarle el umbral lo empeoraría.

### 🔴 El problema real: el algoritmo nunca fusionaba clusters
La muerte de Jorge Messi dejó **68 noticias repartidas en 20 clusters**. Al medir las similitudes entre centroides aparecieron **146 pares de clusters coexistentes por encima del umbral 0.75** — o sea que, según su propia regla, deberían haber sido uno solo:

```
0.9484  "Murió Jorge Messi, el padre de Lionel Messi: tenía 68 años"
        "Murió Jorge Messi, el padre de Lionel, a los 68 años"
0.9193  "La fuerte carta de Chiqui Tapia tras la muerte de Jorge Messi"
        "La carta de Claudio 'Chiqui' Tapia por la muerte de Jorge Messi"
```

La causa está en `_mejor_match()`: devuelve el mejor candidato **global** entre centroides de clusters y noticias sueltas. Si llega una noticia que matchea un cluster existente a 0.85 pero hay una suelta casi idéntica a 0.99, gana la suelta y nace un cluster paralelo. Nada volvía a unirlos.

El daño era concreto: el caso Chiqui Tapia partió un hecho de 4 medios en un cluster publicable de 2 medios y otro de 1 medio que se **descartaba**. Se perdía cobertura real por un artefacto del orden de evaluación.

### El reparto de responsabilidades entre clustering y Fase 4
Antes de elegir un umbral de fusión se replanteó la pregunta de fondo, y eso cambió el diseño:

> Un cluster grande no es un problema si Fase 4 puede separarlo. Si 27 notas hablan de la misma muerte pero 3 cuentan cómo fue, 3 quién era y 3 qué harán, esa separación es por **ángulo**, y distinguir ángulos es leer los textos — que es exactamente lo que hace el modelo de síntesis.

Esto resolvió el callejón sin salida del umbral. La similitud coseno mide **de qué habla** un texto, no **qué ángulo toma**: "cómo murió Jorge Messi" y "qué hará la AFA" comparten vocabulario casi idéntico. Por eso los rangos de aciertos y errores se superponen y ningún umbral los separa. El número no puede hacer ese trabajo; un LLM que lee los artículos sí.

De ahí el reparto que queda fijado:

| | Unidad | Optimiza | Herramienta |
|---|---|---|---|
| **Clustering** | el hecho y su cobertura | no perder cobertura (recall) | embeddings |
| **Fase 4** | el ángulo | precisión editorial | el modelo leyendo |

Un cluster amplio y limpio es **mejor** materia prima que varios fragmentos: le entrega al modelo las coberturas de todos los medios juntas, que es lo que permite comparar enfoques. Los 20 clusters fragmentados eran el problema; uno de 46 noticias con 5 medios es la solución, siempre que Fase 4 lo desagregue.

Esto además disuelve la disyuntiva "¿la unidad es el evento o la historia?", que era una elección forzada por pedirle al clustering un trabajo que no puede hacer. **El clustering agrupa el hecho; la síntesis define el evento.**

Verificado antes de cerrarlo: el modelo **ya soporta N síntesis por cluster** sin migración — `Sintesis.cluster_id` es una FK sin `unique` y `Cluster.sintesis` ya está declarado como `List["Sintesis"]`.

### Consecuencia: el mínimo de 2 medios se evalúa por ángulo
Si el cluster deja de ser lo que se publica, contar medios sobre el cluster deja de proteger. Ejemplo real: el ángulo *"la carta que Jorge Messi le escribió al Barcelona"* lo cubrió **solo TN** (2 notas). Hoy queda como cluster suelto de 1 medio y se descarta bien; una vez absorbido por el cluster grande, el conteo a nivel cluster da 5 y pasaría el filtro, publicando una síntesis de una sola voz.

La regla no se borra, se aplica en dos niveles:

- **cluster con ≥ `MIN_MEDIOS_CLUSTER` medios** → condición *necesaria*: justifica gastar una llamada al modelo. Se queda en `cerrar_clusters_vencidos()` como pre-filtro barato, porque si el cluster entero tiene un solo medio ningún ángulo adentro puede tener dos.
- **ángulo con ≥ `MIN_MEDIOS_CLUSTER` medios** → condición *suficiente*: esto sí se publica. Lo evalúa Fase 4, y descarta el ángulo, no el cluster.

### La fusión: dos intentos y una lección repetida
La primera implementación fusionaba de a pares, tomando el par más parecido y **recalculando el centroide** después de cada unión. Sobre datos reales se comió 46 noticias en un solo cluster: reprodujo exactamente el **encadenamiento** que el centroide vino a evitar a nivel de noticia, ahora a nivel de cluster.

La segunda decide todos los pares contra la **misma foto de centroides** (union-find, sin recalcular dentro de la vuelta). Baja de 10 fusiones a 6 y el cluster mayor de 46 a 27 — pero al medir el punto fijo se vio que **converge igual al mismo resultado** en 3-4 corridas. El criterio del centroide es intrínsecamente inestable: fusionar mueve el centroide y habilita la fusión siguiente.

Con el reparto de responsabilidades ya definido, esa inestabilidad dejó de ser un problema: el destino —el cluster amplio— es el que queremos. Así que la función **itera hasta el punto fijo dentro de la misma llamada**. Queda idempotente y el pipeline termina siempre en el mismo estado, en vez de consolidar de a poco a lo largo de varias corridas del scheduler y depender de cuántas alcanzaron a ejecutarse antes del cierre.

**Resultado medido:** 27 clusters → **17**, en una sola llamada (10 fusiones, la segunda pasada da 0). El mayor quedó en **46 noticias de 5 medios y las 46 mencionan a Messi — cero intrusos**. Los otros hechos (Enner Valencia, Simeone, Almada, el dólar, los desalojos) quedaron cada uno por su lado.

Sobrevive el cluster más viejo, para que fusionar no estire el plazo de cierre. Solo se tocan clusters abiertos: los cerrados ya pudieron haberse publicado.

### Reversibilidad
Se validó explícitamente antes de implementar, porque el diseño de Fase 4 se apoya en esto: **nada de esto toca el esquema**. Pasar de una síntesis por cluster a N —o volver atrás— no necesita migración. `UMBRAL_FUSION_CLUSTERS = 1.01` desactiva la fusión de hecho (el coseno nunca supera 1), el filtro por ángulo vive dentro del servicio de síntesis, y los clusters son reconstruibles en segundos porque los embeddings quedan persistidos en las noticias. Lo único no reversible desde este repo son las síntesis ya entregadas por webhook al backend del producto — de ahí que la entrega se diseñe con idempotencia.

---

## Fase 3 — Análisis previo: exclusión por género y umbrales por tópico (descartado)

Debate del 8/8/2026, **descartado por la sección anterior**. Se conserva porque documenta qué se evaluó y en qué se falló al leer los datos: la intuición sobre farándula se corrigió acá, y la de deportes se corrigió después, en sentido contrario al que decía esta misma sección.

### El disparador
¿Conviene detectar el tópico de cada noticia y usar un umbral de agrupación distinto según el tópico? La intuición inicial era: farándula más laxa (más ambigua), economía más rigurosa.

### Lo que dicen los datos

**El tópico es extraíble de la URL.** Cada medio usa segmentos consistentes: La Nación (`politica`, `economia`, `deportes`, `sociedad`, `espectaculos`…), TN (`politica`, `economia`, `deportes`, `show`, `policiales`…), El Cronista (`economia-politica`, `negocios`, `columnistas`), Ciudad (`espectaculos`, `cine-y-series`, `musica`), Paparazzi (`teve`, `romances`), Gente (`entretenimiento`).

**Pero 25 de 37 clusters (68%) cruzan más de un segmento** — casi siempre por vocabulario distinto para lo mismo: `espectaculos + show`, `espectaculos + teve`, `espectaculos + romances + show`, `entretenimiento + espectaculos`, `policiales + seguridad`, `economia + economia-politica + negocios`. Se resolvería con una tabla de normalización a tópicos canónicos, y el problema de "¿qué umbral uso si el cluster mezcla tópicos?" se esquiva aplicando el umbral **de la noticia entrante**, no el del cluster. O sea: la idea **es implementable**.

### La observación más importante: el problema es el *género*, no el tópico
Los falsos positivos reales que se observaron tienen todos el mismo perfil:
- El blob de economía → todas las notas venían de `columnistas`. **Columnas de opinión.**
- Las recetas agrupadas entre sí → sección `cocina`. **Periodismo de servicio.**

Dos columnas de opinión sobre el Banco Central siempre se van a parecer semánticamente: discuten el mismo sujeto sin reportar un hecho distinto. Subir el umbral de "economía" ayudaría a medias; el problema de fondo es que **una columna de opinión no es un evento**, y el producto compara cómo distintos medios encuadran *el mismo hecho*. Sin hecho no hay enfoques que comparar. Lo mismo aplica a recetas, horóscopos y notas de "cómo hacer X".

### Corrección a la intuición inicial sobre farándula
Sobre economía la intuición se confirma. **Sobre farándula los datos dicen lo contrario.** Los clusters de espectáculos a 0.75 fueron los más limpios de todos (Leandro Rud, Griselda Siciliani, Tuli Acosta, Thiago Medina: todos correctos y multi-medio). Hay una razón estructural: la farándula tiene **altísima superposición de entidades** — Wanda Nara aparece en muchas noticias distintas. Bajar el umbral ahí no agrupa mejor el mismo hecho, empieza a fusionar *hechos distintos sobre la misma persona*: en el barrido a 0.70 se coló una nota sobre Nora Colosimi dentro del cluster de Wanda Nara.

Si algo, espectáculos necesitaría un umbral **más alto**. El que sí necesitaría uno más bajo es **deportes**: el mismo partido Unión-Lanús quedó en 0.6462 porque un medio destaca los goles y el otro el marcador.

### Recomendación (en este orden) — ⚠️ ninguna se implementó
1. ~~**Exclusión por género, primero.** Sacar del agrupamiento los segmentos que no reportan eventos: `columnistas`, `opinion`, `cocina`, `recetas`, `horoscopo`, `lifestyle`.~~ **Descartada:** el segmento identifica el tópico, no el género, y `MIN_MEDIOS_CLUSTER` ya cubría el problema.
2. ~~**Umbrales por tópico, solo si después sigue haciendo falta** — esperando que `deportes` baje y `espectaculos` suba.~~ **Descartada:** los rangos de aciertos y errores se superponen, y deportes resultó necesitar lo contrario de lo que dice acá.
3. **Esperar a Fase 4 para calibrar.** Lo único que sobrevivió, aunque por una razón más fuerte que la prevista: no es que convenga esperar a Fase 4 para elegir mejor el umbral, es que **la separación por ángulo no es un problema de umbral** y le corresponde a Fase 4 hacerla leyendo los textos.

### Lección de método
Las dos recomendaciones se apoyaban en clusters de una sola corrida. Con una segunda corrida —y con dos muertes de alta cobertura, que estresan el agrupamiento mucho más que un día común— las dos se cayeron, y apareció un problema estructural que ninguna de las dos habría tocado. Es la segunda vez en el proyecto que un diagnóstico sobre una sola observación resulta equivocado; la primera fue Clarín y sus horóscopos.

---

## Fase 4 — Síntesis Neutra con IA (diseño cerrado, implementación pendiente)

### La unidad de publicación es el ángulo, no el cluster
Decidido al cerrar Fase 3 (ver arriba, "Reparto de responsabilidades"). El clustering entrega **el hecho y toda su cobertura**; Fase 4 lee ese material y lo separa en **ángulos**, emitiendo una síntesis por ángulo. Un cluster puede producir N síntesis, y el modelo ya lo soporta sin migración (`Sintesis.cluster_id` es una FK sin `unique`).

Dos consecuencias directas para la implementación:
- **El filtro de cobertura se evalúa por ángulo.** `MIN_MEDIOS_CLUSTER` sobre el cluster queda como pre-filtro barato (condición necesaria: evita gastar una llamada al modelo en algo que nunca va a publicar), pero el que decide qué se publica es el conteo de medios distintos **dentro del ángulo**. Se descarta el ángulo, no el cluster.
- **Hay que definir qué se le manda al modelo.** Un cluster puede traer decenas de artículos (medido: 46). Con `título + EMBEDDING_CHARS_CUERPO` son unos pocos miles de tokens; con el cuerpo completo, decenas de miles. Pendiente de decidir al implementar.

### El cálculo señala, el modelo juzga
Antes de llamar a Gemini se calcula evidencia sobre el cluster (`services/preprocessing.py`): el núcleo compartido, el vocabulario propio de cada medio vía TF-IDF, y qué entidades menciona en exclusiva o calla habiéndolas dicho otro. Esa evidencia entra al prompt junto con los cuerpos.

**No reemplaza al modelo, lo apunta.** Se evaluó que la comparativa saliera calculada y que Gemini solo la redactara, y se descartó: el cálculo no distingue *"omitió el nombre de la denunciante"* (decisión editorial grave) de *"omitió Instagram"* (un posteo incrustado). Esa distinción es criterio. El reparto queda igual que en Fase 3 — el cálculo aporta recall, el modelo aporta juicio:

| | Hace | No hace |
|---|---|---|
| TF-IDF + spaCy | señala candidatos: qué mirar | decidir qué importa |
| Gemini | verifica contra el cuerpo, juzga y redacta | descubrir las diferencias desde cero |

Por eso **se manda el cuerpo completo** y no un extracto: el texto es lo que permite verificar la pista. La evidencia sin el cuerpo es una afirmación que hay que creer; con el cuerpo es una hipótesis contrastable. El prompt dice explícitamente que las señales son pistas a verificar y pide citar la frase que respalda cada afirmación, para que la salida sea auditable.

**Que funcionó, medido sobre datos reales** (cluster de la vuelta de Messi a Rosario, 3 medios): Ciudad Magazine → `celia cuccittini, historia amor, esfuerzo` (la familia); Paparazzi → `mega operativo, a qué hora llega` (el espectáculo); TN → `helicóptero, operativo seguridad, traslado` (la logística). Tres encuadres distintos del mismo hecho, detectados contando palabras.

**Que hubo que corregir, y por qué importa:**
- **El IDF tiene que salir del corpus completo, no del cluster.** Con 3 documentos no hay forma de saber qué palabra es rara: ajustándolo dentro del cluster, los "términos distintivos" daban `no, le, pero, estaba`. Con el IDF de las 840 noticias, `matanza, virrey, juvenil, hermana`.
- **Las entidades hay que unificar entre medios, no dentro de cada uno.** NER devolvió `Lara Agustina Ledesma` en un medio y `Iara Agustina Ledesma` (errata de etiquetado) en otro. Unificando por medio, el sistema informaba que el segundo medio *omitía* un nombre que en realidad había publicado. Un falso omitido en una nota sobre abuso sexual no es un detalle: se unifica con un vocabulario común a todos los medios.
- **La forma canónica es la más mencionada, no la más larga.** Los epígrafes en mayúsculas dejan variantes basura (`THIAGO MEDINA Y EL`) que ganaban por longitud y pasaban a representar a la entidad.
- **Filtrar autorreferencias del medio y plataformas.** El pie de página de Ciudad ("seguinos en el canal de WhatsApp") salía como su rasgo más distintivo.
- **`es_core_news_md` en vez de `sm`.** El chico confundía nombres (`Iara` por `Lara`) y etiquetaba verbos como entidades.

**Costo medido** sobre 21 clusters publicables reales: 68.534 tokens de entrada por corrida completa, o sea **US$0,007 a 0,021**. El tope de `SINTESIS_NOTAS_POR_MEDIO` es lo que lo sostiene: el cluster más grande tenía 63 noticias y se mandan 10 (2 por medio). Se acota **por medio y no en total** a propósito — un recorte global se llevaría puesto al medio que publicó una sola nota, y quedarse sin un medio es quedarse sin comparativa.

### Esquema: `Sintesis` es el ángulo, y las noticias que lo respaldan van en tabla
Migración `979689aeb928`. `Sintesis` suma `titulo_angulo` (lo que ve el usuario), los campos de entrega, y una tabla intermedia `SintesisNoticia`.

**Por qué tabla y no una lista de ids en JSON.** La relación es genuinamente muchos-a-muchos: un minuto a minuto respalda el hecho y las reacciones a la vez, y cada ángulo se apoya en varias notas. Pero la razón de fondo es que **de ahí sale la regla que decide qué se publica**: un ángulo necesita `MIN_MEDIOS_CLUSTER` medios distintos, y eso es un `count(distinct medio_id)` sobre el join. Con ids sueltos en un JSON habría que traer todo a memoria para contarlo y nada garantizaría que las noticias referenciadas existan.

**El autogenerado de Alembic estaba mal y había que corregirlo.** Agregaba `titulo_angulo`, `enviado_backend` e `intentos_envio` como `NOT NULL` sin `server_default`: contra una base que ya tuviera síntesis, PostgreSQL no puede completar una columna `NOT NULL` sin saber con qué, y la migración falla. Se agregó `server_default` y se lo quita en el mismo `upgrade()`, para que el esquema quede igual al modelo (que define esos valores por defecto en Python). Probado de las dos formas contra una base temporal: desde cero, y con una fila preexistente que quedó correctamente completada. Es exactamente el caso que `mission.md` advierte al pedir revisar siempre el autogenerado.

### Se publica al alcanzar 2 medios, no al cerrar el cluster
Medido sobre 51 clusters publicables reales:

| | |
|---|---|
| Del 1er al 2do medio | mediana **1,33 h** · p90 4,40 h · máx 6,96 h |
| Clusters que nunca suman un 3er medio | **38 de 51 (75%)** |
| Re-síntesis si se publica al llegar a 2 medios | **16** en total (0,31 por cluster) |
| Ventana en que llega el último medio | mediana 1,97 h · máx 6,02 h |

Esperar el cierre significaba publicar a las ~13 h (1,33 h hasta ser publicable + 12 h de ventana), que para un producto de noticias es no llegar. Publicar temprano cuesta 16 llamadas extra sobre 51 clusters — centavos a los precios medidos.

**El disparador de la re-síntesis es el medio, no la noticia**: gatillar por nota nueva daría 97 re-síntesis contra 16 por medio nuevo, y sin ganar nada — una segunda nota de TN sobre un hecho que TN ya cubrió no aporta un enfoque distinto. El cluster de Messi tiene 63 noticias y 5 medios: por nota serían ~60 llamadas, por medio 3.

**Consecuencia sobre el cierre:** `cerrar_clusters_vencidos()` deja de ser la puerta de la publicación. `procesado` cambia de sentido — de *"listo para sintetizar"* a *"cerrado, ya no cambia"*. `descartado` sigue igual: nunca llegó a 2 medios y nunca publicó nada.

### La descomposición en ángulos se congela en la primera síntesis
Las re-síntesis reciben los ángulos ya existentes en el prompt y solo pueden **actualizar su contenido o agregar uno nuevo**; nunca re-partir lo ya publicado.

El motivo no es de integridad interna: el cluster no cambia y todos los ángulos siguen apuntando a él pase lo que pase. **Es de identidad ante el consumidor.** El backend del producto guarda cada síntesis con likes y comentarios encima; si la v2 renombra y reparte distinto, esos likes quedan colgando de un ítem que ya no existe, y el backend no tiene cómo mapear v1 a v2.

De acá sale además la clave que faltaba: **`Sintesis.id` es el identificador estable del webhook**. El contrato con el backend pasa a ser "mismo id, actualizá; id nuevo, insertá", que cierra el cabo suelto de la idempotencia (antes quedaba "a resolver por el backend", sin darles con qué resolverlo). Por lo mismo, re-sintetizar es un `UPDATE` sobre la fila existente y no un borrar-e-insertar, que además perdería `enviado_backend` y volvería a empujar lo ya entregado.

El costo es que la primera descomposición se decide con material de 2 medios y quedamos atados a ella. Es un precio bajo: en el 75% de los casos no hay segunda pasada, y un ítem que se renombra solo es peor de cara al usuario que un recorte algo imperfecto.

### El intervalo de ingesta se queda en 15 minutos
Se evaluó agrandarlo a 30-45 min, porque la mayoría de las ingestas trae duplicados y porque dos medios que caen en la misma pasada se resuelven con una síntesis en vez de síntesis + re-síntesis. El efecto existe, pero se satura enseguida:

| Intervalo | Re-síntesis que se ahorran | Latencia extra por publicación |
|---|---|---|
| 15 min (actual) | 4 de 16 | — |
| 30 min | 6 de 16 | +8 min |
| 45 / 60 min | 6 de 16 | +15 / +22 min |
| 90 min | 8 de 16 | +38 min |

**El hueco mediano entre dos medios que cubren el mismo hecho es de 90 minutos**: los medios no se copian en minutos sino en horas, así que ningún intervalo razonable colapsa la mayoría de las re-síntesis. Pasar de 15 a 30 min ahorra 2 llamadas (fracciones de centavo) a cambio de 8 minutos de demora en **cada** publicación — lo mismo que acabábamos de rechazar al descartar esperar el cierre.

**Un ratio alto de duplicados no es desperdicio**: deduplicar es un lookup indexado por `guid`, y lo único que escala con la frecuencia es el reagrupamiento, medido en 3,6 s por corrida (unos 6 minutos de CPU por día). Lo que compra pollear seguido es latencia de detección, que sí vale.

Descartado también el riesgo de perder noticias por rotación del feed: La Nación publica 6,5 notas/h y su ventana de ~100 items tarda 15 h en renovarse.

### Qué dispara la síntesis
Dos condiciones, y hacen falta las dos:

1. **Llegaron noticias desde el último intento**, vía `Cluster.noticias_al_sintetizar` (migraciones `98c48e2dc7b1` y `faa5d6fc466e`). Es la guarda contra el reintento infinito: **no alcanza con mirar si el cluster ya tiene síntesis**, porque si ningún ángulo llegó al mínimo de medios no se crea ninguna fila y el cluster sería indistinguible de uno nunca intentado.
2. **Las noticias todavía sin ángulo cubren `MIN_MEDIOS_CLUSTER` medios.** Este es el disparador real, y sale del join de `SintesisNoticia`.

La marca **cuenta noticias, no medios**, y eso corrige el diseño anterior. Contar medios evitaba 97 re-síntesis y las dejaba en 16, pero se comía este caso: si TN y La Nación ya están en el cluster y los dos publican después sobre los homenajes de la AFA, eso es un ángulo nuevo con dos medios y perfectamente publicable, pero el conteo de medios sigue en 2 y no dispara nada. Contando noticias se detecta, y la condición 2 evita igualmente disparar cuando el material nuevo viene de un solo medio (con una sola voz no hay ángulo publicable).

El agujero apareció al responder "¿cuándo se fusionaría un cluster?", no escribiendo el servicio — enumerar los escenarios antes de codificar es lo que lo destapó.

### La fragmentación se corrige en la asignación, no en la fusión
Al enumerar las fallas de Fase 4 apareció que `fusionar_clusters_duplicados()` **crashea** si el cluster absorbido ya tiene síntesis: SQLAlchemy intenta dejar `sintesis.cluster_id` en NULL sobre una columna que no lo admite. Verificado. Y como se publica a la ~1,3 h mientras la fusión corre cada 15 min, iba a dispararse seguido — justo en los eventos de cobertura alta, que son los que fragmentan.

Buscando la causa se encontró que el problema no estaba en la fusión sino un paso antes. `_mejor_match()` devolvía el mejor candidato **global** entre centroides y noticias sueltas, así que una suelta casi idéntica le ganaba a un cluster que ya era match válido:

```
Cluster A existe (la muerte). Llega una nota de Ciudad:
   centroide de A          -> 0.87   (por encima del umbral: pertenecía a A)
   nota suelta de Paparazzi -> 0.96
Ganaba la suelta => nacía un cluster paralelo describiendo el mismo hecho.
```

**Ahora un cluster que supera el umbral le gana a cualquier suelta.** Es la lectura literal de lo que el umbral significa. Se había evaluado en Fase 3 y descartado porque en ese momento la granularidad elegida era "solo duplicados"; esa decisión quedó superada cuando el cluster pasó a buscar cobertura y la separación por ángulo pasó a Fase 4, así que el motivo del descarte ya no existía.

**Medido sobre las 368 noticias del día de mayor cobertura:**

| | Mejor candidato global | Gana el cluster |
|---|---|---|
| Clusters | 50 | **26** |
| Publicables | 37 | 16 |
| Cluster mayor | 9 noticias | 82 |
| Noticias agrupadas | 144 | 141 |
| **Pares a fusionar después** | **19** | **0** |

**Sin encadenamiento: las 82 noticias del cluster mayor eran todas del mismo hecho, cero intrusos.** El centroide de un grupo grande y coherente es un atractor estable y ningún tema ajeno le llega a 0.75 — la misma propiedad que en Fase 3 evitó el encadenamiento, ahora jugando a favor.

Los publicables bajan de 37 a 16 pero no se pierde cobertura (144 contra 141 noticias agrupadas): de esos 37, una decena eran fragmentos de la misma muerte. El peso se desplaza a Fase 4, que ahora tiene que separar de verdad ese cluster en ángulos — antes la fragmentación funcionaba como red involuntaria.

### La fusión queda como red de seguridad, y como canario
No se eliminó, aunque sus disparos medidos sean cero. Queda un hueco angosto: cuando una noticia se empareja con una suelta para crear un cluster, **la suelta entra sin que se revise si ella misma pertenecía a un cluster existente** (solo pasa si viene después en el orden de evaluación; si viniera antes, ya se habría ido sola). Requiere una combinación puntual de orden y geometría y no se observó ninguna vez, pero si muerde produce dos publicaciones duplicadas empujadas al backend, que es difícil de retractar. Una función que no se dispara no cuesta nada; un duplicado publicado sí.

Además sirve de **canario**: si los logs muestran fusiones frecuentes, lo que están diciendo es que la regla de asignación se rompió.

**Las síntesis se mudan al superviviente, no se borran.** Su id es la clave de idempotencia del webhook: borrarlas dejaría al backend con ítems huérfanos con sus likes encima. El superviviente hereda además la marca `noticias_al_sintetizar` más alta del par — como el cluster fusionado tiene más noticias que sus partes, la marca queda por debajo del total y eso dispara la re-síntesis sobre el material ya unificado.

### Modelo elegido: `gemini-3.5-flash-lite`, con el razonamiento apagado
Optimizado para alto volumen y bajo costo, con **salida estructurada soportada**, que es lo que este servicio necesita. Los límites de tokens (1M de entrada) sobran holgadamente: nuestros prompts miden ~8.000.

**El razonamiento (*thinking*) se acota con `thinking_level`, no con `thinking_budget`.** Los tokens de razonamiento **se facturan como salida**, y la salida es ~80% del costo de esta fase, así que dejarlo en automático podía multiplicar la cuenta sin que se note. La tarea además es de extracción con esquema fijo —leer, verificar pistas y completar campos—, no de razonamiento abierto.

El primer intento fue `thinking_budget = 0` (documentado como "0 = DISABLED" en el SDK) y **el modelo lo rechaza con un 400 `INVALID_ARGUMENT`**, sin decir qué argumento. Se aisló probando la config de a una pieza contra la API real:

| Config | Resultado |
|---|---|
| llamada pelada / solo `temperature` | OK |
| **`thinking_budget=0`** | **400 INVALID_ARGUMENT** |
| `thinking_budget=-1` | OK |
| `thinking_level="LOW"` | OK |
| `response_schema` con `Optional[int]` | OK |

Y midiendo el gasto por nivel: **MINIMAL y LOW consumen 0 tokens de razonamiento**, MEDIUM 349 y HIGH 448. Queda en `LOW`: no cuesta nada en las tareas simples y deja margen para escalar cuando el caso lo pide.

Lección de método: el 400 era genérico y el SDK documenta el `0` como válido. Aislar la config pieza por pieza contra la API costó tres llamadas de fracciones de centavo y evitó adivinar.

**Dos palancas de costo que quedan disponibles y hoy no hacen falta**: la Batch API (para trabajo no urgente, y esto no lo es) y el caché de contexto. El caché no rinde acá porque la parte repetida del prompt es la instrucción, ~1.000 de 8.000 tokens, y la entrada ya es la parte barata.

### `synthesis.py`: qué hace y qué decide
- **Salida estructurada.** El esquema de la respuesta se le pasa a Gemini como `response_schema`, así que el JSON es válido por construcción y casi toda la familia de fallos de formato desaparece de raíz, en vez de pedirlo en prosa y parsear a la esperanza.
- **El filtro de cobertura va sobre el ángulo.** Un ángulo nuevo se publica solo si sus noticias cubren `MIN_MEDIOS_CLUSTER` medios distintos. A los ángulos que ya existen no se les aplica ni se les quitan noticias: ya se publicaron, y del otro lado tienen lectores encima. Si el modelo devuelve un ángulo existente con menos notas, se **suman** las nuevas en vez de reemplazar.
- **Un `id_existente` que no corresponde al cluster es alucinación** y se trata como ángulo nuevo.
- **Los índices de notas inventados se descartan**; si un ángulo queda sin notas válidas, no se publica.
- **La marca se escribe aunque no se publique nada**, que es lo que corta el bucle. Y solo se escribe si la síntesis llegó a persistirse: un cluster que falló se reintenta solo en la corrida siguiente.
- **El bloqueo por filtros de contenido se cuenta aparte** (`SintesisBloqueada`) y no se reintenta: la misma entrada da el mismo bloqueo. Si empieza a pasar seguido, lo que dice es que el producto no puede cubrir policiales — una decisión de producto, no un bug. Los datos reales ya tienen material que puede activarlo.
- **Un cluster que falla no arrastra a los demás.**

**Validado contra Gemini real** (cluster de la muerte de Jorge Messi, 8 notas de 3 medios, de las que se enviaron 6 por el tope por medio):

- **6.747 tokens de entrada, 879 de salida, 0 de razonamiento.** Confirma la estimación previa y que `thinking_level=LOW` no agrega costo.
- Separó la cobertura en **dos ángulos correctos**: el fallecimiento y la llegada de Lionel (3 medios) y los homenajes del mundo del fútbol (2 medios). Ambos superaron el mínimo de cobertura.
- La comparativa salió fundamentada y con citas textuales: *"TN destacó la ubicación del cementerio El Prado / omitió la trayectoria laboral previa de Jorge Messi"*, *"Paparazzi destacó el operativo de seguridad y el arribo desde Miami"*.

**Un problema que solo apareció con el modelo real: los nombres de medio no vuelven como están en la base.** Devolvió `"La Nacion"` sin tilde, y la comparativa quedaba con una clave que no matchea `"La Nación"`. Se agregó `_comparativa_validada()`, que compara sin acentos ni mayúsculas contra los medios que **de verdad participan del cluster** y guarda el nombre canónico. De paso implementa el descarte de medios ajenos, que estaba documentado como decisión pero no en el código.

### El pipeline avisa cuando falla, y solo la fusión corta la cadena
`services/alerts.py` centraliza los avisos por mail; la alerta de ingesta pasó a usarlo. Se extrajo recién ahora, cuando aparecieron dos usuarios reales.

En el job del scheduler, cada paso corre aislado: **uno que falla no frena a los siguientes**, porque todos son idempotentes y la corrida siguiente retoma sola. Tres cosas que importan:

- **El `rollback()` no es opcional.** Después de una excepción de base la sesión queda inutilizable, y sin él los pasos siguientes fallarían en cascada por un motivo distinto al original — lo peor posible para diagnosticar.
- **La fusión sí corta la cadena.** Es el único paso que, si falla, omite la síntesis: sintetizar sin haber consolidado publicaría dos veces el mismo hecho, y una publicación entregada al backend no se retracta.
- **Los avisos tienen cooldown** (`ALERT_COOLDOWN_MINUTOS`, 60 por defecto). Un fallo permanente serían 96 mails por día al intervalo de 15 minutos, y a partir del tercero nadie los lee.

La recuperación ya existía por idempotencia; lo que faltaba era enterarse.

### Lo que no tiene hecho no es problema de este motor
Con el flujo real andando apareció una publicación de **horóscopo** (La Nación + Revista Gente, el mismo día). Eso desmiente el argumento con el que se había descartado la exclusión por género: *"el periodismo de servicio no se replica entre medios, se autofiltra"*. Falso — el horóscopo lo publican todos los medios todos los días, así que pasa el mínimo de cobertura sin esfuerzo. El razonamiento venía de una corrida donde las recetas y los cronogramas habían caído en clusters de un solo medio por casualidad.

Lo que **sí** se confirmó es que el segmento de URL no sirve para esto: una de las cuatro notas del cluster estaba bajo `/tecnologia/` ("los tres signos con menos suerte"). Por eso `services/categorias.py` busca en la **URL completa**, no en la sección.

**El límite de responsabilidad, decidido explícitamente:** este motor compara enfoques editoriales de un mismo hecho y entrega síntesis. Si no hay hecho, no hay nada que comparar y no es su trabajo. Se evaluó producir acá un digest por categoría y día ("el horóscopo de hoy") y **se descartó**: mezcla dos productos distintos en el mismo motor, necesita un segundo prompt sin comparativa, y arrastra una migración. Qué se hace con esas notas —tag suscribible, enlace, o nada— lo resuelve el back-end.

El motor entonces **clasifica y deja afuera del agrupamiento, nada más**. Las notas quedan guardadas, con su categoría derivable de la URL y disponibles en `GET /search`. No se filtran en la ingesta a propósito: así la decisión es reversible cambiando un patrón y reagrupando, sin haber perdido datos.

**Sobre los patrones:** son angostos. Se probó `signos` a secas, que sobre 1.200 noticias reales no dio un solo falso positivo, y se sacó igual — "signos de recuperación" es español corriente y el riesgo a futuro no compensaba. Las notas sueltas que se escapen no llegan a formar cluster de todos modos: les falta el segundo medio.

### Umbral de fusión bajado a 0.85
Con datos reales, dos clusters de la muerte de Jorge Messi quedaron a **0.8806** —debajo del 0.90— y publicaron ángulos solapados (*"Fallecimiento y llegada de Lionel"* contra *"Fallecimiento y antecedentes de salud"*).

El mecanismo: el primer cluster acumuló 12 notas de repercusiones (mensajes de Palermo, Paredes, Sofi Martínez), su **centroide se corrió hacia "reacciones"**, y cuando llegaron las fotos del velatorio ya no alcanzaban 0.75 contra ese centroide desplazado. Nacieron como cluster aparte y la fusión a 0.90 no los tocó. A 0.85 se unen, y ya estaba medido que a ese nivel la fusión consolida sin mezclar hechos ajenos.

### Resultado de la corrida comparativa

| | 0.90, sin categorías | 0.85, con categorías |
|---|---|---|
| Clusters | 21 | 19 |
| **Publicaciones** | **12** | **7** |
| Horóscopo publicado | sí | no (8 notas enrutadas) |
| Muerte de Jorge Messi | 2 clusters, ángulos solapados | 1 cluster, 1 publicación |
| Comparativas incompletas | sí | no |

También se ajustó el prompt para que la comparativa **cubra todos los medios que aportaron notas al ángulo**: antes se salteaba alguno (un ángulo con 2 medios describía uno solo).

### El tope por medio recortaba producto, no solo costo
`SINTESIS_NOTAS_POR_MEDIO = 2` se había puesto para acotar el gasto de los clusters de cobertura alta. En la corrida comparativa apareció que un cluster de **14 notas producía una sola publicación**, cuando una versión con menos material agrupado había sacado dos. Se probó el mismo cluster con distintos topes:

| Notas enviadas | Tokens | Ángulos publicables |
|---:|---:|---:|
| 6 (tope 2) | 7.785 | **1** |
| 9 (tope 3) | 10.737 | **1** |
| 14 (todas) | 15.484 | **3** |

Subir a 3 no movió nada: con 9 de 14 notas el modelo sigue viendo una sola historia. Y el ahorro que justificaba el recorte era **13 centavos al mes** — el tope costaba dos publicaciones para ahorrar eso.

**Rediseñado como piso + techo.** `SINTESIS_NOTAS_POR_MEDIO` pasa a ser un piso garantizado por medio (que ninguno quede afuera sigue siendo intocable) y `SINTESIS_MAX_NOTAS = 30` es el techo global. El cupo sobrante se reparte **por rondas entre medios** y no por cercanía global al centroide: con 46 notas y 5 medios, quedarse con las mejores en bruto sesgaba el material hacia el medio más prolífico.

30 cubre entero el caso de 14 notas y casi entero el peor real medido (46), y deja el gasto acotado por arriba en vez de crecer con la cobertura.

**Resultado sobre los mismos 6 clusters: 7 publicaciones → 9.**

Aclaración sobre el límite del modelo: el `1.048.576` de la ficha es el **tamaño de un request**, no una cuota diaria ni semanal. Nuestros prompts usan entre 1% y 3% de eso. Las cuotas reales son RPM, TPM y RPD, dependen del tier y hay que mirarlas en la consola. El punto donde nos pueden apretar no es el volumen sino la **ráfaga**: se sintetizan todos los clusters pendientes seguidos, medido en ~18 requests por minuto, y ahí sí puede aparecer un 429 — que `tenacity` absorbe con espera creciente.

### Entrega de síntesis al backend web/mobile
El motor no expone la síntesis vía polling: la empuja por webhook al back-end del producto (web/mobile), que la persiste en su propia BD junto a atributos propios (likes, comentarios, etc.).

- **Sin entidad nueva**: no hace falta una clase `NoticiaProcesada` separada — el estado de entrega se guarda como campos directos en `Sintesis` (`enviado_backend: bool`, `fecha_envio: Optional[datetime]`, `intentos_envio: int`). Se descartó una tabla de log de envíos aparte por sobre-ingeniería: hoy hay un solo backend destino.
- **Reintentos**: `tenacity` en el momento de enviar (mismo patrón que la ingesta). Si se agotan, la `Sintesis` queda con `enviado_backend=False` y un **job periódico sobre el `APScheduler` ya existente** (no una cola de mensajes) barre las síntesis no entregadas y reintenta. Sin reenvío manual — se descartó por depender de que un operario vea una alerta y actúe.
- **Autenticación del webhook**: firma HMAC-SHA256 sobre el cuerpo del request + timestamp en el header (para poder rechazar requests viejos y mitigar replay), con secreto compartido vía variable de entorno en ambos lados. Se prefirió por sobre un token estático porque el secreto nunca viaja en la red (se manda una firma derivada, no el secreto en sí) — defensa en profundidad más allá de lo que ya da TLS.
- **Idempotencia del lado del backend receptor**: queda a resolver por el equipo de backend/mobile, no es una decisión de este repo.

### La entrega es un barrido, no un envío de lo recién generado
Al implementarlo cayó una simplificación que no estaba en el diseño original. El paso de entrega no manda "lo que se acaba de sintetizar": **selecciona todo lo que tenga `enviado_backend=False`**, sin importar de cuándo sea.

Con eso, el primer intento y el reintento de lo que falló hace horas son exactamente el mismo código, y **el job periódico de reintento que estaba planificado deja de hacer falta**. Es el mismo argumento que ya sostiene todo el pipeline: si cada paso es idempotente, alcanza con volver a correrlo.

Por lo mismo, la entrega es el único paso que corre **aunque falle la fusión**. La síntesis sí se saltea —publicar sin consolidar duplicaría un hecho—, pero lo que quedó sin entregar de corridas anteriores no tiene por qué esperar a que se arregle otra cosa.

### El intento se cuenta aunque el envío falle
Detalle chico con consecuencia grande: `intentos_envio` se incrementa en un `finally`, no después del éxito. Si solo avanzara al entregar bien, un back-end permanentemente caído se quedaría en cero para siempre, nunca alcanzaría `WEBHOOK_MAX_INTENTOS` y el barrido lo reintentaría cada 15 minutos indefinidamente sin que nadie se entere.

Sobre el corte: un **4xx no se reintenta** (salvo 408, 425 y 429). Un 4xx significa que el contrato se rompió —un campo que cambió de forma, una firma que no valida— y eso se arregla con una corrección, no insistiendo. Los 5xx y los timeouts sí se reintentan.

Para que el corte no sea una trampa sin salida hay dos escapes: una **re-síntesis resetea el contador** (el cuerpo cambió, merece otra oportunidad) y `POST /deliver?forzar=true` reincluye las agotadas cuando el problema del otro lado ya está resuelto.

### Lo que mostró la primera corrida completa con la fase cerrada
Flujo entero sobre 1.354 noticias: 39 s punta a punta, 7 llamadas al modelo (20.639 tokens de entrada, 3.281 de salida, **0 de razonamiento**), **US$ 0,0034 la corrida**, y las 17 publicaciones entregadas y verificadas por un receptor independiente. El costo dejó de ser una preocupación: son ~2 centavos de dólar cada 100 publicaciones.

El embudo real, en cambio, es angosto y conviene tenerlo a la vista:

| | notas | |
|---|---:|---|
| ingeridas | 1.354 | 100% |
| dentro de un cluster | 210 | 15,5% |
| respaldando una publicación | 50 | 3,7% |

**39 de 66 clusters son de exactamente 2 notas** y 13 de las 17 publicaciones tienen exactamente 2 medios: el producto vive pegado al mínimo. No es una falla del clustering —1.116 notas simplemente no tienen par en ningún otro medio— sino lo que hay cuando 6 medios cubren agendas distintas. La palanca es **sumar medios**, no bajar el umbral (ya medido: degrada). Cada medio nuevo multiplica los pares posibles en vez de sumarlos.

Dato asociado: **El Cronista agrupa solo el 5,7%** de sus 176 notas y participó de 3 publicaciones. Es económico-financiero puro y cuando La Nación o TN tocan economía lo hacen desde otro lado. Hoy, en los hechos, el producto es **La Nación contra TN**, con las revistas apareciendo en espectáculos.

### La ventana de síntesis perdía material en silencio
De esa misma corrida salió que **30 clusters publicables, con 85 noticias adentro, nunca se intentaron sintetizar**: todos con la marca en `None`.

La causa era el recorte de `clusters_pendientes`, que descartaba lo creado hace más de `HORAS_CLUSTER_ABIERTO * 2` (24 h). Esos 30 eran anteriores a que la Fase 4 existiera, así que en su momento no fue un bug — pero el mecanismo sí es un problema vivo, y contradice la contingencia sobre la que está armado todo el pipeline. Nos dijimos *"cada paso es idempotente, la corrida siguiente retoma sola"*; **para la síntesis eso vencía a las 24 horas**, y nada lo decía. La alerta avisa que un paso falló, no que quedó material inalcanzable.

Dos cambios:

- **El plazo se desacopla y se ensancha**: `HORAS_MAXIMAS_SIN_SINTETIZAR = 72`. El `* 2` sobre la ventana del cluster era un acoplamiento sin razón — son dos preguntas distintas. 72 h le da margen a una caída de fin de semana largo (viernes a la noche a lunes a la mañana son ~60 h).
- **Lo que caduca deja rastro.** `descartar_vencidos_sin_sintetizar` cuenta los que **podrían haber publicado** (los que caducan con un solo medio no perdieron nada), les pone la marca y avisa. La marca hace el aviso terminal: sin eso se repetiría en cada corrida para siempre, y una alerta que se repite sin novedad es una alerta que se deja de leer.

Que una noticia de hace tres días deje de ser candidata sigue estando bien. Lo que estaba mal era el silencio.

*Nota sobre los datos de desarrollo:* con el plazo nuevo esos 30 clusters volvían a estar en alcance, y se habrían publicado noticias del 08/08. Se los marcó a mano como históricos, por única vez, para no ensuciar con material viejo el corpus que venimos usando para evaluar. En producción el mismo caso —una caída real— sí debería terminar en publicación, que es justamente lo que arregla el cambio.

### Publicaciones que decían comparar y mostraban una sola voz
Dos de las 17 (`Evolución de la inflación` y `Aumento de la mora`) tenían notas de La Nación y El Cronista, así que pasaban el filtro, pero el modelo escribió **una sola entrada de comparativa**. Salían al aire como comparativa de enfoques mostrando un solo enfoque, que es exactamente lo que el producto promete no hacer.

El filtro contaba medios **con notas**; la comparativa la escribe el modelo y puede tener menos. Ahora el mínimo se exige **en los dos lados**: noticias y comparativa escrita. El prompt ya lo pedía explícitamente y aun así pasaba — es la clase de cosa que el código tiene que garantizar, no pedir.

Efecto lateral correcto: si al descartar un medio inventado la comparativa queda con una sola voz, el ángulo tampoco se publica. Si una de las dos voces era alucinada, no había dos voces.

Mirando eso apareció algo peor en la ruta de actualización: **la comparativa se pisaba entera**, así que una re-síntesis podía degradar un ángulo ya publicado de dos voces a una. Además de ser peor que no haberlo publicado, incumple lo que `webhook_contract.md` ya le promete al back-end: *la comparativa suma medios, no los quita*. Ahora se fusiona — la entrada nueva de un medio reemplaza a la vieja, pero un medio que ya estaba no desaparece porque el modelo no lo haya vuelto a mencionar.

### Segunda ronda de medios: ninguno pasa, y el motivo cambió
El embudo angosto (3,7% de las notas llega a una publicación) apunta a sumar medios, así que se reevaluaron cinco candidatos. **Ninguno pasa el criterio de Fase 2**, pero los motivos son distintos y conviene distinguirlos:

| Medio | Feed vivo | `content:encoded` | Cuerpo | Agenda |
|---|---|---|---|---|
| Clarín | sí, 26 feeds | **no** | 201 car (copete) | nacional ✓ |
| Perfil | sí, 13 feeds | **no** | 190 car (copete) | nacional ✓ |
| Buenos Aires Times | sí, 100 items | **no** | 154 car (copete) | es Perfil en inglés |
| Cadena 3 | **no — congelado en 2018** | sí | 190 car (copete) | Córdoba |
| Diario Crónica | sí, 500 items/feed | sí, **vacío** | 0-50 car (epígrafe) | Chubut ✗ |
| La Voz | servidores caídos | — | — | Córdoba |

Dos hallazgos que no estaban en la evaluación de Fase 2:

- **Tener el tag no es tener el cuerpo.** Cadena 3 y Diario Crónica declaran `content:encoded` y adentro traen el copete o directamente el epígrafe de la foto ("La hipertensión, una de las enfermedades crónicas", 50 caracteres). El criterio hay que medirlo en caracteres, no en presencia del tag.
- **Un medio solo suma si cubre los mismos hechos.** Diario Crónica publica 500 items por feed, frescos y bien formados, pero su agenda es Comodoro Rivadavia: arenas silíceas de Chubut, Telebingo Chubutense, paritaria petrolera. Casi nada se cruza con lo que cubren La Nación o TN, así que sumarlo agregaría volumen sin agregar un solo par. Lo mismo Cadena 3 y La Voz, las dos de Córdoba.

Por eso los candidatos correctos son Clarín y Perfil: son nacionales y **ya se los vio cubriendo hechos que hoy publicamos** (CAME/Galperin y Milei/Lula aparecieron en la muestra de los dos).

**Se probó la extracción desde la página**, que es para lo que `trafilatura` está reservado en `requirements.txt` desde Fase 2:

| | mediana extraída | fallos | tiempo | `robots.txt` |
|---|---:|---:|---:|---|
| Clarín | 4.207 car | 0/6 | 0,3 s | permite las rutas de artículo |
| Perfil | 4.402 car | 0/6 | 0,3-0,5 s | permite las rutas de artículo |

Eso los deja en el medio del pelotón de lo que ya tenemos (El Cronista 4.574, La Nación 3.805, TN 2.648, Ciudad 2.281). Adoptarlo **cambiaría la regla dura de Fase 2** —"el feed debe traer el artículo completo"— por una segunda vía de ingesta: un request HTTP por artículo en vez de uno por feed, con la fragilidad de depender del maquetado de cada medio. Queda como decisión abierta — retomada y con plan de implementación al cierre de esta fase, ver "Segunda vía de ingesta: extracción por URL" más abajo.

### Revisión del código con las fases cerradas
Con Fase 4 terminada se revisó todo el motor. Lo que apareció no fueron bugs sueltos sino **tres lugares donde el código no hacía lo que su propia documentación decía**, todos introducidos en las últimas correcciones. Vale anotarlo como patrón: el riesgo no estuvo en lo viejo sino en lo recién escrito.

**El descarte por caducidad era irreversible, y la alerta prometía lo contrario.** El mail decía "si esto aparece sin que haya habido una caída, el plazo quedó corto", pero `descartar_vencidos_sin_sintetizar` estampaba `noticias_al_sintetizar` con el conteo real de noticias. Subir el plazo devolvía los clusters a la ventana de fecha y la guarda anti-bucle los salteaba igual: la recomendación era mentira. Ahora se marcan con `MARCA_CADUCADO = -1`, un valor imposible como conteo, que `clusters_pendientes` no toma como intento. Verificado sobre la base real: 14 de 14 clusters caducados pasan la guarda con la marca nueva, y 14 de 14 quedaban bloqueados con la anterior.

**El aviso de ese descarte se silenciaba solo.** `enviar_alerta` tiene un cooldown de 60 minutos por clave y el pipeline corre cada 15: los clusters que caducaran en las corridas 2, 3 y 4 de la hora se descartaban sin un solo mail — exactamente el silencio que la función existía para eliminar, y encima terminal. Se agregó `ignorar_cooldown`, pensado solo para avisos que informan algo irreversible y que el emisor garantiza no repetir. El cooldown protege contra un fallo que se repite; frente a un evento único protege de más.

**La misma forma, al revés, en el webhook.** La alerta de síntesis agotadas consultaba *todas* las trabadas después de cada barrido, así que una sola disparaba un mail por hora para siempre. Ahora avisa solo por las que cruzaron el tope en esa corrida, y el total queda en `agotadas_total` como visibilidad sin ruido.

**La comparativa podía nombrar un medio ausente de las fuentes.** `_comparativa_validada` filtraba contra los medios del **cluster**, no contra los del ángulo. Un ángulo con notas de TN y La Nación podía publicarse describiendo a TN y El Cronista: pasaba el filtro de dos entradas, pero El Cronista no aparecía en sus `fuentes`. Para el front eso es un enfoque sin una sola nota que lo respalde, y contradice la lectura natural de `webhook_contract.md`. Ahora el alcance es el ángulo; en una actualización incluye además los medios que el ángulo ya tenía, porque sus noticias siguen ahí.

**"Un feed que falla no frena a los demás" era cierto solo para errores de red.** Cualquier otra excepción se escapaba de `ingerir_medio` sin commit y abortaba la ingesta de todos los medios que faltaban. Además el commit era por medio, así que un fallo tardío se llevaba puesto lo que ya habían traído los feeds anteriores. Ahora el commit es por feed y no sale ninguna excepción de `ingerir_feed`. El propio test destapó que la primera versión del arreglo solo protegía el procesamiento y no la descarga.

De paso: `stats["error"]` guardaba solo el último feed fallado —con 9 feeds, que cayera uno se leía igual que caerse entero— y pasó a ser `errores`, una lista.

### Los feeds por sección son archivo, no cobertura
Buscando ensanchar el embudo apareció que usábamos **un solo feed por medio**, el general, cuando los diarios grandes publican también uno por sección. La primera medición parecía contundente:

| Medio | feed general | unión de secciones | exclusivos |
|---|---:|---:|---:|
| La Nación | 89 items | 342 | **253** |
| TN | 100 items | +85 en 3 secciones | 85 |
| El Cronista | 33 items | 34 | 1 |
| Ciudad Magazine | 25 items | 25 | 0 |

Con eso parecía que veíamos el 26% de La Nación. **La conclusión era equivocada** y se probó en producción: sumar 8 feeds de sección a cada uno de los dos grandes trajo 151 noticias, y de esas

- la **antigüedad mediana fue de 25,5 h** (la más vieja, de casi 4 años),
- solo **14 entraban en la ventana de agrupamiento**,
- y formaron **cero pares**: la mejor similitud entre ellas fue 0,556, muy por debajo del umbral.

El error fue comparar **fotos únicas** en vez de pensar en muestreo continuo. El feed general es una ventana móvil de actualidad —7 h en La Nación, 23 h en TN— y las secciones guardan meses de archivo. Los 253 "exclusivos" eran viejos, no cobertura desplazada.

La prueba definitiva: **dentro de la ventana temporal que cubre el general, las secciones de La Nación aportan 0 items que el general no tenga.** TN muestra 41, pero todos de más de 10 h — con el polling cada 15 minutos ya los habíamos capturado cuando eran nuevos. Con 89 items cubriendo 7 h y un ciclo de 15 minutos, el margen es de 28×.

**Se revirtió a un feed por medio.** Lo que sí quedó, porque son mejoras independientes:

- **El modelo soporta varios feeds** (`Medio.feeds_rss`, lista JSONB, migración `a72ec65ef1f1`). No cuesta nada con listas de un elemento y sirve el día que entre un medio con el feed general flaco.
- **La deduplicación mira `guid` y `url`, no solo `guid`.** Con varios feeds eso dejó de ser redundante: el mismo artículo aparece en el general y en su sección, y nada garantiza que le pongan el mismo guid — la segunda copia llegaría al `INSERT` y reventaría contra el índice único de `url`, tirando la ingesta entera del medio. En la corrida real se atraparon 317 duplicados en La Nación y 304 en TN, sin una sola colisión.
- **Un feed que falla no frena a los demás del mismo medio.**

Dos cosas para el registro:

- **`tn.com.ar/feed/<seccion>/` responde 200 pero ignora la sección** y devuelve el feed general. La que filtra de verdad es la de Arc. Es una trampa fácil de no ver, porque no falla: miente.
- **`fecha_publicacion` puede venir muy mal.** Una nota sobre el San Lorenzo-Huracán de ayer llegó con fecha de hace 1.408 días: es una página *evergreen* que el medio actualiza sin tocar el `pubDate`. Esa nota queda fuera de la ventana de agrupamiento aunque sea cobertura actual. Es anterior a este cambio y no se tocó — queda anotado.

### El bug que el mock no podía ver: `utcnow().timestamp()`
Se probó la entrega contra un receptor local que valida la firma **copiando literalmente el pseudocódigo del contrato**. Rechazó las 11 síntesis con `401 timestamp vencido`.

`datetime.utcnow()` devuelve un datetime *naive*, y `.timestamp()` sobre un naive lo interpreta como **hora local**: desde Argentina el epoch salía corrido 3 horas. Contra un receptor que valida la ventana anti-replay —que es lo que nuestro propio contrato le pide al back-end— eso rechaza absolutamente todo. Se cambió por `time.time()`.

Vale anotar cómo apareció: con `httpx.post` mockeado el test pasaba, porque el mock no valida nada. El bug necesitaba **un segundo actor que verificara de verdad**. Tras el arreglo, el mismo receptor aceptó las 11 con firma válida, la segunda corrida no reenvió nada y con el secreto cambiado rechazó — o sea que la firma protege de verdad y no es decorativa.

### El nombre del hecho no se manda
Al mirar el primer payload real apareció que `hecho.titulo` traía *"Quiénes son los jugadores de la Selección Argentina que acompañan a Messi en el velatorio…"*. Ese campo es `Cluster.titulo_evento`, que no es más que el titular de la primera nota que formó el cluster: **el encuadre de un medio puntual**.

Mandarlo sería entregarle al front, como nombre neutro del hecho, exactamente lo que el producto se propone no hacer. Se sacó. Para agrupar los ángulos de una historia alcanza con `hecho.id`; si más adelante hace falta una etiqueta visible, hay que generarla neutra y no reciclar un titular.

Por la misma lógica de identificadores, la **comparativa viaja como lista con el `id` del medio** y no como el diccionario indexado por nombre que se guarda en la base. Un nombre para mostrar cambia (un rebranding, una tilde corregida) y del otro lado eso deja filas huérfanas.

### El tópico: qué sección es cada publicación
Faltaba lo más básico para que el back-end pueda filtrar: **de qué tema es cada publicación**. El motor no lo asignaba en ningún lado.

La opción barata era derivarlo de la URL, porque los medios ya categorizan (`tn.com.ar/deportes/…`, `lanacion.com.ar/economia/…`). Se midió sobre las 1.296 noticias:

- Cada medio nombra lo mismo distinto: `el-mundo` (La Nación) es `internacional` (TN), `economia-politica` (El Cronista) es `economia`, y `show` / `teve` / `entretenimiento` / `romances` son todos espectáculos. Con una tabla de ~50 entradas se normaliza el **93,6%** de las URLs.
- Normalizar sube el acuerdo entre medios de **3/11 a 8/11** publicaciones.

**Pero los 3 desacuerdos que quedan no son ruido, son el producto:**

| Publicación | Medio A | Medio B |
|---|---|---|
| Muerte de Jorge Messi | TN → `deportes` | Paparazzi → `teve` |
| Galperin contra CAME | La Nación → `politica` | El Cronista → `negocios` |

Los dos tienen razón. Que un medio lo trate como deporte y otro como espectáculo **es encuadre editorial** — justo lo que el motor existe para mostrar. Una votación por mayoría promediaría precisamente la señal del producto.

Así que se resolvió con el reparto que ya usábamos con TF-IDF y NER, **el cálculo señala y el modelo juzga**: la sección normalizada de cada medio entra al prompt como pista y el modelo elige de una lista cerrada leyendo los textos. Cuesta ~4 tokens de salida por ángulo, no necesita mantenimiento al sumar un medio, y decide **por ángulo**, que es la unidad que se publica.

**Taxonomía cerrada de 10**: politica, economia, sociedad, policiales, internacional, deportes, espectaculos, tecnologia, ciencia, lifestyle. Cerrada porque con texto libre convivirían "Deportes", "deportes" y "Fútbol", y la navegación del producto se rompe sola. No hay `opinion` ni `columnistas`: eso es **género**, no tema (una columna sobre inflación es economía), la misma distinción que ya habíamos hecho con el horóscopo.

**Principal + secundario opcional.** El caso que lo justifica es el velorio de Jorge Messi: pertenece con igual derecho a deportes y a espectáculos, y con un solo tópico desaparecería de una de las dos secciones.

Detalle de implementación: el secundario es un enum aparte que incluye el valor `ninguno`, en vez de un campo nulable. Los esquemas de respuesta del modelo manejan mucho mejor un enum obligatorio que uno nulable, y con `ninguno` explícito no hay forma de que devuelva algo fuera de la lista. Se traduce a `NULL` al guardar: es un detalle del protocolo y no tiene por qué llegar a la base.

**Validado contra Gemini real sobre las 11 publicaciones.** En 7 el tópico coincide con lo que declararon los medios; en 4 se aparta, y todas se sostienen:

- Los dos ángulos del cluster de Messi: `deportes + espectaculos` para el velorio, y **solo `deportes`** para el de las reacciones futbolísticas. Dos ángulos del mismo cluster con tratamiento distinto, que es lo que una votación por cluster no puede producir.
- Galperin: los medios se partían entre política y negocios, el modelo resolvió `economia`.
- El vuelco de la lancha frente a la Estatua de la Libertad: los dos medios dijeron `internacional` y el modelo puso `policiales + internacional`. **Es el único caso donde sobreescribió una señal unánime.** Se sostiene (un accidente con víctimas es policiales, y dejó internacional como secundario), pero es el patrón a vigilar.

**El tópico se congela igual que el título.** Mover una publicación de Deportes a Espectáculos entre una entrega y la siguiente es el mismo problema que renombrarla: del otro lado ya está en una sección, con lectores encima. La única excepción son las síntesis anteriores a que el campo existiera, donde no hay nada que preservar.

### Segunda vía de ingesta: extracción por URL — **diferida a después de la 1.0**

Retomando la decisión abierta más arriba. Reconfirmado con una segunda corrida, dos días después de la primera:

| | 1ª corrida (6 art.) | 2ª corrida (6 art.) | fallos |
|---|---:|---:|---:|
| Clarín | 4.207 car | 4.419 car | 0/6, 0/6 |
| Perfil | 4.402 car | 2.741 car | 0/6, 0/6 |

**Clarín se sostiene entre corridas; Perfil se movió 38%** — con la segunda muestra entró una entrevista de 17.398 caracteres que arrastra la mediana. Con *n*=6 por corrida el número no es estable; lo que sí se repitió sin excepción en las dos rondas: 0 fallos sobre 12 artículos, ninguno por debajo de 400 caracteres, 0,3-0,5 s por artículo, `robots.txt` de ambos permite las rutas de artículo sin `crawl-delay` declarado. La lectura cualitativa ("el método funciona y devuelve un artículo, no basura") está firme; la lectura cuantitativa exacta no — antes de decidir con el número hace falta correrlo sobre 40-50 artículos por medio, no 6.

**Medido el volumen que agregaría por ciclo:**

| | items del feed | ventana temporal | ritmo | por ciclo de 15 min |
|---|---:|---:|---:|---:|
| Clarín (`/rss/lo-ultimo/`) | 10 | 0,7 h | 14,0 notas/h | ~3,5 |
| Perfil (`/feed/politica`) | 70 | 36,7 h | 1,9 notas/h | ~0,5 |

Del orden de 4 requests extra por ciclo — insignificante como carga. **Pero el feed de Clarín cubre apenas 42 minutos**, contra las 7 horas de La Nación. El margen de tolerancia a una caída del pipeline pasa de 28× a 2,8×: tres ciclos salteados y lo publicado en ese hueco se pierde sin dejar rastro, porque el feed ya lo rotó. Es justo el caso para el que se dejó la capacidad de `Medio.feeds_rss` al revertir los feeds de sección (ver arriba) — no como archivo redundante, sino como red de contención para un feed general demasiado corto.

**Por qué es viable sin poner en riesgo lo que ya funciona:** la costura ya existe. En `ingestion._procesar_items`, cuando `_parsear_entry` devuelve `None` por falta de `content:encoded`, hoy se descarta la nota (`stats["sin_contenido"] += 1`). Ese es el punto donde entraría la extracción — y el contrato de salida es un string `contenido_limpio` idéntico al que ya produce `limpiar_html`. De ahí en más (vectorización, clustering, síntesis, tópicos, webhook) nada distingue el origen del texto: **no hay nada que adaptar río abajo.**

Lo que sí exige diseño, en orden de lo menos al más obvio:

1. **El orden de filtrado tiene que invertirse.** Extraer antes de deduplicar bajaría la página de artículos que ya tenemos en cada corrida (el feed de Clarín re-sirve los mismos 10 items siempre) — de ~3,5 requests útiles por ciclo a 10, mayoría desperdiciados. `es_en_vivo` y la deduplicación por `guid`/`url` solo necesitan campos que el RSS sí trae, así que van antes; la extracción va al final, solo para lo que sobrevivió a los dos filtros.
2. **El scheduler no tiene margen documentado.** `main.py` registra el job con `add_job(..., "interval", ...)` sin `max_instances`, `coalesce` ni `misfire_grace_time`. El default de APScheduler es `max_instances=1`: si una corrida se pasa de los 15 minutos, la siguiente **se descarta en silencio**, con un warning que nadie mira. Hoy es imposible (6 requests de feed). Con extracción de artículos de por medio, sigue siendo lejano pero deja de ser "no puede pasar" — y si pasa justo con Clarín, se combina con el punto anterior (ventana de 42 min) para perder noticias sin alerta.
3. **La degradación pasa de ruidosa a silenciosa.** Hoy "sin cuerpo" es visible: cuenta en `stats["sin_contenido"]` y dispara un warning si el feed entero vino vacío. Si un medio rediseña su maquetado, `trafilatura` no falla — devuelve algo corto (menú, aviso de cookies) que *parece* contenido y contamina embeddings y prompts en silencio. Hace falta un piso de caracteres con alerta propia; no es un detalle, es parte del cambio.
4. **La política de reintentos no se puede reusar tal cual.** `_descargar_feed` tolera hasta ~20 s de backoff por feed; multiplicado por artículo empujaría directo al punto 2. El extractor necesita la suya, más corta, y que un artículo caído se saltee solo ese artículo sin tirar el feed.
5. **Tiene que activarse por medio, no global**, para no mandarle requests de página a los medios que ya entregan cuerpo completo por RSS (los 6 actuales). Eso es una columna nueva en `Medio` y su migración — chica, pero es esquema.

**No entra en la app hasta validar que suma pares reales.** Diario Crónica fue la advertencia: 500 items por feed, bien formados, cero pares porque su agenda no se cruza con la nuestra. Para Clarín y Perfil hay indicio a favor (se los vio cubriendo CAME/Galperin y Milei/Lula, hechos que ya publicamos) pero es indicio, no medición — falta extraer un día completo de los dos, vectorizar contra el corpus real y contar pares por encima del umbral antes de escribir una sola línea de producción.

**Se decidió explícitamente diferir todo esto a después del cierre de Fase 5.** Con el back-end integrado, probado y una versión 1.0 estable, se retoma en una rama nueva. Motivo: tocar la ingesta ahora compite por atención con la comunicación real con el back-end y sus pruebas, que es lo que cierra el producto mínimo. Esta sección documenta la discusión completa para no tener que rehacerla — decisión de diseño e implementación planeada, ejecución pendiente.

---

## Fase 5 — Deployment y Escalabilidad (alcance mínimo)

### Kubernetes, Prometheus/Grafana, Redis y rate limiting quedan afuera — no es indecisión, es la escala real

El roadmap traía esos cuatro ítems desde que se escribió el proyecto, sin relación con el volumen de uso real: un motor interno, sin tráfico público, corriendo hoy en un solo contenedor sin siquiera un servicio `app` en `docker-compose.yml`. `mission.md` lo dice explícito: *"no resuelvas problemas de escala que todavía no existen"*, y el proyecto tiene además una restricción dura de costos por ser desarrollo propio — Kubernetes y un stack de Prometheus/Grafana autoalojado son gasto de infraestructura real, no solo trabajo de más.

Calibrado con el usuario: **VPS único con Docker Compose**, **stack mínimo viable**. Los cuatro quedan documentados en `roadmap.md` como diferidos a propósito, con el motivo de cada uno, no simplemente borrados — para que quien retome la fase sepa que fue una decisión y no un olvido.

Alcance que sí quedó accionable: CI, completar `docker-compose.yml`, las 3 consultas que no escalaban, y el pool de conexiones. De paso, un healthcheck real — necesario para que el `HEALTHCHECK` del Dockerfile tenga algo que verificar de verdad.

### El CI no lleva Postgres para correr los tests — y eso no es un atajo

`tests/conftest.py` arma un engine SQLite en memoria (`StaticPool`) para toda la suite. Verificado antes de diseñar el workflow: los 215 tests que existían pasaban así, sin `DATABASE_URL`, sin Postgres levantado. Por qué eso es legítimo y no un hueco de cobertura:

- `GET /search` mockea `buscar_noticias_similares` completo — el propio código ya documentaba que es porque usa el operador `<=>` de pgvector, que SQLite no soporta.
- `pgvector.sqlalchemy.Vector` es un `UserDefinedType` genérico: en SQLite serializa a texto sin fallar al crear la columna ni al insertar, y solo fallaría si se usara el operador de distancia — que ningún test dispara.
- `synthesis.clusters_pendientes` y `descartar_vencidos_sin_sintetizar` están probadas directo contra la sesión SQLite, sin mock: son SQL portable.
- `search.listar_clusters` **no tenía tests** (no existía `tests/test_search.py`). Esta fase lo cerró.

Sumar un servicio Postgres al job de tests no habría agregado cobertura real — habría sido ceremonia. Lo que sí es un riesgo genuino, y ya mordió una vez (la migración `979689aeb928` de Fase 4 rompía contra una base con datos por un `NOT NULL` sin `server_default`), es que las migraciones de Alembic apliquen limpias contra Postgres+pgvector real. Por eso el CI tiene **dos jobs con objetivos distintos**:

- `tests`: `pytest --cov=src --cov-fail-under=80`, sin Postgres.
- `migraciones`: un servicio `pgvector/pgvector:pg16` real, y el único paso es `alembic upgrade head`.

Confirmado además que `alembic/env.py` importa solo `src.models` y `src.config` (no `src.services`), así que aplicar migraciones no dispara carga de spaCy/sentence-transformers/Gemini — el job de migraciones corre en segundos, no minutos. Y que `.dockerignore` no excluye `alembic/` ni `alembic.ini`, así que la imagen ya podía migrarse desde adentro sin tocarlo.

Fuera de este workflow a propósito: build de la imagen Docker completa en CI. Sería la validación más fiel al Dockerfile real, pero agrega minutos de build (compilación de `numpy`/`scikit-learn`, descarga de `es_core_news_md`) sin que hubiera evidencia de que el Dockerfile se rompa. Se agrega si eso llega a pasar.

### `docker-compose.yml`: migración en el mismo `command`, no un servicio aparte

`app` corre `alembic upgrade head && uvicorn ...` como un solo comando, en vez de un servicio `migrate` separado con `depends_on: condition: service_completed_successfully`. La alternativa "pura" —un servicio dedicado— es una segunda definición de servicio para lo mismo, y en un VPS único con una sola réplica no compra nada: `alembic upgrade head` ya es idempotente (no-op si el esquema está al día) y falla rápido y ruidoso si una migración está rota — el contenedor no arranca, que es exactamente la señal que hace falta.

`app` depende de `db` con `condition: service_healthy`, no solo `depends_on: [db]`: sin la condición, Compose arranca `app` en cuanto el contenedor de `db` existe, no cuando Postgres ya acepta conexiones — y la primera migración fallaría por una carrera, no por un error real.

**Validado en vivo, con Postgres real** (no solo revisado a mano): `docker compose build app` compiló sin errores; `docker compose up -d` mostró que `app` esperó a que `db` pasara a `Healthy` antes de arrancar; los logs confirmaron `alembic upgrade head` corriendo (no-op, ya migrada) seguido de `Uvicorn running`; `curl http://localhost:8000/` devolvió `200` con `database: ok`; al cortar `db` con `docker compose stop db`, `GET /` pasó a devolver `503` con `status: degradado`, y Docker marcó el contenedor `app` como `unhealthy` en el siguiente ciclo del `HEALTHCHECK` (~30s); al reiniciar `db`, `app` se recuperó solo, sin reiniciarse — el `pool_pre_ping` hizo su trabajo.

### Pool de conexiones: los valores y qué pasa si se agregan réplicas

`DB_POOL_SIZE=5` / `DB_MAX_OVERFLOW=10` / `DB_POOL_TIMEOUT=30` / `DB_POOL_RECYCLE=1800`, nuevos en `config.py`, con el mismo estilo de comentario largo que el resto del archivo.

Calibrados contra el hecho conocido de que hoy hay **un solo proceso Uvicorn** (el Dockerfile no pasa `--workers`): aunque los endpoints son síncronos, FastAPI los corre en el threadpool de Starlette, así que ese único proceso sí atiende varias requests a la vez, cada una con su propia conexión vía `get_session`, más la que sostiene el scheduler durante todo el pipeline. 5 conexiones de base + 10 de overflow (15 en total) da margen sin acercarse al `max_connections` por defecto de Postgres (100), dejando lugar para conectarse a mano (`psql`, un script) sin agotar el pool de la app.

`pool_recycle=1800` no duplica a `pool_pre_ping` (que ya estaba activo): `pool_pre_ping` detecta una conexión muerta recién al intentar usarla; `pool_recycle` la descarta y renueva antes de que eso pase, protegiendo contra que Postgres o un firewall/NAT del VPS la cierren del otro lado por inactividad sin avisar.

Qué pasa si más adelante se suman réplicas (fuera de alcance de esta fase, ver `tech_stack.md` punto 4): cada réplica abre su propio pool, así que N réplicas piden hasta N × 15 conexiones. A partir de ~6 réplicas eso ya se acerca al `max_connections` por defecto de Postgres, y ahí hace falta bajar el pool por réplica, subir `max_connections`, o sumar un pooler (PgBouncer) — ninguna de las tres hace falta con una sola réplica, así que no se resuelve ahora.

### Las 3 consultas que no escalaban — y una cuarta que el diseño original no vio

Detectadas en la revisión de código al cerrar Fase 4, documentadas como pendientes en `roadmap.md`. Las tres se resolvieron con el mismo patrón: reemplazar el acceso lazy a una relación dentro de un loop (o una carga de tabla completa) por `sqlalchemy.orm.selectinload`, que trae la relación de todas las filas padre en una consulta adicional acotada (`WHERE <fk> IN (...)`) en vez de una por fila. Se eligió `selectinload` sobre `joinedload` porque hay fan-out (un cluster tiene varias noticias) y `joinedload` habría duplicado filas del padre por cada hijo.

- **`synthesis.clusters_pendientes`**: cargaba `SintesisNoticia.noticia_id` de **toda la tabla** sin filtrar (crecía con el historial del producto, no con el tamaño de la corrida), y hacía un `SELECT Noticia` por cada cluster candidato. Pasó a `selectinload(Cluster.noticias)` sobre los candidatos, y el filtro de `SintesisNoticia` acotado a los ids de noticias realmente en juego. De `2 + N` queries a 3 constantes.
- **`search.listar_clusters`**: hacía `SELECT Noticia JOIN Medio` por cada cluster listado — hasta 101 queries con `limite=100` (el máximo del endpoint). Pasó a `selectinload(Cluster.noticias).selectinload(Noticia.medio)`, usando las relaciones que ya existían en los modelos en vez de un join manual. De `1 + N` a 3 constantes. De paso quedó con su primer test directo (`tests/test_search.py`, no existía — antes solo se probaba mockeada en `test_api.py`).
- **`synthesis.descartar_vencidos_sin_sintetizar`**: el fix más chico a primera vista — precargar `Cluster.noticias` antes del comprehension que la recorría.

El cuarto caso, no anticipado en el diseño: el test de no-escalamiento de `descartar_vencidos_sin_sintetizar` seguía fallando **después** de aplicar `selectinload`. La causa no estaba en la query de lectura sino en lo que pasa después: la función hace `session.commit()` para persistir `MARCA_CADUCADO`, y SQLAlchemy expira todos los atributos de los objetos al commitear por defecto (`expire_on_commit=True`) — incluidos `id` y las relaciones ya precargadas. El código seguía leyendo `c.id` y `c.noticias` (para el cuerpo del mail de alerta) **después** del commit, y cada lectura post-expiración dispara una recarga individual: el mismo N+1 que `selectinload` acababa de eliminar, reapareciendo un par de líneas más abajo. Se resolvió capturando `ids_perdidos` y `notas_perdidas` **antes** del commit. Vale anotarlo como el mismo patrón que ya apareció en la revisión de Fase 4: el bug estaba en código que ya se había tocado en esta misma pasada, no en lo viejo.

Los tres (cuatro) fixes tienen test de no-escalamiento: se compara el número de queries entre pocas filas y muchas, y se exige que sea igual, en vez de fijar un número mágico (`assert queries <= 3`) — más robusto a que una futura query constante legítima no rompa el test sin que el N+1 haya vuelto de verdad. El helper `contar_queries` (nuevo en `tests/conftest.py`) engancha el evento `before_cursor_execute` de SQLAlchemy para contarlas dentro de un bloque `with`.

### El healthcheck pasa a devolver 503, no un campo informativo

Antes `GET /` devolvía siempre `200 {"status": "ok", ...}` — confirmaba que Uvicorn respondía, nada más. Con `app` corriendo dentro de Docker eso significa que el `HEALTHCHECK` del Dockerfile (`curl -f http://localhost:8000/`) nunca podía detectar una base caída: el proceso seguía vivo y respondiendo 200 aunque cada request real fallara río abajo.

`verificar_conexion` (nuevo en `database.py`) corre `SELECT 1` contra la sesión de la request. Si falla, `GET /` devuelve `503` con `status: degradado` y `database: error` — `curl -f` interpreta cualquier código ≥400 como fallo, así que Docker marca el contenedor `unhealthy` con la misma señal que ya usaba, sin tocar el Dockerfile. Confirmado en la validación en vivo (sección de arriba) que el ciclo completo funciona: base caída → 503 → contenedor `unhealthy` → base recuperada → 200 sin reiniciar `app`.

### Resultado

221/221 tests (215 + 6 nuevos: 3 de `listar_clusters` en `tests/test_search.py`, 2 de no-escalamiento en `synthesis.py`, más el de healthcheck degradado), 95,5% de cobertura. `docker compose up --build` validado de punta a punta contra Postgres real, con caída y recuperación de la base incluida.

---

## Rediseño de tópicos: de principal/secundario a tópicos + subtópicos

### El problema: `topico_secundario` mezclaba dos preguntas distintas

Señalado por el usuario mirando un payload real: *"no me parece correcto que si el tópico principal es deporte, el secundario pueda ser espectáculo"*. Tenía razón, y el motivo no era el ejemplo puntual sino el diseño del campo.

`topico` + `topico_secundario` (Fase 4) representaba con la misma pareja principal/secundario dos cosas que no son lo mismo:

1. **Otra categoría igual de válida** — el velorio de Jorge Messi es deportes Y espectáculos, sin que una sea subordinada de la otra. Las dos secciones tienen la misma razón.
2. **Un recorte más fino DENTRO de una categoría** — una cobertura de deportes que específicamente es sobre fútbol.

Meter las dos bajo un campo "secundario" produce combinaciones que no describen bien ninguna de las dos preguntas: `topico=deportes, topico_secundario=espectaculos` parece decir que espectáculos es menos importante que deportes, cuando en realidad son pares. Y no había ningún lugar para representar "fútbol dentro de deportes" en absoluto.

### El diseño: dos listas independientes, con la jerarquía garantizada por código, no por el modelo

- **`topicos: List[Topico]`**, 1 o 2 categorías, **pares** (no principal + secundaria). El caso Messi pasa a ser `topicos=["deportes", "espectaculos"]`, sin jerarquía falsa.
- **`subtopicos: List[Subtopico]`**, 0 o más recortes finos, cada uno con un padre fijo en `SUBTOPICO_PADRE`.

La pieza que de verdad resuelve la objeción del usuario: **la jerarquía no depende de que el modelo la respete, la garantiza `con_padres_completos` después**. Si el modelo elige `subtopicos=[futbol]` sin haber incluido `deportes` en `topicos`, el código se lo agrega. Nunca puede quedar un subtópico huérfano de su categoría — es una regla mecánica, no un criterio que el modelo pueda aplicar bien o mal.

```python
def con_padres_completos(topicos, subtopicos) -> List[Topico]:
    resultado = list(topicos)
    for subtopico in subtopicos:
        padre = SUBTOPICO_PADRE[subtopico]
        if padre not in resultado:
            resultado.append(padre)
    return resultado
```

Puede devolver más de 2 tópicos en el caso límite de que el modelo ya haya llenado el tope de 2 sin incluir el padre de un subtópico elegido. Se prioriza la consistencia sobre el tope: el tope de 2 es una guía de prompt para no diluir la señal, no una regla dura que valga más que "un subtópico sin categoría".

### Decisiones de diseño confirmadas con el usuario antes de tocar código

Tres eran genuinamente su call, no algo para decidir en silencio (regla de `mission.md`):

1. **Enum de subtópicos plano y único, no uno por categoría.** Gemini estructurado no puede acotar un enum según el valor de otro campo del mismo objeto — un enum por categoría no evitaría la validación en código y solo complicaría el prompt. Confirmado además que la conversión de Pydantic a schema de Gemini (`origin.model_json_schema()`, vía `google.genai._transformers.process_schema`) preserva `min_length`/`max_length` como `minItems`/`maxItems` del lado de la API — probado localmente antes de comprometerse al diseño.
2. **Tope de 2 tópicos por ángulo**, igual al límite implícito del diseño anterior. Sin tope la señal se diluye y el filtro por categoría deja de servir para navegar.
3. **Taxonomía de subtópicos construida en el momento**, no diferida. Se midió contra la base real en vez de inventarse.

### La taxonomía: medida, no de memoria

Primera pasada: contar el primer segmento de URL contra `SECCIONES` (la tabla ya existente para `topico_declarado`). Resultado inesperado: varias entradas de `SECCIONES` que parecían buenas candidatas a subtópico —`futbol`, `famosos`— **tenían cero notas** en el roster de medios activos. Eran de una evaluación de medios anterior (Fase 2/3), no de los 6 medios que ingerimos hoy. Contar de memoria en vez de medir habría producido una taxonomía con entradas muertas.

Segunda pasada, la que importó: mirar el **segundo segmento** de URL bajo las secciones grandes (`deportes`, `economia`, `sociedad`, `espectaculos`, `internacional`), sobre 1.995 noticias reales:

| sección | 2do segmento | notas |
|---|---|---:|
| deportes | futbol | 246 |
| deportes | rugby / hockey / tenis / automovilismo / basquetbol | 12 / 5 / 5 / 4 / 3 |
| espectaculos | personajes | 13 |
| espectaculos | teatro | 6 |
| espectaculos | musica | 5 |
| espectaculos | cine | 3 |
| economia | campo | 31 |
| economia | negocios | 8 |
| sociedad | psicologia / jardineria | 8 / 6 |

Combinado con lo que ya aportaba el primer segmento (`teve` 55, `musica` 18, `romances` 13, `cine-y-series` 12, `negocios` 54, `campo` 11, `estados-unidos` 97, `propiedades` 31, `autos` 23, `cocina` 15), la taxonomía final quedó en **16 subtópicos sobre 5 categorías**:

```
deportes:       futbol, rugby, hockey, tenis, automovilismo, basquetbol
espectaculos:   teve, musica, cine, chimentos
economia:       negocios, campo
internacional:  estados_unidos
lifestyle:      propiedades, autos, cocina
```

Política, policiales, tecnología y ciencia quedan **sin subtópicos a propósito**: no apareció ninguna sección de URL que se distinga con fuerza de la categoría misma. Es preferible que el modelo no elija nada a que elija de una lista sin respaldo — mismo criterio que ya rige `topico_declarado` devolviendo `None`.

Judgment calls editoriales, no medidos, confirmados con el usuario:

- **`chimentos`** agrupa `romances` (13) + `personajes` (13) — contenido de farándula/rumores. El nombre es una elección de estilo, no un dato.
- **Los deportes minoritarios entraron igual** (rugby 12, hockey 5, tenis 5, automovilismo 4, basquetbol 3) pese a volumen bajo, por decisión explícita del usuario — completitud editorial por sobre la evidencia estricta en este caso puntual.
- **`salud` y `educacion` (bajo sociedad), agregadas en una segunda pasada.** Bajo `sociedad` solo habían aparecido `psicologia` (8) y `jardineria` (6) con volumen medido, ninguno con espalda suficiente. Pero revisando el resultado, el usuario señaló que salud y educación son categorías que alguien busca específicamente, y no tenerlas de entrada dejaría esas búsquedas sin filtro fino desde el día uno — mismo criterio que los deportes minoritarios, aplicado retroactivamente. Medido en el corpus de 6 medios activos: `salud` con 18 notas de primer segmento (mismo orden que `musica`, que sí había entrado); `educacion` con **0** — está en `SECCIONES` desde Fase 4 pero ningún medio activo la usa hoy como sección propia. Se sumó igual, siguiendo el mismo criterio ya aceptado: la completitud editorial pesa más que la evidencia estricta cuando el usuario lo pide explícitamente.

`subtopico_declarado(url)` (nuevo en `topicos.py`) es el mismo patrón que `topico_declarado`, pero mira los primeros **dos** segmentos de la ruta en vez de uno: medido, el 87% de las notas de deportes con un segundo segmento útil lo tienen en `/deportes/futbol/...`, no en `/futbol/...`.

### Migración de datos: el secundario viejo se vuelve un tópico par, no un subtópico

`Sintesis.topico` + `Sintesis.topico_secundario` (dos strings nullable) pasan a `Sintesis.topicos` + `Sintesis.subtopicos` (dos listas JSONB) — migración `27e6744ee0b2`, escrita a mano por los mismos motivos que la de `feeds_rss` en Fase 5: hay datos que backfillear, y un autogenerate habría hecho add + drop perdiendo el tópico de cada síntesis ya publicada.

Backfill: `topico` pasa a ser el primer elemento de `topicos`; si había `topico_secundario`, se agrega como **segundo tópico par**, no como subtópico — es la traducción correcta bajo el diseño nuevo, porque bajo el viejo esos valores YA eran categorías de pleno derecho, nunca un recorte fino. `subtopicos` queda en `[]` para todo lo existente: no hay forma de reconstruir un recorte que el diseño anterior no capturaba.

Verificado contra la base real tras aplicar la migración (77 síntesis existentes): 30 con 2 tópicos (las que tenían secundario), 47 con uno solo, las 77 con `subtopicos` vacío. Ninguna perdió su tópico.

### `_persistir`: la congelación se generaliza a listas sin cambiar la semántica

El tópico ya estaba congelado desde su publicación (Fase 4: mover una publicación de Deportes a Espectáculos entre entregas confunde a quien ya la vio). Esa semántica se preserva idéntica con listas: en una actualización, `topicos`/`subtopicos` solo se completan si `sintesis.topicos` está vacío (síntesis previa al campo o al rediseño); si ya tiene valor, no se toca, sin importar qué haya elegido el modelo esta vez.

### Validado contra Gemini real, no solo mockeado

Sobre 4 clusters reales sin síntesis previa (para forzar la rama de creación con el schema nuevo):

| cluster | resultado |
|---|---|
| Rumores Griselda Siciliani / Emiliano Brancciari | `topicos=["espectaculos"]`, `subtopicos=["chimentos"]` |
| Thiago Medina imputado | `topicos=["policiales"]`, `subtopicos=[]` |
| Tren choca cerca de "la Bombonera" | `topicos=["sociedad", "policiales"]`, `subtopicos=[]` |
| Crédito en dólares (Gobierno/bancos) | 4 ángulos generados, 4 descartados por cobertura insuficiente — sin relación con tópicos |

Dos cosas para el registro: el modelo **ya incluyó el padre correcto por su cuenta** en el caso de Griselda (`espectaculos` junto con `chimentos`), así que `con_padres_completos` no tuvo que intervenir en ningún caso real — la garantía mecánica queda como red de seguridad probada por unit tests, no como algo que se dispare seguido. Y el caso del tren cerca de "la Bombonera" es una buena señal de que el modelo lee el texto y no hace pattern-matching de superficie: pese a la mención del estadio, no lo etiquetó como deportes.

Confirmado además, antes de comprometerse al `max_length=2` en el schema: `google.genai` convierte un `List[Enum]` de Pydantic con `Field(min_length=1, max_length=2)` a un `ARRAY` con `minItems`/`maxItems` en el schema real que recibe la API — no es un supuesto, se probó localmente contra la librería instalada.

### Contrato del webhook actualizado

`specs/webhook_contract.md` — `topico`/`topico_secundario` pasan a `topicos`/`subtopicos`, con la taxonomía completa de subtópicos, la garantía de que todo subtópico tiene su padre presente, y un ejemplo real con subtópico poblado. **Sin entrega real todavía** (falta URL y secreto del otro equipo), así que el cambio no rompe nada en producción — pero como es un documento compartido, queda marcado explícitamente como cambio de forma sobre una versión anterior para que el otro equipo lo vea si ya había empezado a integrar contra el contrato viejo.

241/241 tests (221 + 20 netos nuevos: `test_topicos.py` reescrito con 41 tests y cobertura 100% del módulo `topicos.py`; `test_synthesis.py` y `test_webhook_delivery.py` actualizados a las listas nuevas, con casos nuevos para la garantía de `con_padres_completos`). 96% de cobertura total.

---

## Copy para redes sociales: `relevancia_social` + `publicacion_redes`

### El pedido

El usuario propuso aprovechar que Gemini ya lee el cuerpo completo de cada síntesis para que además genere, para las publicaciones que lo ameriten, un párrafo corto para redes (Twitter/Facebook) — distinto del resumen neutro que ya se entrega — y una lista de hashtags. La lógica de negocio real (cuándo publicar, con qué cadencia, si los hashtags se curan) queda para más adelante con el equipo de marketing; acá solo se resuelve qué genera el motor y cómo se lo entrega al back-end.

### Primer diseño descartado: dos llamadas a Gemini

La primera idea fue partir el trabajo en dos: una llamada barata, en la síntesis de siempre, que solo *marca* si el ángulo es de relevancia nacional (`relevancia_social: bool`); y una segunda llamada, on-demand, que recién generaría el párrafo y los hashtags para los ángulos que el back-end decidiera publicar — así se evitaba gastar en redactar copy para ángulos que nunca se publican en redes.

Se descartó al debatirlo: el costo de una llamada a Gemini lo domina el **tokens de entrada** (el cuerpo completo del cluster), no cuánto se le pide de salida. Una segunda llamada reenviaría ese mismo contexto de cero — sale más caro, no más barato. Generar el copy **condicional, en la misma llamada** que ya se hace para toda síntesis es estrictamente mejor: sigue siendo una llamada por cluster, sin duplicar contexto, y el texto de más que escribe el modelo para el subconjunto relevante es un costo marginal al lado del cuerpo de las noticias.

### La condición vive en el prompt, no en el schema

El `response_schema` estructurado que arma Gemini (vía `google.genai`, a partir del `AnguloGenerado` de Pydantic) no puede expresar "`resumen_redes` es obligatorio solo si `relevancia_social` es `true`" — esa lógica condicional no existe en JSON Schema tal como lo arma la librería. La instrucción vive como texto plano en `construir_prompt`: completar `resumen_redes`/`hashtags` solo si `relevancia_social` es `true`, si no dejarlos vacíos.

Como no es una garantía dura, `_persistir` la refuerza en código — mismo principio que `con_padres_completos` con la jerarquía de tópicos: **el código, no el modelo, garantiza la coherencia.** Si `relevancia_social` da `false`, o si da `true` pero el modelo no llenó `resumen_redes` pese a la instrucción, no se crea ni se toca ninguna fila — se ignora en silencio (con un log) en vez de guardar contenido a medias.

### Tabla aparte (`PublicacionRedes`), no columnas en `Sintesis`

No es 1:1 con toda síntesis — la mayoría de los ángulos no son de relevancia nacional, así que la mayoría de las filas de `Sintesis` no tendrían nada que poner en columnas nuevas. Con columnas nullable, esas columnas quedarían vacías en la mayoría de las filas de la tabla más grande y más consultada del sistema. Con tabla aparte (`sintesis_id` único, FK a `sintesis.id`), solo existe fila donde hace falta.

La migración (`3c175c27adde`) es autogenerada sin ajustes de datos, a diferencia de la de tópicos: es una tabla nueva, no hay nada que transformar. Se recortó a mano un `alter_column` que el autogenerate proponía sobre `sintesis.topicos`/`subtopicos` (los quería `nullable=True`) — es un drift preexistente entre el modelo, que no declara `nullable` en su `Column()`, y la base real (que los tiene `NOT NULL` desde la migración de tópicos). Ajeno a este cambio, no se tocó.

### No se congela, pero tampoco se retracta

A diferencia de `titulo_angulo`/`topicos` (congelados desde la primera síntesis — ver la sección de tópicos más arriba), `resumen_redes`/`hashtags` **sí se actualizan en cada resíntesis**: es contenido de marketing descartable, no la identidad publicada del ángulo, así que reemplazarlo con una versión más nueva no rompe nada del lado del back-end.

Pero si una resíntesis posterior marca `relevancia_social=false` (el hecho creció y el modelo ya no lo considera de relevancia nacional, o cambió de criterio), la fila existente **no se borra ni se vacía** — se deja como está. Mismo principio que ya regía la entrega general: "el motor nunca retracta una publicación entregada" (`specs/webhook_contract.md`, antes punto 9, ahora 10). El copy pudo haber salido ya a Twitter; borrarlo de la base no lo despublica de ahí, y sí le rompe al back-end una fila que tenía.

### Entrega: mismo payload, no un pipeline aparte

`publicacion_redes` se suma como un campo más (nullable) dentro de `sintesis` en el payload que ya arma `webhook_delivery.construir_payload` — no se creó un endpoint ni un estado de entrega/reintento propio. Reutiliza el que ya tiene `Sintesis` (`enviado_backend`/`intentos_envio`): como es 1:1 con la síntesis y viaja en el mismo evento, no había necesidad real de una segunda máquina de estados solo para esto. `sintesis_pendientes` precarga la relación con `selectinload` para no sumar una query por fila al barrido.

### Prompt

`relevancia_social`: `true` solo si el hecho nombra una persona con reconocimiento público o una institución de renombre nacional — un filtro más angosto que el de tópicos, no una categoría más. `resumen_redes` tiene un tope de 240 caracteres (dentro del límite de Twitter con margen para un link) y no debe repetir `resumen_neutro` palabra por palabra. `hashtags` entre 2 y 5, en minúscula, sin `#` — con la aclaración explícita de que Gemini no tiene noción de qué está en tendencia hoy: es insumo crudo para que marketing lo cure, no el hashtag final.

### Resultado

250/250 tests (241 + 9 nuevos: `TestPublicacionRedes` en `test_synthesis.py` — creación condicional, safety net sin resumen, actualización en resíntesis, no-retractación — y dos tests de payload en `test_webhook_delivery.py`). 96% de cobertura total, `publicacion_redes.py` al 100%.

### Validado con una corrida real completa — y un límite del diseño que expuso

Corrida real de punta a punta (ingesta → vectorización → clustering → síntesis) contra Postgres y Gemini reales, 148,4 s, 27 clusters pendientes: 26 sintetizados (1 falló), 26 creados + 4 actualizados. Sobre las 120 síntesis totales de la base, **26 quedaron marcadas `relevancia_social=true`**, 0 filas vacías (el safety net no tuvo que descartar ninguna). Por tópico, la *tasa* de marcado más alta no fue la de mayor volumen: deportes 33% (6/18), política 29% (4/14), internacional 29% (2/7) contra espectáculos 24% (12/49) — coherente con el criterio del prompt ("persona/institución reconocida", no un tema en particular). Ejemplos reales: el reparto de los Fondos de Asistencia Laboral (CNV + Ministerio de Economía) y la reforma del Banco Central en Diputados salieron relevantes por nombrar instituciones de renombre nacional, no personas — el criterio del prompt cubre ambos casos, no solo famosos.

**El límite real, no anticipado en el diseño**: `relevancia_social` solo se decide cuando un cluster tiene cobertura nueva (dispara `clusters_pendientes` → `sintetizar_cluster` → se le manda todo el cluster de nuevo a Gemini). Un cluster que ya cerró y no recibe noticias nuevas **no vuelve a pasar por Gemini nunca**, así que se queda con `publicacion_redes: null` para siempre, sin importar cuán relevante sea. Caso real de esta misma corrida: la síntesis 23 ("Fallecimiento y velorio de Jorge Messi en Rosario") sigue en `null` — el hecho que generó dos de las 26 marcadas relevantes (88 y 89, sobre las repercusiones) — porque el cluster original no volvió a sintetizarse.

De las 94 síntesis sin `publicacion_redes`, solo 4 fueron evaluadas y descartadas explícitamente en esta corrida; las otras 90 son anteriores a que el campo existiera y siguen sin evaluar por el mismo motivo. Se evaluó un backfill puntual (script one-off que reevaluara `relevancia_social` sobre las síntesis viejas sin volver a mandar el cuerpo completo de las noticias) y se decidió **no hacerlo por ahora**: se documenta como límite conocido del diseño en vez de resolverlo, a la espera de que haga falta de verdad. Visualización completa de la corrida (proporción, tasa por tópico, las 26 relevantes con su copy) publicada como artifact para referencia.

---

## Auditoría de llamadas: RSS, base de datos y Gemini

Pedido explícito del usuario: revisar cuántas llamadas hace el pipeline completo (RSS, DB, Gemini) y sacar las que sean evitables. Auditoría estática primero (sin Docker, leyendo el código), después los fixes validados contra la suite en SQLite, y por último una corrida real pendiente de que el usuario levante el contenedor.

### RSS: nada que sacar

Una request por feed (`ingestion._descargar_feed`), reintentos solo ante fallo. El costo ya es el mínimo posible.

### Gemini: ya era 1 llamada por cluster

Confirmado de nuevo sobre el código actual: `llamar_modelo` se llama una sola vez por cluster en `sintetizar_cluster`, sin duplicados. Nada para tocar acá — la revisión confirmó lo que ya se sabía de Fase 4.

### Base de datos: 5 hallazgos, todos corregidos

**1. `preprocessing.get_vectorizador`** — `total = len(session.exec(select(Noticia.id)).all())` traía **todos los ids de `noticia`** (miles de filas) solo para un `len()`, y corría una vez por cada cluster sintetizado. Se cambió a `select(func.count()).select_from(Noticia)`: mismo dato, sin traer una sola fila a Python.

**2. `synthesis.sintetizar_cluster` — dos duplicados lisos por cluster**: `select(Medio)` corría dos veces (una adentro de `construir_evidencia`, otra al armar el prompt) y `select(Noticia).where(cluster_id==X)` también (una para la evidencia, otra al final solo para contar y actualizar `noticias_al_sintetizar`). Se resolvió haciendo que `construir_evidencia` devuelva `medios_por_id` y `total_noticias` ya calculados, y que `sintetizar_cluster` los reuse en vez de volver a pedirlos.

**3 y 4. `clustering._cargar_clusters_abiertos` y `clustering.cerrar_clusters_vencidos`** — el mismo patrón de N+1 que se corrigió en Fase 5 para `synthesis.py`/`search.py`, pero que **no se tocó en esa revisión** porque no se miró `clustering.py`. Cada una traía las noticias de cada cluster con una query aparte, dentro de un loop; con 20-60 clusters abiertos son 20-60 queries evitables por corrida, y `_cargar_clusters_abiertos` la usan tanto `agrupar_pendientes` como `fusionar_clusters_duplicados`, así que el costo se pagaba dos veces.

**5. `ingestion._ya_esta`** — un `SELECT` por cada item del feed (`_procesar_items`), buscando si esa nota ya existía por `guid` o `url`. Con varios feeds por medio son cientos de queries por corrida, la mayoría para descubrir que la nota ya estaba. Se cambió a una sola consulta por feed (`guid IN (...) OR url IN (...)`), con los duplicados **dentro** del mismo feed cubiertos por dos sets en Python (la consulta en lote es de una sola vez, no ve lo que se va agregando en el propio loop).

### El bug que apareció al aplicar el fix 3 — y por qué no se resolvió con `selectinload`

El primer intento de los puntos 3 y 4 fue el mismo patrón que ya usa el resto del código: `selectinload(Cluster.noticias)`. Rompió dos tests de `TestFusionarClustersDuplicados` — después de fusionar, las noticias del cluster absorbido volvían a `cluster_id = NULL` en vez de quedar en el superviviente.

La causa: `fusionar_clusters_duplicados` reasigna `noticia.cluster_id` **por fuera de la relación** (una query aparte, no `cluster.noticias.append(...)`) y después borra el cluster absorbido con `session.delete()`. Si `Cluster.noticias` ya estaba cargada en el identity map —que es justo lo que hace `selectinload`—, SQLAlchemy sigue viendo esas noticias como hijas del cluster que se está borrando, y el cascade por defecto (`save-update`, sin `delete-orphan`) les pone `cluster_id = NULL` al hacer flush del `delete` — pisando el `UPDATE` directo que ya se había hecho.

`cerrar_clusters_vencidos` no tiene este problema porque no borra el cluster ni reasigna las noticias de otro. Por eso el fix quedó distinto en cada función: `cerrar_clusters_vencidos` sí usa `selectinload(Cluster.noticias)`, y `_cargar_clusters_abiertos` (que alimenta tanto `agrupar_pendientes` como la fusión) arma el mapa cluster→noticias a mano con una query en lote (`cluster_id IN (...)`) sin tocar la relación del ORM — mismo resultado, 2 queries en vez de N+1, pero sin dejar un estado que el `delete` de la fusión pueda pisar. Atrapado por los tests existentes, no por inspección: si `TestFusionarClustersDuplicados` no hubiera estado ya escrito, este bug habría llegado a producción.

### Validado con logs reales, antes/después — y un sexto hallazgo mucho más grande que los otros cinco

Con el contenedor levantado, se corrió el pipeline completo dos veces contra Postgres y Gemini reales (con `echo=True` en SQLAlchemy) y se comparó statement por statement contra el log guardado de la corrida real de la sección anterior. Los tres patrones medibles dieron exactamente lo esperado:

| Query | Antes | Después |
|---|---|---|
| Dedup por item en ingesta (fix 5) | 415 | **0** (reemplazadas por 6, una por feed) |
| `select(Medio)` en síntesis (fix 2) | 55 (≈ 1 ingesta + 2×26 clusters) | **23**, exacto: 1 ingesta + 1×22 clusters |
| `COUNT(*)` de `get_vectorizador` (fix 1) | 0 (antes traía filas, nunca contaba) | 22, una por cluster |
| **Total de statements SQL** | **9.755** | **6.448** (−34%)|

Pero el total solo bajó 34%, no lo que los tres fixes de arriba hacían esperar, porque apareció algo que no estaba en la auditoría original: `SELECT ... FROM noticia WHERE noticia.id = :pk` —una fila por vez— pasó de **8.345 a 5.778** ocurrencias. Es el **85-89% de todas las queries de las dos corridas**, con o sin los 5 fixes.

**La causa: `agrupar_pendientes` hacía `session.commit()` por cada cluster nuevo**, para conseguirle el id autoincremental antes de asignárselo a las dos noticias que lo forman. `commit()` expira por defecto los atributos de **todos** los objetos que la sesión tiene cargados, no solo el cluster nuevo. El resto del loop sigue comparando cada noticia suelta contra todas las demás en `_mejor_match`, leyendo `.embedding`/`.cluster_id` de objetos que ya están expirados — cada lectura dispara su propia recarga fila por fila. Con 25 clusters nuevos y 329 sueltas evaluadas en la corrida real, eso cascadea a miles de queries.

No apareció en la auditoría estática porque no se pensó como caso a mirar (no es una lectura en loop, es una escritura), y no lo agarraron los tests de no-escalamiento de los puntos 3 y 4 porque esos parten de clusters *ya existentes* — ninguno pasa por la rama de `agrupar_pendientes` que crea un cluster nuevo, que es la que dispara el `commit()`. Hueco real de cobertura, no mala suerte: se cerró con un test dedicado (`TestCrearClusterNuevoNoEscala`) antes de dar el fix por terminado.

**El fix**: `session.commit(); session.refresh(cluster)` pasa a ser `session.flush()`. Alcanza para que Postgres asigne el id (que es lo único que hacía falta) sin expirar nada, y el `commit()` único que ya cierra la función sigue persistiendo todo al final.

**El invariante correcto para el test no es "misma cantidad de queries sin importar cuántos clusters se creen"** — cada cluster nuevo es un `INSERT` genuino, y eso escala con la cantidad de clusters por diseño, no es N+1. Lo que sí tiene que valer, y es lo que rompía el bug, es que la cantidad de queries no dependa de cuántas noticias sueltas más haya para comparar una vez creado el primer cluster. El test fija `clusters_creados` en 2 en los dos casos y varía solo el ruido de sueltas sin match (3 vs 50): con el bug, más ruido después del primer cluster son más recargas; con el fix, cero de más.

### Resultado

256/256 tests (250 + 6 nuevos: `TestCargaDeClustersAbiertosNoEscala`, `TestCerrarClustersVencidosNoEscala` y `TestCrearClusterNuevoNoEscala` en `test_clustering.py`, `TestDeduplicacionNoEscala` en `test_ingestion.py`). 96% de cobertura total, `clustering.py` al 100%.

El fix de `get_vectorizador` (COUNT en vez de traer filas) y los dos duplicados de `sintetizar_cluster` no tienen test de no-escalamiento propio: no son un N+1 que crezca con filas, son llamadas de más dentro de una sola unidad de trabajo. Se validaron con la comparación de logs de arriba, no con un test de escala.

### Confirmado con una tercera corrida real: el patrón desaparece por completo

Con el fix 6 aplicado, tercera corrida real (14,2 s — esta vez con poco material nuevo: 14 noticias vectorizadas, 1 cluster nuevo creado, 0 sintetizados, así que no es una comparación de carga pareja contra las dos anteriores). Lo que sí es comparable sin depender del volumen es el patrón puntual: **`SELECT ... FROM noticia WHERE noticia.id = :pk` pasó de 5.778 a 0.** Con un cluster nuevo de verdad creado en esta corrida (por la rama de código que antes disparaba el problema) y cero reloads, queda confirmado que el `flush()` en vez de `commit()` elimina el patrón entero, no solo lo atenúa.

---

## Reestructuración de raíz + auditoría de `requirements.txt`/`requirements-dev.txt`

### Raíz del repo: scripts a `scripts/`, docs de Fase 1/2 retirados o movidos a `specs/`

Pedido del usuario tras notar que la raíz competía con `specs/` como fuente de verdad. Verificado antes de tocar nada, no supuesto: el `README.md` decía "Estado: Fase 2 ✅ completa" (tres fases atrás de la realidad) y `QUICK_START.md`/`TESTING.md`/`PRUEBAS_RESUMEN.md` estaban explícitamente titulados "Fase 1" en su primera línea — referenciaban un endpoint `GET /test-db` que ya no existe (retirado en Fase 3) y un `unzip sin_ruido_fase1_complete.zip` que no refleja cómo se usa el repo hoy.

- **Borrados** (nada los citaba como fuente de contenido, a diferencia de `VALIDACION_FASE2.md`): `check_rss.py` (su propio docstring decía "es descartable"), `QUICK_START.md`, `TESTING.md`, `PRUEBAS_RESUMEN.md`.
- **`seed_medios.py` y `verify_setup.py` → `scripts/`**, con un shim de `sys.path` al principio de cada uno (`sys.path.insert(0, ...)`) para que sigan corriendo igual como `python scripts/archivo.py` desde la raíz — sin el shim, `from src...` fallaría porque al ejecutar un script directo Python solo agrega el directorio del script al `sys.path`, no el directorio de trabajo. Verificado corriendo los dos contra Postgres real después de moverlos, no solo por sintaxis.
- **`VALIDACION_FASE2.md` → `specs/validacion_manual.md`**: a diferencia de los tres anteriores, `change_logs.md` (acá mismo, Fase 2) y `tests/test_api.py` lo citaban como referencia real para las queries de chequeo contra Postgres — se movió y renombró en vez de borrarse, con una nota aclarando que el listado de medios del ejemplo es de esa corrida puntual.
- Todas las referencias cruzadas actualizadas (`README.md` y los `specs/*.md` que lo mencionaban).

256/256 tests después del movimiento, sin nada roto.

### `requirements.txt` / `requirements-dev.txt`: 4 dependencias sin uso, y una decisión sobre lint

Auditoría por `grep` de imports reales contra cada paquete listado, no por inspección superficial.

**Sacadas, sin uso en ningún lado y sin plan que las mencione:**
- `fastembed` — el rol de generar embeddings ya lo cubre `sentence-transformers`.
- `litellm` — Gemini se llama directo con `google-genai`, sin capa intermedia.
- `rich` — no se usa ni para logging.
- `newspaper4k` — quedó reservada junto con `trafilatura` desde Fase 2 "por si acaso", pero cuando la segunda vía de ingesta se evaluó de verdad (más arriba, "Segunda vía de ingesta: extracción por URL"), la comparación medida fue solo `trafilatura` contra el RSS actual. La decisión ya está tomada a favor de `trafilatura`; `newspaper4k` nunca compitió por nada.

**Se quedan, aunque no aparecen en ningún `import` directo — son dependencias reales, no sobrantes:**
- `psycopg[binary]` — el driver que SQLAlchemy resuelve desde el esquema `postgresql+psycopg://` de `DATABASE_URL`.
- `python-dotenv` — confirmado con `pip show pydantic-settings`: es una dependencia declarada de `pydantic-settings`, que la usa para leer `.env`.

**Lint: se suma `ruff` a CI, no `black`/`mypy` todavía.** Los tres estaban instalados en `requirements-dev.txt` sin ningún `pyproject.toml`/config ni paso de CI que los corriera — peso muerto real, no una elección deliberada. De los tres:
- `ruff` tiene retorno claro y barato: además de estilo, detecta imports y variables sin usar, nombres no definidos. Sirvió de prueba: correrlo sobre el repo encontró 8 casos reales (`settings` sin usar en `ingestion.py` y `conftest.py`, `os` sin usar en `conftest.py`, `Medio` sin usar en `test_api.py`, `con_padres_completos` sin usar en `test_synthesis.py`, una variable local sin usar, y un f-string sin placeholders en `verify_setup.py`) — todos corregidos antes de sumar el gate. Configurado en `ruff.toml` con `select = ["F"]` (solo pyflakes) a propósito: nada de largo de línea ni estilo, que chocaría con el estilo ya establecido de comentarios largos en español. `alembic/versions/` queda excluido del lint — son migraciones autogeneradas que importan `sqlmodel`/`pgvector.sqlalchemy` por convención de la plantilla de Alembic aunque una migración puntual no los use; no es código de la app.
- `black` (consistencia de formato) y `mypy` (chequeo de tipos) quedan afuera de `requirements-dev.txt` por ahora, no perdidos: `black` importa sobre todo cuando hay más de una persona tocando el código (evita diffs de formato en PRs), y con el proyecto siendo básicamente de un solo desarrollador ese valor es marginal hoy. `mypy` tiene valor real dado que el proyecto ya usa type hints en todos lados, pero SQLModel/SQLAlchemy son dinámicos por diseño (`Relationship`, columnas resueltas en runtime) y configurarlo bien para no ahogarse en falsos positivos es un costo de adopción real, no un `pip install` y listo. Mismo criterio que el resto del proyecto: no resolver un problema que todavía no pesa lo suficiente — se retoma cuando otro desarrollador o equipo toque el código y lo decida.

256/256 tests, `ruff check .` limpio.

---

## Séptimo hallazgo: el mismo patrón de `commit()` en `vectorizar_pendientes`

Al validar el fix del sexto hallazgo con noticias nuevas del día (RSS reales, no un dataset congelado), apareció el mismo patrón en una función que la auditoría original no tocó: `vectorizar_pendientes` en `src/services/vectorization.py`.

### El hallazgo

Corrida real con `echo=True`: 216 noticias pendientes de vectorizar, y **184** ocurrencias de `SELECT ... FROM noticia WHERE noticia.id = :pk` — el mismo patrón de recarga fila por fila del sexto hallazgo, en otra función.

**La causa es idéntica en estructura, distinta en disparador.** `vectorizar_pendientes` carga todo el backlog en una lista (`pendientes`) y lo procesa en lotes de `BATCH_SIZE=32`, con un `session.commit()` al final de cada lote — a propósito, para acotar el tamaño de la transacción con un backlog grande (ver el comentario original del archivo). Ese `commit()` expira los atributos de las 216 noticias cargadas, no solo las 32 del lote recién procesado. En el lote siguiente, `construir_texto(noticia)` lee `titulo`/`contenido_limpio` de objetos expirados y cada lectura dispara su propia recarga. Con lotes de 32 sobre 216 pendientes, eso son 216 − 32 = 184 recargas — coincide exacto con lo medido.

**A diferencia del sexto hallazgo, acá no basta con precomputar antes del loop.** Un primer intento armó todos los textos (`construir_texto`) antes de cualquier commit, pensando que alcanzaba con resolver la lectura. No alcanzó: un test de no-escalamiento (dos lotes de tamaño fijo, variando solo cuántas noticias trae el segundo) siguió fallando, 33 queries contra 6 esperadas. La asignación misma, `noticia.embedding = embedding`, también dispara una recarga sobre un objeto expirado — SQLAlchemy necesita el estado previo del atributo para el historial de cambios, y eso alcanza para gatillar el `SELECT` aunque no se lea nada explícitamente. Expirado, un objeto recarga tanto al leerlo como al escribirlo.

**El fix real: re-consultar cada lote, no cargar el backlog entero de una vez.** `vectorizar_pendientes` pasa a pedir un `COUNT(*)` inicial (para `stats["pendientes"]`) y, dentro del loop, un `SELECT ... WHERE embedding IS NULL LIMIT <tam_lote>` por iteración. Como cada lote ya vectorizado deja de cumplir el filtro `embedding IS NULL`, la siguiente consulta trae automáticamente el próximo lote sin pedir offsets ni IDs a mano. Cada noticia se toca una única vez, en su propia iteración, antes de su propio commit — nunca cruza el commit de otro lote. De paso, ya no hace falta tener todo el backlog en memoria a la vez, un beneficio adicional para un backlog grande que el diseño original no tenía.

No se usó `flush()` en vez de `commit()` (la solución del sexto hallazgo) porque acá el commit periódico es intencional — limitar el tamaño de la transacción con un backlog grande es la razón de ser del loop por lotes, no un descuido.

### El test

`TestVectorizarPendientesNoEscala` en `tests/test_vectorization.py`, mismo criterio que `TestCrearClusterNuevoNoEscala`: se fija la cantidad de LOTES (2) en los dos casos y se varía cuántas noticias trae el segundo lote (3 vs 30). Con el bug, "muchas" tenía muchas más queries que "pocas" (33 vs 6); con el fix, la misma cantidad.

### Validado con dos corridas reales

Antes del fix: 184 recargas sobre 216 pendientes. Después del fix, misma corrida repetida contra Postgres real: **0** recargas de ese patrón durante la vectorización; el puñado residual que quedó (6, en toda la corrida) corresponde a otro código, en escala fija y no relacionada con el tamaño del backlog.

257/257 tests (256 + `TestVectorizarPendientesNoEscala`).

---

## El copy de redes pasa de "resumen corto" a "gancho", y se garantiza que entra en un tweet

### El disparador: ¿entra realmente en Twitter?

Con el copy ya generándose bien, la pregunta siguiente era práctica: en un posteo de X entran 280 caracteres y ahí tiene que caber **el texto, los hashtags y la URL a la nota**. Medido sobre las 91 publicaciones con copy que había en la base:

- **Entraban 90 de 91.** Mediana 210, mínimo 161.
- La que no entraba se pasaba **por exactamente 1 carácter** (id 159, 281).

Dos reglas del conteo de X que definen el presupuesto, y que no son obvias:

- **Cualquier URL cuenta 23 caracteres fijos**, sin importar su largo real, porque X la envuelve en `t.co`. La URL al back-end entra siempre por 23, sea corta o larguísima.
- El límite es de 280 *weighted characters*: los codepoints 0-4351 pesan 1 y el resto 2. **Las tildes y la ñ pesan 1** (verificado: en las 91 publicaciones no había un solo carácter de peso 2), así que para el español el conteo es 1:1.

Presupuesto: `280 − 23 (URL) − 3 (separadores) = 254` para repartir entre texto y hashtags.

### Que entrara el 98% era suerte, no diseño

El tope del schema era 240 y los hashtags hasta 5. El peor caso *permitido* era `240 + 2 + ~70 + 1 + 23 ≈ 336`, que se pasa por 56. Entraba casi todo porque el modelo escribía más corto de lo que se le permitía — el mismo patrón que ya habíamos visto con `relevancia_social`: **el prompt pide, solo el código garantiza.**

### La decisión: no es un resumen recortado, es un gancho

La primera propuesta fue bajar el tope de 240 a 190 y listo. El usuario la corrigió, y el cambio es de fondo y no de número: **un posteo no compite con la nota, invita a abrirla.** El desarrollo está a un click, en la URL del mismo tweet, así que el copy tiene que ser corto y llamativo, no un resumen comprimido.

El ejemplo con el que se calibró, para el apagón en el estadio de Barracas Central:

> `La inesperada falla eléctrica durante el partido del equipo del Chiqui Tapia`

76 caracteres, contra una mediana de 145 de lo que se venía generando.

**Tensión con la neutralidad, y cómo se resolvió.** "Llamativo" empuja justo contra el núcleo del producto. La salida fue distinguir de dónde sale el gancho: **de nombrar lo concreto y reconocible** (la persona, el club, el lugar, la cifra) y **no de adjetivos que valoren ni de clickbait**. El propio ejemplo funciona así: no exagera el hecho, elige el detalle que engancha y nombra a alguien reconocible. El prompt lo pide explícitamente y prohíbe "increíble", "escándalo", "mirá lo que pasó" y las preguntas retóricas.

Objetivo nuevo: **menos de 120 caracteres** (`TWEET_OBJETIVO_RESUMEN`), con aire sobre el ejemplo sin habilitar volver al párrafo.

### `max_length` del schema se queda en 240 a propósito

Podría parecer que hay que bajarlo al objetivo, pero no: `max_length` es una **validación** de Pydantic, así que un gancho de 130 no se recortaría — tiraría `ValidationError` y voltearía la síntesis entera del cluster. El copy de redes es contenido descartable y no puede ser el motivo por el que se pierde una publicación. Queda como cota de tolerancia; el objetivo vive en el prompt y la garantía en el código.

### La garantía: `ajustar_a_tweet`, y por qué recorta en ese orden

El `response_schema` no puede expresar "la suma de estos dos campos más una URL no pasa de 280". `ajustar_a_tweet` lo asegura después, y el orden del recorte no es arbitrario:

1. **Primero se sacan hashtags**, no texto: el resumen es la información y los hashtags son decoración, así que perder un hashtag cuesta menos que perder media oración.
2. **No se baja de 2 hashtags**, que es lo que el contrato le promete al back-end.
3. Recién ahí se recorta el texto, **en borde de palabra** — cortar a mitad de palabra se lee como un error del producto.
4. Caso patológico (dos hashtags larguísimos que no dejan lugar): se van todos. Es preferible un posteo sin hashtags que uno mutilado.

**El recorte lo hace el motor y no el back-end.** Si quedara del otro lado tendrían que cortar sin saber qué parte del texto es prescindible, y cortarían a mitad de palabra; acá sabemos que los hashtags son lo primero que sobra.

### Un bug que encontró el test, no el razonamiento

La primera versión de `_recortar` reservaba 1 carácter para los puntos suspensivos. Pero `…` es U+2026, **fuera del rango 0-4351: pesa 2**. El resultado quedaba 1 punto por encima del límite en el caso justo. Lo agarró `TestAjusteATweet.test_no_baja_del_minimo_de_hashtags_que_promete_el_contrato` antes de que llegara a producción — y es la prueba de que el conteo ponderado importa aun en textos en español, porque el carácter problemático lo agregamos nosotros.

### Validado contra Gemini real

Se le pidió la síntesis de 4 clusters que ya tenían copy, con el prompt nuevo y sin persistir:

| Cluster | Antes | Ahora | Tweet completo |
|---|---|---|---|
| Icardi / Vicuña | 139 | **76** | 130/280 |
| Apagón en La Plata | 125 | **86** | 144/280 |
| Muerte de Hayden Panettiere | 171 | **67** | 137/280 |
| Apagón en Barracas Central | 134 | **97** | 172/280 |

El último es el caso del ejemplo, y el modelo produjo *"La falla eléctrica que dejó a oscuras el estadio de Barracas Central en su inauguración de luces"* — mismo espíritu, y nombra al club en vez de al dirigente, que es más neutro.

268/268 tests (261 + 7 de `TestAjusteATweet` y `test_guarda_el_copy_ya_ajustado_al_tweet`).

### Lo que queda mezclado a propósito

Las 91 publicaciones que ya tenían copy **conservan el texto largo**: `publicacion_redes` no se congela pero tampoco se regenera sola, así que solo se actualizan cuando su cluster vuelva a sintetizarse por cobertura nueva. Durante un tiempo van a convivir ganchos cortos y bajadas largas. No se hizo un backfill por el mismo criterio que con el límite conocido de `publicacion_redes` (más arriba): reprocesar todo el historial con Gemini cuesta y el contenido viejo ya está entregado.

---

# Post-1.0 — Backlog punto 1: segunda vía de ingesta por URL

## Etapa 0: la medición que levanta el candado (18/08/2026)

La sección "Segunda vía de ingesta: extracción por URL" (más arriba) dejó dos candados. El primero —*"se retoma con el back-end integrado y probado"*— quedó cumplido: la corrida del 18/08 entregó 15/15 síntesis al back-end, 221/221 acumulado, cero rechazos de firma. El segundo era explícito y es el que se ataca acá:

> **No entra en la app hasta validar que suma pares reales.** […] falta extraer un día completo de los dos, vectorizar contra el corpus real y contar pares por encima del umbral **antes de escribir una sola línea de producción**.

Se hizo exactamente eso, con un script de medición fuera de `src/` (`scratchpad/validar_extraccion_url.py`): recolectar URLs de 8 feeds de sección de Clarín y 5 de Perfil, filtrarlas con los **mismos** filtros del pipeline real (ventana de `HORAS_CLUSTER_ABIERTO`, `es_en_vivo`, `categoria_no_evento`), extraer con `trafilatura`, vectorizar con `vectorizar_textos` y **replicar el loop de `agrupar_pendientes` en memoria** reusando `_mejor_match` y `_ClusterEnMemoria`, sin escribir en la base. Se corrió una simulación de control sin los medios nuevos para confirmar que el delta es atribuible a ellos.

### Resultado

| Métrica | Resultado |
|---|---|
| **A — clusters publicables nuevos** (decide) | **16**: 4 desbloqueados (tenían 1 solo medio) + 12 nacidos de una suelta + un artículo |
| A — control sin Clarín/Perfil | **0 + 0** — el delta es atribuible a los medios nuevos |
| **B — pareo con el corpus** (diagnóstico) | Clarín 18/60 (30%), Perfil 21/60 (35%) |
| **C — salud de la extracción** | 120/120 extraídos, **0 fallos** |

Salud en detalle, con *n*=60 por medio (la medición anterior era de 6 y el propio change_log la declaraba no concluyente):

| Medio | mediana | p10 | mínimo | s/artículo |
|---|---:|---:|---:|---:|
| Clarín | 3.982 | 2.162 | 1.914 | 0,31 |
| Perfil | 3.099 | 1.615 | **701** | 0,38 |

Referencia con la que se leyó A: una corrida produce hoy ~15 síntesis, así que **≥3 clusters/día justifica el trabajo y <1 reproduce el caso Diario Crónica**. Dio 16. Y B confirma que la agenda se cruza de verdad: no es Crónica.

**El mínimo de 701 caracteres es el dato que faltaba para la etapa 2**: un piso de ~500 atrapa menús y avisos de cookies sin tocar nunca un artículo legítimo.

### La auditoría manual: 14 de 16, y por qué los 2 restantes no son culpa de esta vía

Los pares se auditaron a mano, leyendo los cuerpos y no los títulos —la historia del proyecto dice que los números de clustering engañan cuando no se miran los casos—. **Los 12 nacidos dieron 12/12 correctos** (Albon–Williams 0,913; YPF 0,911; Simeone–Álvarez 0,910; Metalfor 0,895; la fábrica textil 0,803; Mathilde Favier 0,783). **De los 4 desbloqueados, 2 son falsos positivos**:

| Cluster | Artículo entrante | Sim. |
|---|---|---:|
| 431 — ciberseguridad en pagos (Deloitte) + tres medios de pago (Payway) | El Gobierno flexibilizó los créditos en dólares | 0,8008 |
| 439 — informe Idesa sobre el FGS + informe de Trabajo sobre paritarias | 254 mil niños en hogares con piso de tierra | 0,7975 |

La primera lectura fue "Clarín y Perfil traen ruido". **Leídos los cuerpos, es al revés.** Los dos clusters ya están mal armados hoy: cada uno junta dos notas de El Cronista sobre hechos distintos, agrupadas porque la escritura económica de ese medio es semánticamente homogénea. Son el "blob de economía" documentado más arriba, en la Fase 3. Los medios nuevos **no crean el defecto: lo destapan**, dándole a un blob preexistente su segundo medio y volviéndolo publicable. Hoy esos clusters existen igual y lo único que los salva de publicarse es que les falta una voz. Es un punto propio del backlog, no un costo de esta vía.

### Hallazgo lateral: el centroide de un blob atrae más que sus miembros

Al medir la similitud del artículo entrante contra cada miembro por separado:

| | vs. miembro A | vs. miembro B | vs. **centroide** |
|---|---:|---:|---:|
| Par 1 | 0,7118 | 0,7938 | **0,8008** |
| Par 2 | 0,7798 | 0,7300 | **0,7975** |

En los dos casos el centroide atrae más que cualquier miembro individual. Promediar dos notas poco relacionadas da un vector en el "medio genérico" del dominio, y ese punto está más cerca de cualquier nota económica que las notas específicas entre sí.

Sugiere un guardarraíl —exigir que la entrante supere el umbral contra **todos** los miembros y no solo contra el centroide—, que con estos dos casos alcanzaría. **No se verificó contra los 14 pares buenos, así que no se sabe cuántos legítimos rompería: es una hipótesis para medir, no una recomendación.**

### Un falso negativo propio, que vale la pena no repetir

La primera corrida abortó con "20/20 rutas rechazadas" en **ambos** medios. Era mentira. `urllib.robotparser.RobotFileParser.read()` descarga el `robots.txt` con `urllib`, que manda `Python-urllib/3.x`, y Clarín y Perfil devuelven **403 a ese User-Agent incluso para el `robots.txt`**. Por spec un 403 sobre `robots.txt` significa "disallow all", así que el parser hizo lo correcto y rechazó todo — sin haber leído una sola regla.

Lo delató que el resultado fuera demasiado redondo: un diario que bloquea todo tampoco sale en Google, y vive de eso.

**La forma correcta es bajar el `robots.txt` con nuestro cliente y nuestro User-Agent y recién ahí parsear el texto** (`parser.parse(respuesta.text.splitlines())`, no `parser.read()`). Hecho así, los dos dan 200: Perfil es `Allow: /` a secas y Clarín solo bloquea `/api/`, `/_next/`, `/videos/*?`, `/cdn-cgi/` y similares — ninguna ruta de artículo, y ninguno declara `crawl-delay`. Se confirma lo que la medición original había registrado.

Para la etapa 2 esto deja una decisión pendiente: hoy el script **falla cerrado** (si no puede leer `robots.txt`, no extrae), que es lo correcto para una medición, pero en producción significaría perder un medio en silencio.

### Conclusión

**El candado queda levantado**: la vía suma 14 pares reales por día contra un piso de 3, con 0% de fallos de extracción sobre 120 artículos. Se avanza a la implementación.
