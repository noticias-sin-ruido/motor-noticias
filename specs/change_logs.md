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

**El autogenerado de Alembic estaba mal y había que corregirlo.** Agregaba `titulo_angulo`, `enviado_backend` e `intentos_envio` como `NOT NULL` sin `server_default`: contra una base que ya tuviera síntesis, PostgreSQL no puede completar una columna `NOT NULL` sin saber con qué, y la migración falla. Se agregó `server_default` y se lo quita en el mismo `upgrade()`, para que el esquema quede igual al modelo (que define esos valores por defecto en Python). Probado de las dos formas contra una base temporal: desde cero, y con una fila preexistente que quedó correctamente completada. Es exactamente el caso que `conventions.md` advierte al pedir revisar siempre el autogenerado.

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

> ⚠️ **Esta sección afirmaba que ningún test necesita spaCy ni `DATABASE_URL` "porque están mockeados". Era falso, y dejó el CI en rojo cuatro corridas seguidas.** Ver "El CI estaba rojo desde el 12/08", al final de este documento.

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

# Post-1.0

## Backlog punto 1 — segunda vía de ingesta por URL: la medición que levanta el candado (18/08/2026)

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

---

## El CI estaba rojo desde el 12/08 — y no era la cobertura (19/08/2026)

Se revisó el CI a raíz de una sospecha de que no se alcanzaba el 80% de cobertura. **La cobertura nunca fue el problema**: GitHub reportó **87,71%, por encima del gate**. Lo que fallaba eran **31 tests**, y venía fallando en las **cuatro corridas desde el 12/08 — incluida la de la 1.0**, sin que nadie lo mirara.

### Las dos causas raíz

| Falla | Tests | Por qué |
|---|---:|---|
| `OSError: [E050] Can't find model 'es_core_news_md'` | 27 | El job de tests no instala el modelo de spaCy |
| `RuntimeError: DATABASE_URL no está configurada` | 2 | El job no define la variable, y en CI no hay `.env` (gitignoreado) |
| Contadores de `TestManejoDeFallos` | 2 | Consecuencia de las anteriores, no un fallo propio |

**Las dos son el mismo problema de fondo: los tests no eran herméticos, y el entorno local los tapaba.** En la máquina de desarrollo hay un `.env` con `DATABASE_URL` y el modelo de spaCy instalado en el venv, así que `pytest` pasaba en verde localmente mientras el CI —que no tiene ninguna de las dos cosas— fallaba. **Local no es evidencia de que el CI pase.**

La afirmación de Fase 5 —*"ninguno de los 215 tests los necesita (mockeados)"*— era la premisa equivocada. 27 tests de síntesis llegan a spaCy **sin querer**, por la cadena `sintetizar_pendientes` → `construir_evidencia` → `get_nlp`; y `TestPipelineProgramado` parcheaba `main.Session` pero no `main.get_engine`, que igual se ejecuta dentro de `Session(get_engine())`.

### Reproducir antes de arreglar

Las dos condiciones del CI se reproducen localmente con variables de entorno, que en `pydantic-settings` ganan sobre el `.env` (y `get_engine` valida con `if not settings.DATABASE_URL`, así que un string vacío sirve igual que la ausencia):

```bash
DATABASE_URL= SPACY_MODEL=modelo_inexistente pytest
```

Dio **31 failed, 237 passed** — exactamente los números de GitHub. Sin esa reproducción el arreglo se habría verificado contra el entorno equivocado, que es justo el error que causó el problema.

### El arreglo

- **spaCy**: fixture `spacy_mockeado` en `tests/conftest.py`, **`autouse`**. Parchea `preprocessing.get_nlp` con un doc de cero entidades. Es `autouse` a propósito y no una fixture que cada test pida: el que se olvide de pedirla vuelve a romper el CI sin notarlo, que es exactamente lo que ya pasó. Los tests que sí prueban NER de verdad (`test_preprocessing.py`) parchean `get_nlp` con su propio mapa dentro del test, y ese parche gana sobre el global.
- **`DATABASE_URL`**: `patch.object(main, "get_engine")` en los dos tests de `TestPipelineProgramado`, al lado del patch de `Session` que ya estaba.

Se arregló **en los tests y no en el workflow**. Poner un `DATABASE_URL` de mentira o un `spacy download` en el YAML habría puesto el CI en verde igual, pero dejando los tests dependiendo de su entorno — y `es_core_news_md` son cientos de MB descargados en cada corrida, que es justo lo que Fase 5 quiso evitar.

Verificado: **268/268 con las condiciones del CI simuladas**, cobertura 95,30%.

---

## Backlog punto 1, etapa 2 — el extractor: `services/extraccion.py` (19/08/2026)

Se construye **solo el módulo**, sin llamadores. Es deliberado: concentra todas las decisiones de red y de formato, se testea aislado, y deja la costura en `_procesar_items` —el cambio riesgoso— para un diff propio y revisable. `Medio.extraer_por_url` sigue sin leerse en ningún lado de `src/`.

Antes de escribir una línea se mapeó el radio de impacto de la vía completa, porque **el contrato de salida del extractor es lo que determina si las etapas siguientes son seguras**. De ese mapa salieron las decisiones de abajo.

### La decisión que ordena todo: normalizar en el origen

El extractor devuelve el texto **colapsando todo espacio en blanco a un solo espacio**, con exactamente el mismo formato que produce `ingestion.limpiar_html`. No es cosmético: apaga cuatro riesgos de una sola vez y evita tocar tres módulos.

El hallazgo que lo motivó no estaba en el diseño original. `synthesis.py` arma el prompt con bloques `--- NOTA n | medio` unidos por saltos de línea. **Ese delimitador es inequívoco hoy solo porque ningún cuerpo contiene `\n`** — y eso no es una decisión explícita de nadie, es una consecuencia de que `limpiar_html` use `separator=" ", strip=True`. `trafilatura.extract()` devuelve el artículo en párrafos separados por saltos. Metido tal cual, el delimitador deja de ser único y un cuerpo que traiga una línea `--- NOTA` **inyecta estructura en el prompt**.

Se evaluó arreglarlo en `synthesis.py` (delimitador más robusto, o escapado al armar el bloque) y se descartó: el problema no es del prompt, es de que dos vías de ingesta produzcan formatos distintos. Normalizando en el origen las dos quedan **indistinguibles río abajo** y no se toca `synthesis.py`, `vectorization.py` ni `preprocessing.py`.

Queda cubierto por `test_coincide_con_limpiar_html`, que compara las dos vías sobre la misma entrada.

### Fallar cerrado y ruidoso ante un `robots.txt` ilegible

La etapa 0 había dejado esto como decisión pendiente: el script de medición fallaba cerrado en silencio, correcto para medir y malo para producción.

Se resuelve **cerrado y ruidoso**. Cerrado porque no sabemos qué permite el medio y suponer que permite todo no es nuestra decisión. Ruidoso —`alerts.enviar_alerta`— porque el silencio acá significa **perder la cobertura de un medio entero sin que nadie se entere hasta mirar los números**, que es el patrón que el proyecto ya corrigió dos veces. La clave de alerta es por dominio (`robots:{base}`) para que el cooldown agrupe y un medio caído no inunde el mail.

El `robots.txt` se baja con `httpx` y nuestro User-Agent y recién después se parsea con `parser.parse(...)` — **nunca `parser.read()`**, por el falso negativo documentado más arriba. Se cachea por dominio: sin eso se pediría una vez por artículo, que con el feed de Clarín son 10 requests extra por ciclo para releer siempre lo mismo.

### Las otras dos reglas del contrato

- **Nunca propaga excepciones.** Es requisito, no comodidad. Quien va a llamarlo es `_procesar_items`, que corre dentro del `try` de `ingerir_feed`: una excepción que se escape dispara `session.rollback()` —perdiendo el feed entero—, un mail de alerta y un `feeds_fallados += 1`. **Un artículo caído se contabilizaría y se alertaría como un feed entero caído.**
- **No devuelve la URL final tras los redirects.** Extraer implica seguirlos, pero la que se persiste tiene que seguir siendo la del feed: de ella dependen la deduplicación con su índice único sobre `Noticia.url`, `categoria_no_evento`, `topico_declarado` y el payload al back-end. Guardar la final duplicaría el artículo o reventaría contra el índice y tiraría la ingesta del medio.

### Verificación contra artículos reales, y un riesgo que se midió en vez de suponerse

Además de la suite (que no sale a la red), se corrió el extractor contra artículos reales:

| | Clarín | Perfil |
|---|---:|---:|
| Extraídos | 5/5 | 5/5 |
| Con saltos de línea | 0 | 0 |
| Por debajo del piso de 500 | 0 | 0 |
| `robots.txt` pedido | 1 vez | 1 vez |

La prueba que importaba era otra: **el mismo artículo por las dos vías**. La Nación es el único medio donde se puede hacer, porque trae `content:encoded` (vía vieja) y su página es extraíble (vía nueva). Sobre 12 artículos, la similitud textual entre ambas dio 77-95%, y `trafilatura` resultó anteponer **título y bajada** al cuerpo.

Eso toca directamente el riesgo del embedding, que se calcula sobre los primeros `EMBEDDING_CHARS_CUERPO` (500) caracteres. Se midió el efecto real vectorizando las dos versiones de cada artículo con `construir_texto`:

- **El lead real nunca quedó fuera de la ventana**: 12 de 12, con offsets entre 185 y 427 sobre 500. La formulación fuerte del riesgo no se materializó.
- Similitud coseno entre las dos versiones: **mediana 0,9019**, máximo 0,9262, **mínimo 0,7426**. Un caso quedó por debajo de `UMBRAL_SIMILITUD=0,75`.

Del peor caso salió una hipótesis —que el título se duplica, porque `construir_texto` ya hace `f"{titulo}. {cuerpo}"`— que **al verificarla resultó ser un artefacto de La Nación y no de la vía**: el cuerpo arranca con el título en 0 de 6 artículos de Clarín y 1 de 6 de Perfil. Y La Nación no va a usar esta vía. Generalizar desde el medio-proxy habría metido en el extractor una heurística de recorte que ninguno de los dos medios reales necesita.

**No se corrige nada, entonces.** Lo que queda por delante del lead en Clarín y Perfil es la bajada, que no es basura: es un resumen escrito por el propio medio, señal legítima para agrupar. La respuesta empírica ya la había dado la etapa 0, que midió **14 pares reales por día con exactamente este texto**.

Queda registrado como **riesgo residual medido, no como defecto**: la bajada consume entre el 37% y el 85% de la ventana del embedding. Si alguna vez el clustering de estos medios rinde por debajo de lo medido, el primer lugar donde mirar es acá.

### Verificado

**295 tests en verde** (268 + 27 nuevos) con las condiciones del CI simuladas, cobertura **95,37%**, `ruff` limpio. Las 3 líneas sin cubrir de `extraccion.py` son `_descargar_pagina`, la frontera con la red, mockeada en toda la suite a propósito.

---

## Backlog punto 1, etapa 3 — la costura, y por qué la extracción va fuera de la transacción (19/08/2026)

El extractor ya existía pero **no tenía llamadores**: `Medio.extraer_por_url` no se leía en ningún lado de `src/`. Esta etapa lo conecta a la ingesta.

El plan original decía "invertir el orden de filtrado dentro de `_procesar_items`". **Se cambió**, y el motivo fue una pregunta de escalabilidad que llevó a medir en vez de suponer.

### La medición que cambió el diseño

La pregunta era si `trafilatura` sería el punto de inflexión al sumar medios — porque los dos que entran ahora son Clarín y Perfil, pero hay varios más sin `content:encoded` que podrían seguirlos.

Se midió el reparto del costo por artículo, bajando el HTML una sola vez y cronometrando la librería sobre HTML ya en RAM (si no, se estaría midiendo la conexión a Clarín y llamándola "costo de trafilatura"):

| Componente | Tiempo | Share |
|---|---:|---:|
| `trafilatura` (CPU) | 16,9 ms mediana (máx. 67 ms) | **1,3%** |
| Red | 304 ms mediana | 23,0% |
| **Pausa de cortesía propia** | 1000 ms fijos | **75,7%** |

**`trafilatura` no es el cuello de botella a ninguna escala realista**: 1200 artículos son ~20 s de CPU contra un ciclo de 15 minutos, y el pico de memoria de una extracción es 5,4 MB. El 98,7% del costo es esperar.

Lo que la medición sí expuso es **dónde queda la extracción respecto de la transacción**. El `SELECT` de `_existentes` abre una transacción y `ingerir_feed` recién commitea al final. Meter la extracción en el medio —que es lo que decía el plan viejo— la dejaría abierta durante toda la descarga de los artículos: ~40 s en la primera corrida de un medio, reteniendo una conexión del pool de 5 y frenando el vacuum de Postgres sobre ese snapshot. A 2 medios se tolera; a 15 es un problema real.

**De ahí la forma final: decidir qué es nuevo (transacción corta) → extraer (sin transacción) → persistir (transacción corta).** `_procesar_items` se partió en `_seleccionar_nuevas` y `_completar_cuerpos`, con un `commit` de solo lectura en el medio.

### Las decisiones de la costura

- **`extraer_por_url` significa "si el feed no trae cuerpo, buscalo en la URL"**, no "extraé siempre". Un item con `content:encoded` usa ese: es gratis y no sale a la red. Importa para el feed de respaldo de Clarín, que sí lo trae.
- **La extracción va después de deduplicar, nunca antes.** Extraer antes significaría bajar las decenas de artículos del feed entero cada 15 minutos para siempre, en vez de los pocos realmente nuevos. Tiene test propio.
- **Los medios sin la bandera no cambian en nada.** El `commit` intermedio y la extracción están detrás de `if usa_extraccion`: mismo camino, mismas queries, mismas transacciones que antes. Es lo que acota el riesgo del diff.
- **Una nota que no se pudo extraer se descarta**, porque `contenido_limpio` es `NOT NULL`. El feed la vuelve a ofrecer el ciclo siguiente mientras siga en su ventana, así que se reintenta sola. Para un artículo permanentemente inextraíble eso es un request cada 15 minutos mientras dure la ventana: acotado y chico, y no justifica una tabla de lápidas.
- **El warning de "ningún item tenía contenido completo" se condiciona a `not usa_extraccion`**: para Clarín y Perfil ese es el estado normal y permanente, y dejarlo sería ruido cada 15 minutos para siempre — el patrón que el proyecto ya corrigió dos veces. Su equivalente allá es que **fallen todas** las extracciones de un feed, que ahora avisa por mail: es la firma de un rediseño del maquetado, y sin aviso es perder un medio entero en silencio.

`sin_contenido` deja de contar los items sin cuerpo en los medios con extracción —ahí son candidatos, no descartes, y contarlos haría que el número sea siempre igual al tamaño del feed— y su señal pasa a `extraidas` y `extraccion_fallida`.

### Dos tests que se verificaron por mutación

El invariante de la transacción tiene un test que lo afirma directamente: el mock de `extraer_varios` registra `session.in_transaction()` al ser llamado. Como es **el test que justifica todo el rediseño**, se comprobó que puede fallar: quitando el `commit` intermedio, da `[True] == [False]`. Un test que no puede fallar no prueba nada.

El otro cierra un hueco que apareció al auditar la etapa: **el guardia de N+1 de Fase 5 cubre solo la vía vieja**, porque `TestDeduplicacionNoEscala` usa la fixture `medio`, que no tiene la bandera. La propiedad se cumplía en la vía nueva —medido a mano— pero nada la protegía en CI.

Al escribirlo apareció una trampa que vale la pena dejar anotada: **replicar el test existente no alcanza.** Aquel aísla precargando duplicados, y con todo duplicado `_procesar_items` sale por el `return` temprano **antes** del commit intermedio, así que el camino nuevo ni se ejercita. Hacen falta los dos casos, y se comprobó reintroduciendo el `_ya_esta` de antes de Fase 5 —un `SELECT` por artículo— en el loop de inserción: el test de notas nuevas lo detecta (74 contra 37 esperadas) y **el de duplicadas pasa igual**. Solo él habría sido un guardia falso.

Un detalle de la medición: las dos corridas hay que arrancarlas del mismo estado (`session.expire`). Si no, la primera encuentra el `Medio` recién cargado y la segunda lo encuentra expirado por los commits de la primera, y la diferencia de un `SELECT` de refresco —costo fijo, O(1) por corrida— se lee como si fuera crecimiento por artículo.

### Verificado

**304 tests en verde** (295 + 9 nuevos) con condiciones de CI simuladas, cobertura **95,23%**, `ruff` limpio, `alembic check` sin drift.

Y punta a punta contra **Clarín real y Postgres real**, con un medio temporal `activo=False` que se borra al terminar:

| | Resultado |
|---|---|
| Artículos extraídos y persistidos | **10/10**, entre 2.745 y 7.432 caracteres |
| Con saltos de línea o bajo el piso | **0** |
| Segunda corrida | **0 extracciones**, 10 duplicadas |
| `in_transaction()` al extraer | **False** |

La segunda corrida es la que importa: confirma contra un feed real que la deduplicación corre antes que la extracción, que es lo que evita bajar el feed entero cada ciclo.

### Lo que queda pendiente a propósito

Correr el **pipeline completo** con noticias extraídas —vectorización, agrupamiento y síntesis— se difiere a la etapa 5, cuando los medios estén dados de alta de verdad. Hacerlo ahora exigiría o bien agrupar noticias temporales contra los clusters reales (mutando estado compartido que después hay que deshacer), o bien gastar presupuesto de Gemini en una síntesis de prueba. La promesa de que río abajo nada distingue las dos vías ya está respaldada por la etapa 0 (14 pares reales por día con este mismo texto) y por `test_coincide_con_limpiar_html`.

**Concurrencia entre medios**: la pausa de 1 s se aplica hoy entre *todos* los artículos, aunque sean de dominios distintos — pero la cortesía se le debe a un medio, no al proceso. Paralelizando entre medios el costo deja de ser la suma y pasa a ser el máximo por medio (~40 s), sea N=2 o N=40. **Disparador: pasar los ~10 medios**, donde la primera corrida ya llega al 44% del ciclo. No hace falta para dos.

---

## Backlog punto 1, etapa 4 — los márgenes del scheduler, y volver audibles sus fallos (19/08/2026)

`add_job` nunca declaró `max_instances`, `coalesce` ni `misfire_grace_time`, así que corría con los defaults de APScheduler 3.11.3 — verificados leyendo `BaseScheduler._configure`, no supuestos: `max_instances=1`, `coalesce=True`, `misfire_grace_time=` **1 segundo**.

Pero el problema de fondo no eran los tres parámetros, que son cinco líneas. **Era que todas las formas en que el scheduler puede perder una corrida son silenciosas**: terminan en un `WARNING` de la librería sobre un stdout que además no se persiste (no hay `basicConfig` ni handler de archivo en todo `src/`).

### Las tres formas de perder una corrida son distintas

Vale precisarlo porque es fácil mezclarlas —y se mezclaron al discutir la etapa—:

| Evento | Cuándo | Lo gobierna |
|---|---|---|
| `EVENT_JOB_MAX_INSTANCES` | Una corrida se pasa del intervalo y la siguiente la encuentra viva | `max_instances` |
| `EVENT_JOB_MISSED` | El scheduler llega tarde a **lanzar** la corrida | `misfire_grace_time` |
| `EVENT_JOB_ERROR` | El job levanta una excepción antes de que `_correr_paso` pueda atraparla | — |

La tercera no estaba en el diagnóstico original y es igual de real: `_correr_paso` protege cada paso del pipeline, pero **no la apertura de la sesión que los envuelve**. Con la base caída, `Session(get_engine())` falla y el error se escapa por ahí.

Dato que acota el alcance: el scheduler usa `MemoryJobStore` y el job se re-agrega en cada `lifespan`, así que **no hay recuperación de corridas perdidas tras una caída**. Los misfires solo pueden venir de demoras dentro del proceso.

### El detalle que ordenó el diseño del listener

`AsyncIOScheduler.wakeup` está decorado con `@run_in_event_loop`, y `BaseScheduler._dispatch_event` invoca los listeners **sincrónicamente**. O sea que el listener corre **dentro del event loop** — y `enviar_alerta` abre una conexión `smtplib.SMTP` bloqueante, sin timeout explícito. Llamarla derecho congelaría la API entera mientras dure el intercambio, y un servidor SMTP colgado la dejaría sin responder.

Por eso el aviso se delega a un hilo daemon. **No** se usa el executor del loop: es el mismo que corre el pipeline, así que con una corrida larga en curso el aviso de que la corrida es larga quedaría encolado detrás de ella.

Hay una asimetría que conviene tener presente porque es contraintuitiva: la **misma** `enviar_alerta` se llama directo y sin problema desde `_correr_paso` y desde el canario de duración, porque el job corre en el threadpool (`AsyncIOExecutor` manda las funciones sync a `run_in_executor`), no en el loop. Lo que cambia no es la función sino desde qué hilo se la llama.

### El canario, y por qué es una fracción

Cada corrida loguea ahora su **utilización** (`duración / intervalo`), y si pasa el **50% del intervalo** avisa. El umbral es una fracción y no un número de segundos a propósito: si el intervalo cambia —y es justo lo próximo a calibrar— el canario lo sigue solo en vez de quedar desincronizado en silencio.

Es además el instrumento que cierra la conversación de escalabilidad: la concurrencia entre medios se difirió hasta pasar los ~10, y esto avisa cuando llegamos en vez de depender de que alguien se acuerde de mirar.

### `INGEST_INTERVAL_MINUTES` pasa a `config.py`

Era una constante de módulo. Se mueve porque **es el parámetro que hay que calibrar con datos**, y que calibrarlo exija editar código y redeployar convierte una prueba de una tarde en un cambio de versión. Es la excepción deliberada a los otros tres, que quedan como constantes en `main.py` por estructurales.

### El baseline, que era la incógnita

No había logs persistidos, así que no se sabía cuánto tarda una corrida. Medido contra la base real, con el pipeline completo:

| Corrida | Total | Síntesis | Utilización del ciclo |
|---|---:|---:|---:|
| Con backlog (24 ángulos) | 206,58 s | 188,38 s | **23,0%** |
| Sin síntesis pendiente | 10,55 s | 0,03 s | **1,2%** |

**La síntesis es el 91% del costo cuando hay material**, y prácticamente cero cuando no. Todo lo demás es plano: ingesta ~4-7 s (6 feeds), vectorización ~6 s, agrupamiento ~0,6 s, fusión y entrega por debajo de 0,1 s. Los 24 ángulos —20 nuevos y 4 actualizados— salieron a ~8 s cada uno.

Vale anotar por qué la entrega reportó 24 y solo se crearon 20: `sintetizar_pendientes` **reentrega** una síntesis existente cuando su cluster recibió material nuevo, poniendo `enviado_backend = False` porque el payload cambió. No es un doble envío del mismo contenido.

**El umbral de 450 s queda 43 veces por encima de una corrida normal y 2 veces por encima de una con backlog de 24 ángulos.** Dispara alrededor de los **55 ángulos en una sola corrida**, que es el tamaño de backlog que deja una caída de varias horas — exactamente el evento del que uno quiere enterarse. Calibrado.

### Sobre el intervalo, con los números a la vista

La predicción que se había hecho antes de medir la síntesis —"si no cambia el orden de magnitud, la conclusión será acortar el intervalo"— **era incorrecta en su premisa**: la síntesis sí cambia el orden de magnitud, del 1,2% al 23%.

Pero la conclusión sobrevive, por un motivo distinto: **el trabajo de síntesis por día lo fija la cantidad de noticias, no el intervalo.** Acortar el ciclo no genera más ángulos, los reparte en más corridas; alargarlo los concentra en menos. Con ~100 ángulos diarios la utilización queda cerca del 2% en cualquiera de los tres escenarios (10, 15 o 20 minutos), así que **el intervalo hay que decidirlo por frescura y no por capacidad**.

Y alargarlo tampoco ayuda contra el caso que sí aprieta —el backlog tras una caída—, porque el backlog tiene el mismo tamaño en cualquier intervalo.

### Verificado

**314 tests en verde** (306 + 8 nuevos) con condiciones de CI simuladas, cobertura **95,69%**, `ruff` limpio.

Las cinco piezas se verificaron por mutación, que a esta altura es la costumbre de la casa: sacar el `add_listener`, sacar `misfire_grace_time`, subir `max_instances` a 2, mandar el mail en el hilo del listener y sacar el canario. **Las cinco las detectan los tests.**

---

## Backlog punto 1, etapa 5 — el alta de Perfil, y por qué Clarín queda afuera (20/08/2026)

El plan original era dar de alta Clarín y Perfil juntos. Antes de tocar código se hizo algo que nunca se había hecho ni para ellos ni para los seis medios que ya corrían: **leer los términos de uso del RSS**.

### La revisión

| Medio | Cuerpo en el feed | Términos | Veredicto |
|---|---|---|---|
| Clarín | 0/438 | Licencia limitada a "títulos y/o links"; prohíbe usar el Servicio de otro modo | ❌ Descartado |
| Perfil | 0/438 | Licencia sobre "el contenido"; pide links de vuelta (los damos) | ✅ Elegido |
| Ámbito | 0/137 | Sin contrato de reuso; su aviso legal solo cubre datos personales (Ley 25.326) | Alternativa viable, postergada |
| La Izquierda Diario | 48/48 | Sin términos propios, pero `robots.txt` bloquea crawlers de IA y reserva TDM (Directiva UE 2019/790 art. 4) | Postergado |

Lo que decidió lo de Clarín no es que falte la herramienta —la etapa 2 construyó justo el extractor que iría a buscar lo que el feed no trae—, es que **retienen el cuerpo a propósito**: 0 de 438 ítems es coherente con una licencia que cubre solo títulos y links. Ir a buscarlo por URL igual sería cruzar una línea que el medio trazó, no una carencia técnica que resolver.

**Replanteo que salió de ahí:** `extraer_por_url` no es una bandera técnica, es la marca de los medios donde el motor cruza esa línea. Los que traen `content:encoded` publicaron el cuerpo; los que no, lo retuvieron.

### La medición de Perfil

48 artículos extraídos de 4 secciones, contra el corpus real dentro de la ventana de 12 h: política 5/12, economía 5/12, sociedad 3/12, policía 2/12. 14 pareos colapsaron en 12 hechos distintos (1,2 notas por hecho) — con ~116 artículos únicos/día, proyecta ~25 hechos/día contra un piso de 3. Perfil pasa con holgura.

**Un solo feed, no cuatro** — igual que el resto del roster, y por el mismo motivo, ahora comprobado también para Perfil: el general es una ventana móvil de 7,1 h que cubre el 94% de lo fresco; las 4 secciones juntas aportan solo 2 ítems que el general no traiga. `/feed/internacionales` da 404 pese a estar publicado en la propia página de RSS de Perfil — no se siembra.

### El filtro de opinión (`CATEGORIAS_NO_EVENTO["opinion"]`)

El feed general trae columnas junto con las noticias. Una columna no reporta un hecho —es género, no tema—, así que entra al mismo mecanismo que ya filtraba horóscopos y recetas (`services/categorias.py`).

Patrón: `/opinion[/-]|/columnistas/|/modo-fontevecchia/`. El anclaje a segmento de URL se verificó contra la base real antes de aplicarlo: `opinion` suelto capturaba 30 URLs, de las cuales una es un falso positivo genuino (`paparazzi.com.ar/.../la-letal-opinion-de-yanina-latorre-...`, no es una columna) y el resto son columnas reales, incluyendo el caso donde el ancla sí tiene que dejar pasar `/economia/opinion-...` de La Nación. `/opinion[/-]` anclado captura las 29 legítimas y excluye la única falsa.

El patrón completo (opinion + columnistas + fontevecchia) alcanza 85 notas sobre las 4.485 de la base, de las cuales **7 ya estaban en un cluster** —todas columnas de El Cronista bajo `/columnistas/`—, así que no hay daño retroactivo nuevo que reparar.

Verificado en producción tras la ingesta real de Perfil: el patrón capturó exactamente 2 de las 47 notas nuevas (`/noticias/opinion/...` y `/noticias/modo-fontevecchia/...`), ambas columnas reales, cero falsos positivos.

### Verificado

- 314 tests en verde, `ruff` limpio, `alembic check` sin operaciones pendientes.
- Seed real: Perfil alta (id=11), un feed, `extraer_por_url=True`.
- Ingesta real: 47 nuevas, 0 duplicadas, 3 en vivo filtradas, **0 fallos de extracción sobre 47**, `robots.txt` pedido una sola vez, 64,2 s (≈ lo proyectado para el pico de la primera corrida).
- Vectorización + agrupamiento reales: Perfil formó 2 clusters propios en esta corrida puntual (no cruzó con otro medio en esta ventana particular); la tasa de pareo contra el corpus ya estaba medida aparte.

### Cabos sueltos que quedaron reformulados

`synthesis.py`, `roadmap.md` y `tech_stack.md` nombraban la "capa gratuita" de Gemini al hablar del rate limit. Se reformuló como "el proveedor limita por minuto" — la afirmación de rate limit sigue siendo cierta para cualquier proveedor y deja de exponer que la instancia corre sobre un tier gratuito. `README.md` se deja igual a propósito: bajarle la barrera a quien evalúa el repo pesa más ahí que en código de producción.

### Decisiones que quedaron abiertas, sin resolver en esta etapa

- **`bloomberg` en el feed de Perfil** (5 de 50 ≈ 10%): contenido sindicado, no es la voz editorial de Perfil que la comparativa quiere medir, y agrega derechos de un tercero. Sin filtrar todavía.
- **Los seis medios ya activos nunca se revisaron por términos de uso.** La Nación y TN son los que más volumen aportan; quedan pendientes de la misma revisión que sí se les hizo a Clarín, Perfil, Ámbito y La Izquierda Diario.

---

## La vida útil de un permiso leído — el `robots.txt` deja de durar semanas (20/08/2026)

Sale de la revisión con subagente del punto 1 completo, que encontró que la caché de `robots.txt` no expiraba nunca. Es el hallazgo B1 de esa revisión.

### El bug, que era peor de lo reportado

`_robots` es un diccionario de módulo y `_parser_robots` abre con `if base in _robots: return _robots[base]`. Ante cualquier fallo se guarda `None` y **ese `None` decide para siempre**: el `except` que loguea y manda el mail queda del otro lado del cortocircuito, así que **no se vuelve a ejecutar nunca**.

De ahí dos cosas que no se habían visto:

1. **El cooldown de 60 minutos de la alerta era código muerto.** `clave=f"robots:{base}"` se diseñó para no inundar la casilla ante fallos repetidos, pero la caché hacía imposible que el fallo se repitiera. Salía un mail, uno solo, para siempre.
2. **El docstring afirmaba algo que el código no hacía.** Decía "ya consultados *en esta corrida*", que es la semántica correcta; pero `limpiar_cache_robots()` solo se llamaba desde `tests/test_extraccion.py`. En producción la caché vivía lo que viviera el proceso — con el scheduler embebido, semanas.

El costo real: un blip de red de un segundo dejaba a Perfil sin extraer hasta el próximo reinicio, en silencio. Y del lado del permiso, si el medio agregaba un `Disallow` no nos enterábamos nunca — seguíamos extrayendo con una autorización leída semanas atrás. Para una vía que existe justamente para ir a buscar lo que el medio no publicó en su feed, eso no es aceptable.

### Los tres caminos que se evaluaron

| | Cómo | Por qué no / por qué sí |
|---|---|---|
| **A. TTL por reloj** | Dos settings nuevos: 24 h de éxito (RFC 9309 dice que un crawler *SHOULD NOT* cachear más que eso) y ~300 s de fallo | ❌ El TTL de fallo tiene que quedar **por debajo de `INGEST_INTERVAL_MINUTES`** para garantizar el reintento. Eso pone una trampa silenciosa justo en la perilla que el roadmap dice que hay que calibrar con datos. Además depende del reloj de pared, que ya nos dio un problema (ver M5 de la misma revisión) |
| **B. Caché por corrida** ✅ | `ingerir_todos_los_medios` llama `limpiar_cache_robots()` al arrancar | Elegido |
| **C. Persistir el `robots.txt` con `fetched_at`** | Tabla propia | Tiene un valor que los otros no: **deja rastro auditable** de qué permiso leímos y cuándo. Pero es esquema + migración para lo que B resuelve en una línea. Queda anotado para el punto 3 del backlog (alta de medios por el operador), donde encaja natural |

**Por qué B.** No inventa configuración, no depende del reloj, no introduce ningún acoplamiento —la vida de la caché *es* la corrida, cambie el intervalo lo que cambie— y **hace verdadero el docstring en vez de reescribirlo**. Además convierte en código de producción una función pública que hasta ahora solo usaban los tests, que era el olor de fondo. Efecto lateral: como el `except` se vuelve a entrar cada ciclo, el cooldown de 60 minutos pasa a estar vivo y a hacer lo que se diseñó que hiciera.

`ingerir_todos_los_medios` es el punto de inserción correcto porque es el **único** punto de entrada en producción: lo llaman el scheduler y `POST /ingest`.

### El costo, medido y asentado

Releer el `robots.txt` una vez por medio y por ciclo cuesta **~0,3 s y ~1 KB** (medido: Perfil 0,326 s · La Nación 0,291 s · TN 0,652 s · El Cronista 0,293 s). Con 20 medios son 6 s por ciclo, el **0,67%** — **no mueve el techo de ~20 medios**, que lo fija la pausa de cortesía de 1 s por artículo.

Quedó documentado en `tech_stack.md` como **punto de quiebre 12**, que además llenó un hueco: el costo de ingesta por medio no estaba en la lista de puntos de quiebre, que es donde un operador lo buscaría antes de dar de alta un medio. Ahí está la distinción que importa y que ya se prestó a confusión una vez: en **régimen** 20 medios usan ~9% del ciclo, pero **dar de alta** 20 medios de una vez son ~1.370 s contra un ciclo de 900 y se pasa. El límite aprieta en el momento del alta, no en la operación normal.

### Verificado

Dos tests nuevos en `TestVidaUtilDelRobotsTxt`, **de comportamiento y no de `assert mock.called`**: envenenan la caché como lo haría una corrida anterior y verifican el resultado observable.

- `test_una_corrida_no_hereda_el_robots_cacheado_por_la_anterior` — cachea `None` y verifica que el artículo se extrae igual (el lado de la cobertura).
- `test_un_disallow_nuevo_se_respeta_en_la_corrida_siguiente` — cachea un parser permisivo, sirve un `Disallow: /` nuevo y verifica que **no** se extrae (el lado del permiso).

Verificados por mutación: sacando la llamada a `limpiar_cache_robots()`, **fallan los dos**.

---

## URLs malformadas — validar en el borde en vez de defender cada consumidor (20/08/2026)

Hallazgo B2 de la revisión con subagente. Arrancó como "`urlparse` está fuera del `try` en `extraccion.permitido`" y terminó siendo una decisión sobre **dónde se valida un dato que entra**.

### El problema tenía tres capas, no una

1. **La reportada**: `permitido` llamaba `urlparse(url)` sin protección. `urlparse("http://[::1/nota")` levanta `ValueError`. Como `permitido` corre dentro del `try` de `ingerir_feed`, esa excepción costaba **rollback del feed entero, mail de alerta y un `feeds_fallados`**: un artículo con la URL rota se contabilizaba y se alertaba como el medio entero caído.

2. **La que no se había visto**: `can_fetch` hace `urlparse(unquote(url))` puertas adentro. Una URL percent-encoded **sobrevive al primer parseo y revienta en el segundo** — verificado: `https://x.com%5B/nota` pasa `urlparse` con `netloc='x.com%5B'` y hace levantar a `can_fetch` cuando el `%5B` vuelve a ser `[`. Un arreglo que solo envolviera `urlparse` dejaba abierta la vía **más probable** de las dos, porque el percent-encoding en una URL de feed es corriente.

3. **La que estaba fuera del alcance del ticket**: `topicos._primeros_segmentos` también hace `urlparse(url)` sin `try`, y lo llama `synthesis` al armar el prompt. Ese camino **no pasa por la extracción**: una URL malformada de un medio *sin* `extraer_por_url` entra a la base igual, porque el cuerpo viene del `content:encoded` y nadie valida el link. O sea que **la exposición es anterior a toda la segunda vía**; no la introdujo este trabajo. Ahí el radio está contenido por el `except` por cluster de `sintetizar_pendientes`, pero el cluster falla su síntesis **cada ciclo hasta cerrarse**: una píldora envenenada.

### La decisión: una validación en el borde, no tres parches

Defender consumidor por consumidor es **exactamente la vigilancia que produjo B2**: `extraer_articulo` promete no propagar excepciones y esa promesa se sostenía con un `try` por operación riesgosa, hasta que a una se le pasó.

Dato que ordenó la elección: **`Noticia(...)` se instancia en un solo punto de todo `src/`** (`ingestion.py`). Hay un único borde por donde una URL entra al motor. Validar ahí cubre de una a `extraccion.permitido`, `topicos.topico_declarado`, `categorias.categoria_no_evento` **y el payload que va al back-end** — porque una URL rota es salida rota se extraiga o no.

Se implementaron las tres, en profundidad y no como alternativas:

| | Qué hace |
|---|---|
| `ingestion.url_utilizable` | Valida en el borde, con contador propio en `stats` |
| `extraccion.permitido` | Envuelto entero: es una guarda, y una guarda que no puede responder tiene que decir que no |
| `extraccion.extraer_varios` | `try` por artículo: uno podrido no se lleva el lote, con el mismo criterio con que `ingerir_feed` aísla los feeds |

**El criterio de validación es el mínimo** para que la URL sirva de algo: que `urlparse` no reviente, que sobreviva a `unquote`, y que haya esquema y dominio. Medido contra las **4.532 URLs reales de la base: cero rechazos**, así que no descarta nada de lo que hoy funciona.

### Decisiones menores que importan

- **Contador propio (`url_invalida`) y no `sin_contenido`.** Son descartes por motivos distintos, y mezclarlos además falsearía el aviso de "ningún item traía contenido", que compara ese contador contra el total de items del feed.
- **El log de `permitido` no afirma la causa.** Dice "no se pudo evaluar el permiso" con el tipo de excepción, y no "URL malformada", aunque sea la causa conocida: si mañana un refactor mete un `TypeError` ahí, un log que declare la causa equivocada manda a quien depure por el camino de al lado.
- **La red del batch usa `logger.exception`.** El riesgo de una red así es tapar un bug propio; el traceback completo es lo que evita que sea silenciosa. El test lo verifica explícitamente.
- **Las URLs relativas se descartan** (`/nota/123`, que emiten algunos feeds descuidados). Es lo correcto —no se pueden pedir ni entregar— pero es un cambio de comportamiento consciente. Resolverlas contra `Medio.url_base` sería una funcionalidad aparte, no un arreglo.

### Por qué ahora, si nunca pasó

**0 de 4.532.** El bug es enteramente latente: ningún CMS real nos mandó nunca una URL así. La justificación no es la evidencia presente sino el **punto 3 del backlog**, que abre la puerta a feeds arbitrarios cargados por cada operador. Sin ese plan, esto sería sobreingeniería y con arreglar `permitido` alcanzaba.

### Verificado

**330 tests** (+14), `ruff` limpio, `alembic check` sin pendientes. Mutación **independiente por cambio**: cada guarda falla solo la suya, ninguna se cubre por accidente con otra. La de `permitido` falla en **las dos** parametrizaciones, o sea que cubre tanto el `urlparse` directo como la vía de `can_fetch` al decodificar.

Un test preexistente (`TestDeduplicacionNoEscala`) comparaba el dict de `stats` completo y hubo que agregarle la clave nueva. Se había afirmado que ningún test lo hacía; la verificación que lo descartó buscaba `assert stats ==` y este usa `assert stats_pocos ==`. Anotado como recordatorio de que un `grep` que no encuentra nada no prueba que no haya nada.

---

## Los parámetros de `trafilatura` van explícitos — y el hallazgo que no era lo que parecía (20/08/2026)

Hallazgo B3 de la revisión. Se reportó —y se repitió como hecho— que `trafilatura.extract(html)` corría con `include_comments=True`, así que **los comentarios de lectores estaban entrando al cuerpo** y contaminando embeddings, TF-IDF, NER y el prompt de síntesis.

### Lo que se verificó y lo que no

El default **sí** es `include_comments=True` (comprobado sobre la versión instalada, 2.2.0). Lo que no se había verificado es **la consecuencia**: que los comentarios entraran de verdad.

A/B sobre 10 artículos reales de Perfil, con spread de largos, bajando cada página una sola vez:

| Configuración | Total extraído |
|---|---:|
| Actual (defaults) | 56.055 |
| `include_comments=False` | **56.055** |
| `include_comments=False, include_tables=False` | 56.055 |
| `include_comments=False, favor_precision=True` | 54.792 (−2,3%) |

**0 de 10 artículos difieren.** Byte por byte idénticos. Perfil no sirve los comentarios en el HTML —los carga por JS, como muchos medios— así que el parámetro nunca tuvo nada que capturar. **Las 47 notas ya ingeridas están limpias y no hubo nada que remediar.**

Antes de eso se había buscado la contaminación por otra vía: el largo. Hay un outlier de 20.735 caracteres, 2,5× el siguiente. Auditado contra la fuente, es **legítimo** — un ensayo largo de Fontevecchia con firma de producción al final. Sirvió para descartar una idea que parecía buena: un **techo** de caracteres simétrico al piso de `EXTRACCION_MIN_CARACTERES`. Un techo que descarte habría tirado una nota real. Queda anotado que la única versión defendible sería un techo que **avise y no descarte**, como el canario de duración del scheduler.

Y sirvió para entender por qué la señal de largo no alcanzaba de todos modos: si los comentarios entraran, entrarían en *todas* las notas proporcionalmente y no habría outlier que mirar.

### La decisión

El arreglo se hizo igual, pero cambia lo que es: **de "limpiar una contaminación" a "cerrar un riesgo latente"**, igual que B1 y B2.

**Los parámetros van explícitos, incluso los que coinciden con el default.** La lección de B3 no es el valor de uno sino que **un default decidiera qué guardamos sin que nadie lo eligiera**. `include_tables=True` se conserva a propósito: en una nota de economía o de elecciones la tabla es contenido, no maquetado. `favor_precision` queda apagado — recorta 2,3% y, sin comentarios que sacar, lo que saca es contenido o boilerplate indistinguible: riesgo sin beneficio medido.

**Y se le puso techo de versión mayor a la dependencia**: `trafilatura>=2.0.0,<3.0.0`. Los parámetros explícitos cubren los que hoy conocemos; un major nuevo puede cambiar otro y volver a decidir por nosotros. Subir de 3.x pasa a ser una decisión que se toma leyendo el changelog, no un `pip install`.

### Verificado

El test **no mockea `trafilatura`**: lleva HTML con un bloque de comentarios de verdad y la biblioteca corre en serio, así que también avisa si una versión nueva cambia el comportamiento — que es el modo de falla que produjo esto. Se comprobó **antes** de escribirlo que ese HTML distingue las dos configuraciones (801 vs 738 caracteres), para que no fuera una guarda vacía. Mutación: volviendo al default, falla.

Dato menor pero anotable: un artículo dio 1.405 caracteres en la base contra 1.316 al re-bajarlo. El medio lo editó después de que lo ingerimos. No es un bug, pero confirma que los cuerpos que guardamos son una foto y no la nota viva.

---

## El filtro de opinión, ahora con guardas — y `/editoriales/` (20/08/2026)

Hallazgo B4. El patrón de opinión de la etapa 5 era **el único entregable sin ninguna guarda en CI**: la revisión lo probó por mutación desanclándolo a `opinion|columnistas|fontevecchia`, y los 331 tests pasaban igual. Todo el razonamiento de la medición —"29 legítimas y excluye la única falsa"— no estaba sostenido por nada.

### Lo que apareció al medir rama por rama

| Rama | Notas |
|---|---:|
| `/opinion/` | 29 |
| `/columnistas/` (El Cronista) | 56 |
| `/editorial(es)?/` (La Nación) | **4 — no estaban en el patrón** |
| `/modo-fontevecchia/` | 1 |
| `/opinion-` | 1 |

**`/editoriales/` era un hueco real, y no teórico**: uno de esos 4 —"Colombia, ante grandes desafíos", cluster 345— **ya estaba agrupado con cobertura de un hecho**, que es exactamente la contaminación que este filtro existe para evitar. Se agregó. La sobre-captura del otro sentido de la palabra la cubre el anclaje a segmento: `/cultura/editorial-planeta-lanza-...` no matchea, porque después de "editorial" el patrón exige `/` o `es/`, no `-`.

### La rama `-` se mantiene, y por qué

Aporta **una sola** nota: `/economia/opinion-los-municipios-socios-...` de La Nación, cuyo título arranca con "Opinión.". Es la rama más floja del patrón y **no hay regex que la distinga** de un titular de noticia que empiece con "opinión dividida sobre...".

Se conserva por la **asimetría de los errores**:

- *Falso positivo* — la nota no agrupa, pero sigue **guardada y buscable**. Recuperable, daño bajo.
- *Falso negativo* — una columna entra al agrupamiento y la síntesis termina comparando una opinión contra crónicas. Eso ensucia el producto, que es la razón de ser del filtro.

Quedó un test que fija ese trade-off **con el comentario que lo explica**, para que nadie lo "arregle" después y pierda el caso de La Nación.

### Lo que quedó afuera a propósito

`/opiniones/`, `/analisis/`, `/columnistas-invitados/`, `/tribuna/`, `/firmas/` y `/opinión/` con tilde: **cero coincidencias todas**. Mismo criterio que en su momento sacó `signos` de este diccionario, aplicado al revés: no se agrega lo que no tiene beneficio medido, aunque parezca inofensivo.

### Un bug que encontró el propio test

El patrón se escribió primero como `/editoriales?/`, pensando "editorial + `es` opcional". En realidad el `?` cae **sobre la `s` sola**, así que exige "editoriale" y `/editorial/` en singular no matchea. Los 4 casos reales son plurales, por eso la medición dio bien y el error quedaba invisible. Lo agarró el caso que se había agregado por si algún medio usa el singular. Corregido a `/editorial(?:es)?/`, con el comentario en el código para que nadie lo "simplifique" de vuelta.

### Verificado

Cinco mutaciones, cada una con su guarda propia: desanclar (falla 2 tests), sacar `/editorial(?:es)?/`, sacar `/columnistas/`, sacar la rama `-`, y el `editoriales?` mal escrito. **La primera es el punto de todo B4**: la mutación que antes pasaba desapercibida ahora falla.

Efecto en los datos: de 85 a **91** notas clasificadas como opinión, 8 de ellas ya en un cluster. Las otras tres categorías intactas (horóscopo 42, recetas 67, juegos 1).

**Cabo suelto anotado**: el agrupamiento ya hecho **no se recalcula**. Esos 8 clusters siguen conteniendo columnas y el filtro lo aplica la ingesta, no una pasada retroactiva. Como los clusters cierran a las 12 h, se limpia solo con el tiempo.

---

## El avisador podía trabar el pipeline — timeout de SMTP y el cooldown que contaba intentos (20/08/2026)

Hallazgos I1 y M8. **De los cinco de la revisión, el único que no era latente.**

### Por qué este sí estaba vivo

`smtplib.SMTP(host, port)` sin `timeout` usa `socket._GLOBAL_DEFAULT_TIMEOUT`, y `socket.getdefaulttimeout()` es `None`: **bloquea indefinidamente**. Verificado que en este entorno el SMTP está configurado y en uso (`smtp.gmail.com:587`), así que cada alerta que manda el motor sale por un socket que puede colgarse para siempre. B1, B2 y B3 necesitaban una entrada rara para dispararse; a este le alcanza una condición de red.

El radio es grande porque `enviar_alerta` se llama **desde el hilo del job** en cinco lugares (`_correr_paso`, el canario de duración, el fallo de feed, el `robots.txt` ilegible y la extracción fallida):

1. El hilo del job queda bloqueado para siempre.
2. Con `max_instances=1`, el scheduler **saltea todos los ciclos siguientes**.
3. Cada salteo dispara `_avisar_corrida_perdida`, que abre un hilo daemon que también se cuelga.
4. Sin más recuperación que reiniciar el proceso.

La etapa 4 había identificado el riesgo ("sin timeout explícito") pero mitigó solo el event loop, moviendo el aviso del listener a un hilo daemon. **La vía que quedó sin cubrir era peor que la cubierta**: una API congelada se nota; un pipeline trabado, no. El avisador, cuyo trabajo es que los fallos se vean, pasaba a ser lo que mata la corrida en silencio.

`SMTP_TIMEOUT_SEGUNDOS = 10.0`. Un `timeout` en el constructor cubre toda la sesión porque `starttls`, `login` y `send_message` heredan el del socket. **Acota cada operación y no la sesión entera**, así que el peor caso son ~4× esos segundos; queda escrito en el comentario para no dejar creer que son 10. Contra un ciclo de 900 s sigue siendo chico, y Gmail responde en 1-3 s.

### M8: el cooldown gastaba intentos en vez de entregas

`_corresponde_avisar` estampaba el timestamp **antes** de intentar el envío. Un envío fallido se comía la ventana igual: los siguientes 60 minutos quedaban mudos **sin haber entregado nada**. Con el timeout puesto los fallos pasan a ser rápidos y frecuentes, así que el problema se vuelve más visible.

El cooldown existe **para no inundar la casilla de mails** —lo dice su propio comentario en `config.py`— y un envío que falla no manda ningún mail, así que no tiene por qué gastar ese presupuesto. La función pasó a llamarse `_en_cooldown` y **solo consulta**; quien estampa es `enviar_alerta`, y únicamente cuando el mail salió.

Su costo, asumido: contra un SMTP roto se reintenta cada ciclo en vez de cada hora. Acotado por el timeout, son ~40 s en el peor caso sobre 900, y el canario de duración avisaría si se pusiera feo.

**Efecto secundario que vale más de lo esperado**: cuando no hay SMTP configurado, el `logger.error` lleva el cuerpo entero de la alerta — **ese log *es* la entrega**. Antes el cooldown lo silenciaba una hora, o sea que se perdía la alerta en vez de ahorrarse un mail. Ahora no, y hay un test que lo fija.

### Verificado, con una limitación que conviene saber

Mutación: sacar el timeout falla `test_se_le_pasa_un_timeout_acotado`; volver a estampar antes del envío falla dos tests.

**El test de I1 es una aserción de contrato, no de comportamiento**: no se puede colgar un socket real en un unit test, así que verifica que se pase un `timeout` acotado, no que un cuelgue se corte. Es más débil que las guardas de B1-B4 y queda dicho en su propio docstring para que nadie lo lea como equivalente.

---

## Corrida de validación con los cinco arreglos puestos — el pipeline paso por paso (20/08/2026)

Corrida completa contra la base real y **contra el receptor real del back-end**, en el mismo orden que `main._ciclo`, midiendo cada paso por separado. Medido con `time.monotonic()` y no con el reloj de pared, que es el instrumento correcto para duraciones (ver M5 de la revisión, todavía pendiente en `main.py`).

### Los tiempos

| Paso | Segundos | % de la corrida | % del ciclo |
|---|---:|---:|---:|
| Ingesta | 71,18 | 17,2% | 7,91% |
| Vectorización | 10,28 | 2,5% | 1,14% |
| Cierre de clusters | 0,08 | 0,0% | 0,01% |
| Agrupamiento | 2,60 | 0,6% | 0,29% |
| Fusión de clusters | 0,08 | 0,0% | 0,01% |
| **Síntesis** | **320,95** | **77,6%** | **35,66%** |
| Entrega al back-end | 8,36 | 2,0% | 0,93% |
| **TOTAL** | **413,60** | 100% | **45,96%** |

**Es una corrida de acumulación, no de régimen** — la distinción importa porque confundirlas ya llevó a una conclusión equivocada antes. Entraron **310 noticias nuevas** de golpe porque el motor llevaba horas sin correr; en un ciclo normal de 15 minutos entran unas pocas por medio.

### Lo que confirma y lo que cambia respecto del baseline de la etapa 4

La etapa 4 midió 206,58 s con un backlog de 24 ángulos (23,0% del ciclo). Esta dio **413,60 s (46,0%)**, el doble, y las dos razones son claras:

- **La síntesis sigue siendo el grueso**: 77,6% de la corrida, 37 clusters sintetizados a **8,7 s cada uno** — consistente con los ~8 s por ángulo de la etapa 4. El costo lo fija la cantidad de material, no el intervalo.
- **La ingesta pasó de 4-7 s a 71,18 s, y el 90% de eso es Perfil.** Sus 47 artículos por extracción tardaron ~64 s; los otros seis medios juntos, ~7 s. Es la confirmación en producción de que **el costo variable por artículo extraído domina la ingesta**, tal como quedó documentado en `tech_stack.md`, punto 12.

### El dato que conviene mirar

**La corrida usó el 46% del ciclo y el canario dispara al 50%.** No sonó, pero por poco. Proyección directa: sumar **un solo medio más con extracción** del tamaño de Perfil agrega ~64 s y lleva la corrida a ~478 s, o sea **53%: el canario suena**.

Eso no es una falla, es el instrumento haciendo su trabajo — y es exactamente el aviso que la etapa 4 construyó para no depender de que alguien se acuerde de mirar. Confirma también que el disparador documentado para paralelizar la extracción (**pasar los ~10 medios**) está bien puesto, quizá incluso holgado.

### Que los cinco arreglos no rompieron nada

| | |
|---|---|
| Warnings en toda la corrida | **2**, ninguno un problema: uno es la validación anti-alucinación descartando el enfoque de un medio ajeno, el otro es un aviso cosmético de HuggingFace |
| Alertas SMTP disparadas | **0** |
| `url_invalida` (B2) | **0** en los 7 medios — la validación nueva no rechazó nada, igual que sobre las 4.532 históricas |
| Extracción de Perfil (B1/B3) | **47 de 47**, cero fallos; `robots.txt` releído una vez por corrida |
| Entrega al back-end | **40 de 40 aceptadas**, 0 rechazadas, 0 fallidas |

### El filtro de opinión en producción (B4)

Sobre las 4.842 noticias que quedaron en la base: **99 clasificadas como opinión** (El Cronista 59, La Nación 21, TN 12, Perfil 7), más recetas 70, horóscopo 44 y juegos 1.

**91 de esas 99 (92%) quedaron fuera del agrupamiento**, que es el filtro haciendo su trabajo. Las 8 restantes son las que ya estaban en un cluster desde antes de que el patrón existiera: el filtro lo aplica la ingesta y no hay pasada retroactiva, así que se limpian solas cuando esos clusters cierren a las 12 h.

---

## Backlog punto 2, etapa 1 — desacoplar el motor de IA (20/08/2026)

Hasta acá `synthesis.py` hablaba Gemini directo, y eso obligaba a **todo el que despliegue el motor a usar Gemini**, con su cuenta y sus términos. El objetivo es que cada operador use el modelo que paga, aquel donde tiene créditos, o uno local que no manda nada afuera.

### Los dos caminos que se evaluaron, y por qué el vertical

Con la arquitectura ya acordada, quedaba **en qué orden construirla**:

| | Cómo | Veredicto |
|---|---|---|
| **Por capas** | Refactor primero (extraer el contrato), configuración y endpoints al final | ❌ El objetivo —que un operador sume su modelo sin tocar archivos— llegaba recién en la etapa 3 o 4, y lo más incierto se validaba último |
| **Vertical** ✅ | Una rebanada completa de punta a punta para **un** adaptador, con Gemini intacto como red de seguridad | Elegido |

**Lo que decidió**: el adaptador genérico se puede probar contra el **endpoint compatible de Gemini con la key que ya tenemos** (verificado el 20/08: existe y acepta nuestro esquema con `$ref` y `anyOf` sin aplanar). O sea que lo más incierto del diseño se valida el primer día, sin cuenta nueva y sin gasto. El camino por capas hacía primero lo fácil.

### Las decisiones de diseño

**Adaptadores por protocolo, no por proveedor.** `openai_compatible` no es un proveedor: es el estándar de hecho, y cubre OpenAI, Azure, OpenRouter, Groq, Together, DeepSeek, Mistral, xAI, vLLM, LM Studio, Ollama y Gemini. **Agregar un modelo es insertar una fila**, no escribir un `.py`. Los otros dos adaptadores del enum (`gemini`, `anthropic`) no agregan cobertura sino **fidelidad**: acceso a lo nativo de cada uno.

**El enum es cerrado.** La tentación al leer "que cada uno use el modelo que quiera" es guardar en la base la ruta de import del adaptador. Eso es ejecución remota de código, y la API no tiene autenticación. Un proveedor que no hable ninguno de los tres protocolos se resuelve con un **gateway** adelante (LiteLLM, OpenRouter), no con código nuestro.

**Anthropic se ganó su adaptador nativo, y no por preferencia.** Su capa de compatibilidad existe (`/v1/chat/completions` responde 401, no 404) pero **`response_format` está documentado como "Ignored"**, y `strict` en tools también. Su propia página dice que la capa "is not considered a long-term or production-ready solution". Como toda la síntesis se apoya en la salida estructurada, un operador con créditos de Anthropic no puede usarlos por ahí.

**El alta sondea, no registra.** `POST /modelos` manda un pedido mínimo y verifica que **vuelva la forma pedida**, no que el endpoint conteste. El caso Anthropic es la prueba de por qué: responde 200, devuelve texto correcto y descarta el esquema en silencio. Un alta que solo probara conectividad habría aceptado ese modelo y el fallo habría aparecido en la síntesis, cada 15 minutos, en el paso más caro del pipeline.

**El modo de estructura lo descubre el sondeo.** El operador no declara si su proveedor acepta `response_format` o solo tool-calling: se prueba uno, y si no da la forma se prueba el otro. Nadie tiene por qué saber ese detalle, y la documentación del proveedor puede mentirle.

**La red de seguridad es el camino por defecto.** Sin filas activas en `modelo_ia`, la síntesis va por `_llamar_gemini`, que quedó **idéntico** —verificado por comparación de AST contra `HEAD`, no de palabra—. Una base que no configuró nada se comporta exactamente como antes de que la tabla existiera. Y conservarlo no es nostalgia: es el único acceso a `thinking_config`, que es la palanca de costo sobre el 77% de la corrida, y **borrarlo sería tirar la forma barata de medir cuánto vale esa palanca**.

**`Sintesis.modelo_usado` desde el día uno.** Guarda el nombre que le puso el operador —no el id del modelo, porque puede haber dos filas del mismo modelo con distinta cuenta— y se actualiza en la re-síntesis, porque describe quién escribió el texto que está ahí ahora. El valor está en la **serie histórica**: el día que se quiera comparar proveedores tiene que ser una query, no un proyecto de medición.

### La revisión, y lo que cambió por ella

Una revisión con subagente encontró **cuatro bloqueantes reales**, dos de ellos de seguridad. Vale dejarlos escritos porque el aprendizaje no es el bug sino el patrón:

- **La cadena de exfiltración seguía abierta.** El prefijo de `api_key_env` impedía nombrar variables ajenas, pero `GET /modelos` **publicaba el nombre de la variable y el `base_url`**: con eso, cualquiera daba de alta un modelo apuntando a su servidor y el motor le entregaba la key del operador en el sondeo mismo. El comentario del código afirmaba que el prefijo "tapa una vía de exfiltración" — le atribuía más de lo que hace. Ahora ninguna respuesta publica esos dos campos, y el comentario dice **exactamente** qué protege y qué no.
- **El SSRF no era ciego.** El error del proveedor se reflejaba crudo en el 422, así que apuntando a un servicio interno la respuesta traía su cuerpo. Ahora solo viaja `error.message` cuando la respuesta tiene la forma de error de OpenAI; lo demás va al log.
- **El `_RESOLVER` no evitaba el N+1 que decía evitar.** `expire_on_commit` está en `True` y `sintetizar_cluster` commitea por cluster, así que el `ModeloIA` se expiraba y se recargaba: **una query por cluster**, medido. Es la **misma trampa que este repo ya tenía documentada** quince líneas más arriba, en `descartar_vencidos_sin_sintetizar`. Se cierra con `session.expunge`.
- **`ProveedorNoConfigurado` se escapaba del manejo de errores**: no se traducía ni estaba excluido del retry, así que una key faltante costaba 3 intentos con espera creciente por cluster — entre 2 y 4 minutos de sleeps por corrida. Ahora se traduce a `SintesisSinConfigurar`, igual que el camino histórico, y se sumó `AdaptadorNoImplementado` por el mismo motivo: no se arreglan reintentando.

### Sobre los tests, que es lo más incómodo

De 24 tests, **cinco mutaciones sobrevivieron**. Una en particular: `test_un_adaptador_inexistente_no_se_reporta_como_falta_de_esquema`, escrito con un docstring de cinco líneas para guardar un bug encontrado ese mismo día, **no lo guardaba** — el mensaje genérico incluía el detalle de cada intento, así que el `match` pasaba igual.

El patrón vale más que el caso: **un test que afirma con `match=` sobre un mensaje compuesto puede pasar por la parte equivocada del mensaje**. Ahora afirma además que el mensaje genérico *no* aparece y que no se salió a la red.

La segunda lección es del mismo tipo. El test del N+1 hacía su propio `expunge` y contaba queries: probaba que `expunge` funciona —cosa de SQLAlchemy— y no que `sintetizar_pendientes` lo llame. Sacando el `expunge` del código, pasaba igual. Ahora corre `sintetizar_pendientes` de verdad y cuenta lecturas de `modelo_ia`: 1 con el arreglo, 3 sin él.

### Verificado

**419 tests** (369 → 419), `ruff` limpio, `alembic check` sin drift, round-trip `upgrade → downgrade → upgrade` de la migración (que incluye el borrado manual de los tipos ENUM, que Alembic no autogenera y sin el cual un re-upgrade falla).

Las siete mutaciones que sobrevivían ahora fallan, cada una en su propia guarda. Punta a punta contra el endpoint compatible de Gemini: alta real en 1,6 s con el modo detectado solo, y rechazo correcto de adaptador inexistente, variable prohibida, modelo inexistente y duplicado.

---

## La primera prueba con un proveedor de verdad — Groq / qwen3.6-27b (20/08/2026)

Primer modelo de un tercero dado de alta con el sistema de la etapa 1. Se eligió `qwen/qwen3.6-27b` en Groq, que expone API compatible con OpenAI.

### Lo que funcionó, que era lo que se estaba probando

- **El alta no necesitó una línea de código.** Una fila con `base_url`, el id del modelo y el nombre de la variable de entorno.
- **El sondeo detectó solo el mecanismo correcto.** La documentación de Groq declara *"JSON Object Mode"*, que **no es JSON Schema**: `response_format` no devolvió la forma, el sondeo cayó a `tools` y ahí sí. El operador no declaró nada. Tardó 7,4 s porque hizo los dos intentos, y ese costo se paga una sola vez.
  Ese mecanismo se había construido por el caso Anthropic, **sin ningún proveedor real donde probarlo**. Groq fue su primera comprobación.
- **El saneado de errores conservó lo accionable.** Groq devuelve errores con forma de OpenAI, así que llegó *"Limit 8000, Requested 23005"* y no un `HTTP 413` pelado. Sin eso el diagnóstico habría sido a ciegas.

### Por qué el modelo no sirve igual

El tier gratuito de Groq limita a **8.000 tokens por minuto**, y los prompts de síntesis miden entre 4.500 y 23.000 tokens. Medido sobre tres clusters reales:

| Cluster | Notas | Prompt | Gemini | Groq |
|---|---:|---:|---|---|
| 190 | 20 | ~23.000 tok | 6,8 s · 2 ángulos | ❌ 413 |
| 458 | 9 | ~16.000 tok | 3,3 s · 1 ángulo | ❌ 413 |
| 464 | 3 | ~4.500 tok | 3,2 s · 2 ángulos | ❌ 400 en el tool call |

Gemini: 3 de 3, 4,4 s promedio. Groq: 0 de 3. **No es un límite de nuestro código sino de capacidad del tier**: con `max_tokens=4000` el pedido más chico igual dio 413, porque el límite cuenta prompt + esquema + salida reservada (10.063 tokens).

El 400 del cluster 464 —el único que entraba en el límite— quedó **sin explicar**. Puede ser complejidad del esquema con ese modelo, pero no se verificó y cada intento cuesta un minuto de espera por el TPM. No se atribuye sin comprobarlo.

### El hallazgo sobre nuestro propio diseño

**El sondeo aprueba con un prompt de juguete, y eso no garantiza que el trabajo real entre.** Pide "un solo elemento, textos de una o dos palabras" —unos cientos de tokens— y pasó sin problema; el prompt real es cincuenta veces más grande y no entra en el límite del proveedor.

O sea que **el sondeo valida el mecanismo, no la capacidad**. Es una distinción que hoy no está dicha en ningún lado, y se descubrió de la peor forma posible: dando de alta un modelo que parecía servir.

Queda anotado como mejora: que el sondeo estime el tamaño de un prompt real y avise cuando los límites declarados por el proveedor no lo admitan. Mientras tanto, la garantía que da el alta es más chica de lo que parece y conviene decirlo.

---

## Etapa 2 del punto 2: la credencial única y el adaptador nativo de Gemini (21/08/2026)

Dos decisiones grandes, y la primera **contradice lo que la etapa 1 había construido una semana antes**. Vale dejar escrito el razonamiento completo porque el cambio se ve como un retroceso si solo se mira el diff.

### Decisión 1 — Una sola variable de entorno para la credencial

La etapa 1 diseñó `api_key_env`: cada fila guardaba el **nombre** de su variable (`MODELO_API_KEY_GEMINI`, `MODELO_API_KEY_GROQ`), lo que permitía tener varios proveedores configurados a la vez.

**Se cambió a una sola variable de nombre fijo, `MODELO_API_KEY`**, y el alta ya no acepta el campo.

Lo que hizo caer el diseño anterior fue notar en qué se apoyaba. El argumento para varias variables era la **cadena de fallback** —si un proveedor pega contra su rate limit, caer al siguiente—, que necesita las dos credenciales vivas en el mismo instante. Pero esa cadena es el punto 6 del backlog: **no está construida**, y `prioridad` hoy solo desempata. Se estaba pagando complejidad permanente por una función que no existe.

Y el costo era real: el `.env` crecía una línea por cada proveedor que alguien probara, para siempre.

**El usuario aportó el argumento que cerró la discusión**: la cadena de fallback no termina en "si falla, probá el siguiente". Para que sirva de verdad hay que decidir *cuánto* mandarle a cada proveedor según los créditos que le queden, y eso es lógica nueva de peso. Con eso, multimodelo pasó a ser un punto propio del backlog en vez de un requisito implícito de éste.

**Lo que se conservó, y por qué.** La columna `api_key_env` sigue existiendo con default `VARIABLE_UNICA`, y `leer_api_key` sigue aceptando la forma con sufijo. Diferir multimodelo así sale gratis: el día que se implemente es exponer un campo, no rehacer la validación ni migrar la tabla. Y las instancias que ya tengan filas con nombres sufijados **siguen funcionando sin tocar nada**.

#### La consecuencia que hubo que atacar

Con nombres por proveedor, que la variable existiera era evidencia útil: si activabas un modelo de Groq sin haber definido su variable, la ausencia lo delataba. `PATCH ?activo=true` se apoyaba en eso y **solo miraba el entorno**, con el argumento explícito de que no valía gastar una llamada al proveedor para prender un interruptor.

**Con una credencial única ese razonamiento se da vuelta.** `MODELO_API_KEY` existe siempre, tenga adentro la key del proveedor que tenga. El chequeo pasaba igual cuando el operador cambiaba de modelo y se olvidaba de cambiar el valor, y el 401 aparecía quince minutos después, en el paso más caro del pipeline y sin nadie mirando.

Así que **activar pasó a sondear de verdad** contra el proveedor, con el timeout corto del sondeo. Cuesta una llamada y a cambio el error sale con el botón todavía apretado. **Apagar no sondea**, y eso es deliberado: apagar es la marcha atrás y tiene que funcionar justamente cuando el proveedor está caído.

#### Un cambio menor con el mismo criterio

`AltaModelo` pasó a `extra="forbid"`. Sin eso, mandar `api_key_env` en el alta se descartaba **en silencio** y quien lo mandó se quedaba creyendo que el motor iba a leer la variable que él eligió. En un endpoint donde el campo de más es justamente el que alguien usaría para desviar la credencial, el silencio es la peor respuesta. Por el mismo motivo, el adaptador nativo **rechaza** `base_url` en vez de ignorarlo.

### Decisión 2 — Dónde viven las palancas específicas de un proveedor

El adaptador nativo existe para dar acceso a `thinking_config`, que no tiene equivalente fuera de Gemini. Eso obligaba a decidir dónde se configura.

Se evaluaron dos caminos:

1. **Columna `thinking_level` propia.** Tipada y obvia, pero es una columna que un solo adaptador lee. Con Anthropic entrando en la etapa 3 y su `thinking.budget_tokens`, serían dos columnas muertas para todos los demás: la tabla empieza a crecer por proveedor, que es justo lo que el enum cerrado había evitado.
2. **Columna `opciones` JSON con allowlist por adaptador.** ✅ **Elegido.** Un solo cambio de esquema para siempre.

**La allowlist no es prolijidad, es la llave del bolsillo.** Sin ella, `opciones` sería un camino directo desde un endpoint sin autenticación hasta el cuerpo del request que sale hacia el proveedor. Cada adaptador declara `OPCIONES_ACEPTADAS` y se valida **al construirlo** —o sea antes de guardar nada—, para que una opción inválida sea un 422 en el alta y no una síntesis que falla cada 15 minutos.

El adaptador compatible declara **cero opciones**, y es una postura: habla con decenas de proveedores distintos, así que una palanca que funcione en uno no tiene por qué existir en otro. Aceptarla sería prometer un efecto que depende de contra quién apunte el `base_url` de esa fila.

### Un mecanismo que no se puede probar no se cuenta como intento

`sondear` probaba siempre `response_format` y después `tools`. El nativo de Gemini tiene **un solo mecanismo**, así que probarle el segundo daba un pedido idéntico al primero, fallaba igual, y el mensaje final acusaba al proveedor de no respetar el esquema *por ninguno de los dos mecanismos* — una conclusión falsa sobre uno que nunca se intentó de verdad.

Cada adaptador declara ahora `MODOS_SOPORTADOS` y el sondeo solo recorre esos.

### El cliente deja de ser global

El camino histórico cachea un `genai.Client` en una variable de módulo. Eso significa que **la credencial queda congelada en el proceso**: cambiar la key no tiene efecto hasta reiniciar.

El adaptador lo arma por instancia. Es lo que hace que cambiar de proveedor no necesite un redeploy — junto con que `_del_entorno` relee el `.env` en cada llamada.

### La medición que la etapa 2 existía para hacer (21/08/2026)

Los tres caminos a Gemini sobre **el mismo prompt real** — cluster 485, 13 noticias de 4 medios, 77.429 caracteres, 18.447 tokens de entrada. Tres rondas cada uno.

| Camino | Éxitos | Mediana | Salida | Razonamiento |
|---|---|---:|---:|---:|
| histórico | 3/3 | 8,98 s | 2.099 | 0 |
| nativo | 2/3 | 8,05 s | 1.712 | 0 |
| nativo con `thinking_level=HIGH` | 3/3 | 28,38 s | 2.267 | 6.267 |
| compatible (capa OpenAI de Gemini) | 3/3 | 8,55 s | 2.229 | 0 |

**Los tres caminos son equivalentes.** Misma entrada exacta, 4 ángulos en las doce corridas, entre 9 y 14 puntos clave, y en la mayoría hasta el mismo título de encabezado. La latencia no se distingue: 8,05 · 8,55 · 8,98 s.

**La palanca de razonamiento funciona y es cara, ahora medido de punta a punta.** De 0 tokens con `LOW` a entre 4.204 y 8.579 con `HIGH`, y la latencia se triplica. Eso confirma el motivo de existir del adaptador nativo: la capa compatible no expone `thinking_config`, así que usar Gemini por ahí es renunciar a la única palanca de costo del pipeline.

#### El hallazgo que no se estaba buscando

Una segunda corrida, de seis rondas, para comprobar si la varianza salía de que el adaptador arma un cliente nuevo por llamada mientras el histórico cachea uno global:

| Caso | Mediana | Peor |
|---|---:|---:|
| histórico (cliente cacheado) | 7,41 s | **486,33 s** |
| nativo (cliente nuevo) | 9,50 s | 9,94 s |
| nativo (cliente reusado) | 7,53 s | 8,56 s |

**El camino histórico tardó 486 segundos en una ronda — ocho minutos — y no falló.** No falló porque `_llamar_gemini` **no tiene timeout configurado**: usa el del socket. Eso no es una anécdota estadística, es una propiedad del código. Y como `llamar_modelo` reintenta hasta tres veces, el techo de un solo cluster es tres veces eso. El ciclo del scheduler es de 15 minutos.

O sea que la comparación se dio vuelta: **el adaptador nuevo no hereda ese riesgo, lo acota.** Su peor caso es el timeout de 120 s, que fue exactamente lo que hizo en la corrida anterior cuando Gemini se colgó (`DEADLINE_EXCEEDED` a los 119,22 s). Lo que parecía una desventaja del adaptador —un fallo donde el histórico "no fallaba"— era el adaptador haciendo lo correcto.

Esto refuerza la etapa 4: retirar el camino histórico no es solo sacar duplicación, es sacar la única llamada sin límite de tiempo del pipeline.

#### Lo que sí costó armar el cliente por llamada

**~2 segundos por llamada** (9,50 s contra 7,53 s de mediana), que es el handshake TLS que el cliente cacheado se ahorra. Con 26 clusters en una corrida son unos 50 segundos, alrededor del 12% del costo de la síntesis medido en la etapa 4.

No se corrigió en esta etapa. La salida no es volver al cliente global —eso es lo que congela la credencial en el proceso— sino **resolver el proveedor una vez por corrida y pasarlo**, igual que ya se hace con el `ModeloIA` y por el mismo motivo. Queda anotado con su medición.

---

## Etapa 4 del punto 2: se retira el camino histórico (21/08/2026)

El camino histórico —`_llamar_gemini` + `get_cliente` + `_cliente`, unas 60 líneas que hablaban Gemini directo leyendo `settings.GEMINI_*`— se borró. Con él se fueron `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TEMPERATURA` y `GEMINI_THINKING_LEVEL` de `Settings` y del `.env.example`.

### Por qué ahora, y no era solo higiene

El motivo original era la duplicación. El que lo volvió prioritario lo encontró la medición de la etapa 2: **`_llamar_gemini` no tenía timeout**, y en seis rondas tardó 486 segundos en una sin fallar. Era la única llamada del pipeline sin techo, y `llamar_modelo` la reintenta hasta tres veces sobre un ciclo de 15 minutos.

Todo lo que queda pasa por un adaptador, y todo adaptador tiene timeout.

Vale nombrar el otro motivo, que no es de rendimiento: mientras existiera, **el motor tenía un proveedor privilegiado escondido en el código**. Un despliegue que no configuraba nada terminaba mandándole los cuerpos de los artículos a Google sin haberlo elegido. Eso es exactamente lo que el punto 2 venía a sacar, así que dejarlo habría sido cerrar el punto sin cumplirlo.

### La decisión: qué pasa sin ninguna fila activa

Se evaluaron dos caminos y **el usuario eligió el segundo**, con un argumento que el planteo original no tenía: si toda síntesis queda atribuida a un modelo real, **se pueden comparar las síntesis que entregó cada uno**. Con una fila implícita, las del default se habrían agrupado bajo una etiqueta inventada.

1. **La fila implícita.** Sin fila activa, se arma un `ModeloIA` en memoria desde `settings` y se pasa por el adaptador nativo. Cero acción del operador, pero `settings.GEMINI_*` quedaba vivo para siempre y el default oculto seguía ahí.
2. **Configurar es obligatorio.** ✅ Sin fila activa la síntesis **no corre** y se dice con todas las letras. Un solo lugar donde se configura el modelo: la tabla.

Lo que hace seguro al (2) es la **migración de datos** `5f80e67d5404`, que traduce la configuración de entorno de cada despliegue a la fila que ahora la representa. No inventa nada: lee las mismas variables que leía el código que se borró.

> ⚠️ Esta primera versión insertaba la fila **activa** cuando no hubiera otra activa, y eso resultó estar mal: la revisión de más abajo explica por qué y cómo quedó. La fila entra apagada.

Dos guardas, cada una por un caso distinto. Si ya existe el nombre, no hace nada (la migración se corrió antes, o alguien creó la fila a mano). Y **si ya hay una fila activa, la inserta apagada**: ese despliegue ya eligió su modelo y no estaba usando el camino histórico, así que activarle una de Gemini le cambiaría el proveedor por debajo.

**La credencial es la única acción manual**, y está amortiguada: `leer_api_key` cae a `GEMINI_API_KEY` cuando `MODELO_API_KEY` no está, **avisando en cada lectura**. Es deuda con fecha, no una segunda forma válida — un fallback silencioso es indistinguible de una configuración correcta, y entonces nadie migra nunca.

### Dos detalles que costaron un intento

- **`bulk_insert` no sirve contra un ENUM de Postgres.** `adaptador` y `modo_estructura` son tipos ENUM, y una tabla declarada al vuelo con `sa.String` produce un INSERT que falla con *"column is of type adaptador but expression is of type character varying"*. La migración usa SQL con casts explícitos. De paso quedó verificado contra `pg_enum` que las etiquetas guardadas son los **nombres** del enum de Python (`GEMINI`), no sus valores (`gemini`).
- **Sin modelo, la corrida corta arriba y no cluster por cluster.** Dejar que cada uno lo descubriera daba 26 excepciones con 26 tracebacks para una sola causa, todas contadas como "fallidas" — que sugiere un problema con los clusters cuando el problema es que falta configurar el motor. `sintetizar_pendientes` devuelve `sin_modelo: True` y no procesa nada.

### Lo que cambió en el vocabulario

`GET /modelos` y `PATCH` devolvían `"(Gemini, camino histórico)"` cuando no había fila activa. Ahora devuelven `"(ninguno activo — la síntesis no corre)"`: **es un estado de alarma, no un default**, y el texto tiene que impedir que se lo lea como "anda solo".

### Lo que la etapa 3 ya no bloquea

Anthropic es usable hoy por el adaptador compatible en modo `tools`, que es el que el sondeo detecta solo. Un adaptador nativo agregaría su palanca de razonamiento, y esa ahora entra por `opciones` sin tocar el esquema. La etapa 3 quedó abierta pero dejó de ser un prerrequisito de nada.

### La revisión de la etapa 4, y lo que encontró (21/08/2026)

Antes de commitear se pasó un revisor sobre las etapas 2 y 4. Encontró siete cosas, y **dos eran agujeros en razonamientos que este mismo documento defiende**. Vale dejarlas escritas con esa forma, porque el patrón —una guarda correcta aplicada a un caso y ciega al otro— es el que hay que aprender a ver.

#### La migración le devolvía a Gemini el privilegio que la etapa 4 vino a sacar

La versión original insertaba la fila **activa cuando no hubiera ninguna otra activa**, y el razonamiento estaba escrito acá arriba: *"ese despliegue ya eligió su modelo"*.

Ese razonamiento vale para el despliegue que **actualiza**. Pero la migración también corre en el que **nace**, y ahí la condición se cumple siempre. Consecuencia, verificada:

1. Instalación limpia. `init_db()` exige `alembic upgrade head`, así que la fila existe **antes** del primer `POST /modelos`.
2. El operador da de alta su modelo con `activar=true`. Recibe `200` y `"activo": true`.
3. Las dos filas empatan en `prioridad` —el default es 100 en la columna y en `AltaModelo`— y **desempata el `id`**, o sea gana la de la migración.
4. Cada 15 minutos los cuerpos de los artículos salen hacia Google, con una credencial que ni siquiera es de Google.

O sea, exactamente *"un despliegue que no configuraba nada terminaba mandándole los cuerpos a Google sin haberlo elegido"*, que es la frase con la que se justificó la etapa 4 — ahora con una fila que lo hacía parecer deliberado.

**La corrección tiene tres partes, y la regla que queda no depende de adivinar en qué caso estamos:**

- **Ninguna migración elige proveedor**: la fila entra siempre apagada. El costo asumido es que quien actualiza tiene que prenderla; a cambio nadie sintetiza contra un proveedor que no eligió.
- **Prender un modelo apaga a los demás.** No se pierde nada: desde la etapa 2 la credencial es una sola, así que dos proveedores prendidos es un estado que no se puede usar. De paso muere el desempate silencioso por `id`.
- **`POST /modelos` devuelve `en_uso`**, que era el único de los tres endpoints que no lo hacía — o sea el único punto donde el operador podía quedarse creyendo que el motor usaba su modelo.

#### El saneo de errores cubría la rama 4xx y dejaba abierta la 200

`_mensaje_de_error` está construido con cuidado para no convertir el SSRF en lectura de servicios internos, **pero solo corre cuando el status no es 200**. Si el destino contesta 200, el cuerpo pasaba por `_leer`, volvía crudo, y `sondear` lo pegaba entero —dos veces, una por modo— en el 422 de un endpoint sin autenticación.

Alcanza con que el destino hable formato OpenAI, que es justo lo que hace un LiteLLM o un vLLM en la red de al lado.

Ahora el cuerpo va **solo al log** y la respuesta dice *"respondió 200 pero sin la forma que pide el esquema"*. Se evaluó devolver una caracterización estructural —qué claves traía— y se descartó: los nombres de las claves también son información del servicio interno, y el operador que depura su propio modelo tiene el log en la misma máquina.

**Y el test que supuestamente guardaba esto usaba `httpx.Response(403, ...)`**: la rama que sí estaba tapada. Tercera aparición del mismo modo de fallo en este repo.

#### Un 200 que no es un chat-completion salía como HTTP 500

`respuesta.json()` estaba fuera del `try`, así que `JSONDecodeError` —que no hereda de `ErrorDeProveedor`— se escapaba del sondeo y del alta.

El caso no es adversarial: es **el tipeo más probable de todo este backlog**, `base_url` sin el `/v1` final. Ollama contesta 200 con `"Ollama is running"` en texto plano.

Se eligió chequear el `content-type` antes de parsear —laxo, alcanza con que diga `json`— para poder dar el mensaje que **diagnostica**: *"no es una API compatible con OpenAI; lo más común es que a `base_url` le falte el sufijo del proveedor, por ejemplo `/v1`"*. Debajo quedó igual la red de `try`, más las guardas de tipo para un JSON que no es objeto, un `choices` que no es lista y un `choice` que no es objeto — todos daban `AttributeError` y salían como 500.

#### La migración escribía sin pasar por la puerta que el endpoint sí custodia

Dos lecturas del entorno sin validar. `float(GEMINI_TEMPERATURA)` **abortaba el upgrade entero** ante cualquier tipeo — y `dotenv_values` no pela comentarios de fin de línea sin comillas, así que `0.2 # baja a propósito` bastaba. Y `GEMINI_THINKING_LEVEL` se insertaba sin contrastar, produciendo una fila que el adaptador rechaza pero recién en la síntesis, por la rama "esto se arregla solo": tres intentos con backoff **por cluster**.

Ahora las dos caen al default avisando. Lo que hace aceptable el fallback hoy y no lo hubiera hecho antes: la configuración vive en una fila que se mira con `GET /modelos`, así que un valor que no se respetó queda a la vista en vez de perderse en el entorno.

#### El `downgrade` no miraba los campos que su propio docstring prometía

Prometía respetar la fila *"si el operador la editó — otro modelo, otra temperatura"* y filtraba por `adaptador`, `base_url` y `max_tokens`, que no discriminan nada: en el adaptador de Gemini `base_url` se rechaza en el constructor, así que **siempre** es NULL. Los dos casos que el texto nombraba pasaban el filtro y se borraban igual. Ahora el `WHERE` mira `modelo`, `temperatura`, `opciones` y `activo`.

#### Sin modelo, la alerta de caducados mandaba al lugar equivocado

El barrido corría **antes** del corte por `sin_modelo`, así que una instalación recién migrada iba marcando clusters como caducados y a las 72 h avisaba *"el plazo quedó corto, subilo"*. La causa no era el plazo. Ahora se corta antes de barrer.

Al moverlo apareció una trampa de SQLAlchemy que vale anotar: **`expunge` sobre un objeto ya expirado lo desprende sin valores**, así que el primer acceso tira `DetachedInstanceError` en vez de recargar. El barrido commitea, y `expire_on_commit` está en `True`. El `expunge` tiene que ir antes de cualquier cosa que commitee, no solo antes del bucle.

### Corrida completa contra el back-end tras la etapa 4 (21/08/2026)

Pipeline entero por los endpoints reales, con el back-end levantado. **16 de 16 entregadas, cero rechazos.**

| Paso | Segundos | % del total | Resultado |
|---|---:|---:|---|
| `/ingest` | 71,08 | 37,5% | 36 nuevas de El Cronista, 311 pendientes de vectorizar |
| `/vectorize` | 10,86 | 5,7% | 311/311 |
| `/cluster` | 1,58 | 0,8% | 37 cerrados, 22 clusters nuevos, 0 fusiones |
| `/synthesize` | 102,27 | 54,0% | 17 clusters, 16 ángulos creados, 8 descartados, **0 fallidos** |
| `/deliver` | 3,65 | 1,9% | 16/16, 0 rechazadas |
| **TOTAL** | **189,44** | | **21% del ciclo de 15 min** |

La síntesis sigue siendo el paso más caro (54%), consistente con lo medido en la etapa 4 del punto 1. La corrida anterior con la que se puede comparar usó el 46% del ciclo; ésta el 21%, pero con menos backlog de síntesis, así que **no son comparables directamente**: lo que fija el trabajo de síntesis es cuántos clusters publicables hay, no el código.

#### Lo que esta corrida existía para verificar

- **Sin modelo activo la síntesis no corre y lo dice**: `POST /synthesize` devolvió `sin_modelo: true` en 0,02 s, sin tocar ningún cluster y sin un solo traceback.
- **Activar sondea de verdad**: el `PATCH ?activo=true` tardó **8,33 s** —el costo real de una llamada al proveedor— y devolvió *"responde y respeta el esquema vía `response_format`"*. Ese es el precio de que un 401 aparezca con el botón apretado y no quince minutos después.
- **`GET /modelos` no publica `api_key_env` ni `base_url`**, verificado sobre la respuesta real.
- **Toda síntesis nueva queda atribuida**: las 16 llevan `modelo_usado='gemini-por-defecto'`. Las 296 anteriores mantienen `NULL`, que sigue significando "anteriores a la columna" — no se rellenaron hacia atrás.
- **El fallback de credencial funciona**: `MODELO_API_KEY` no está en ese `.env` y la síntesis corrió igual leyendo `GEMINI_API_KEY`.

#### Lo que la corrida encontró, y que ningún test podía encontrar

**19 avisos idénticos del fallback en una sola corrida** — uno por cluster sintetizado, más el sondeo, más cada `GET /modelos`. A 96 corridas por día son unas **1.800 líneas iguales**.

El argumento original era correcto —un fallback silencioso es indistinguible de una configuración correcta— pero la frecuencia estaba mal: a ese volumen el aviso deja de ser visibilidad y pasa a ser ruido que uno aprende a filtrar, y filtrado no avisa nada.

Pasó a **una vez por proceso**, que conserva lo que importa: aparece en cada arranque, o sea después de cada deploy, que es cuando hay alguien mirando.

Vale anotar el patrón: este defecto no era detectable por tests unitarios —cada llamada era correcta por separado— sino solo mirando el log de una corrida real. Es el mismo tipo de hallazgo que el `_llamar_gemini` sin timeout: emerge del volumen, no de la lógica.

#### Un cabo suelto de la misma familia, encontrado al explicar la corrida

Preguntando por qué la corrida había funcionado sin `MODELO_API_KEY` —la respuesta es el fallback a `GEMINI_API_KEY`— salió a la luz qué pasaría el día que esa vía no esté: **`SintesisSinConfigurar` caía en el `except Exception` genérico** de `sintetizar_pendientes`.

O sea, 17 clusters, 17 tracebacks y 17 "fallidos" para una sola causa. Es exactamente el diagnóstico engañoso que la etapa 4 había corregido para el caso *"no hay ninguna fila activa"*, y que se le había escapado al caso *"la fila está pero no tiene con qué autenticarse"* — que llega por un camino distinto (una excepción desde adentro del bucle, no un chequeo previo).

Ahora corta la corrida y lo dice una sola vez. Los clusters quedan sin marca, así que entran en carrera solos cuando la configuración esté.

`stats` distingue las dos formas de "el motor no está configurado", porque se arreglan distinto: `sin_modelo` es que nadie eligió proveedor, `sin_credencial` es que el elegido no tiene credencial. **Ninguna de las dos cuenta como cluster fallido**, que es el punto.

---

## Se cierra el punto 2: la etapa 3 no se implementa (21/08/2026)

**Anthropic nativo queda como limitación documentada, no como tarea pendiente.** Con eso el punto 2 del backlog cierra.

### Lo primero: el roadmap estaba desactualizado

Decía que un adaptador nativo aportaría *"su palanca de razonamiento (`thinking.budget_tokens`)"*. **Eso ya no existe**: `budget_tokens` está removido en los modelos actuales de Anthropic y devuelve **400**. Lo reemplazaron `thinking: {type: "adaptive"}` y `output_config.effort` (`low` a `max`).

Y hay algo que no sabíamos y que hace al nativo **más** valioso de lo que decíamos: Anthropic tiene **salida estructurada nativa** vía `output_config.format`, o sea JSON válido por construcción, igual que `response_schema` en Gemini. Por la capa de compatibilidad, en cambio, el esquema viaja como `tools` y es una guía, no una garantía.

### La decisión, y el número que la define

| Modelo | Por síntesis | ~20/día | Al mes |
|---|---:|---:|---:|
| Opus 5 | US$0,147 | US$2,95 | **~US$88** |
| Sonnet 5 | US$0,088 | US$1,77 | ~US$53 |
| Haiku 4.5 | US$0,029 | US$0,59 | ~US$18 |

Contra **US$0** del tier gratuito de Gemini que corre hoy. Anthropic no tiene tier gratuito.

Se evaluaron dos caminos: construir el adaptador validándolo con una corrida barata (~US$1,50, que sí es asumible), o cerrar el punto documentando la limitación. **Se eligió el segundo**, y el motivo decisivo no fue el costo de construirlo sino el de **correrlo**: agregar una dependencia y ~250 líneas para un camino que el proyecto no puede pagar es deuda sin contrapartida.

### El supuesto que queda abierto, dicho como supuesto

Está verificado que la capa de compatibilidad de Anthropic **ignora `response_format` en silencio**. Que acepte `tools` —de lo que depende que Anthropic sea usable sin adaptador nativo— **nunca se probó**: no hubo credencial con crédito.

Eso quedó escrito en tres lugares donde alguien lo va a leer antes de tropezarse: el docstring de `ModoEstructura`, el comentario de `Adaptador.ANTHROPIC`, y el mensaje de error de `construir`, que ahora dice qué hacer en lugar de cada adaptador reservado en vez de dar un texto genérico.

Es deliberado no venderlo como hecho: exactamente el error que costó el episodio de Groq fue dar por bueno un mecanismo sin probarlo.

### Dos defectos que aparecieron mientras se documentaba esto

**El placeholder del `.env.example` tapaba el fallback de credencial.** Al renombrar `GEMINI_API_KEY` a `MODELO_API_KEY` en un `.env` real, el valor que quedó fue `tu_api_key_aqui` — que es *truthy*. `leer_api_key` lo devolvía como si fuera una credencial y nunca llegaba a `_heredada`, así que la síntesis habría dado **401 en cada cluster**; y como un 401 es `ErrorDeProveedor`, eso son **tres reintentos con espera creciente por cluster**. El síntoma —"el proveedor rechaza la key"— no se parece en nada a la causa, que es una línea sin completar.

Ahora un valor que empieza con `tu_` cuenta como no configurada y el mensaje lo dice con todas las letras. La comprobación ya existía dentro de `_heredada`; lo que faltaba era aplicarla también en la vía principal.

**Y los tests de credenciales no eran herméticos.** `_del_entorno` hace `dotenv_values(".env")` relativo al directorio actual, así que los tests que no se movían a un temporal leían **el `.env` real del desarrollador**. Se descubrió de la peor forma: cinco tests se rompieron sin que se tocara una línea de código, solo porque el `.env` de la máquina cambió. Ahora la fixture hace `chdir` a un `tmp_path` sin `.env`.

### Se saca el fallback a `GEMINI_API_KEY` (21/08/2026)

La compatibilidad temporal duró lo que tenía que durar: el `.env` del despliegue ya usa `MODELO_API_KEY`, así que `leer_api_key` dejó de mirar el nombre viejo. Con eso se van `VARIABLE_HEREDADA`, `_heredada()`, el flag `_ya_se_aviso` y los cinco tests que lo cubrían.

Sobrevive lo único de ese bloque que no era transitorio: **la comprobación de placeholder**. Nació adentro de `_heredada` y se había extendido a la vía principal el mismo día que se encontró el defecto; ahora es `_es_placeholder`, con `PREFIJO_PLACEHOLDER = "tu_"` como convención declarada del `.env.example`.

Vale anotar que el fallback cumplió su función de manual: existió para que una actualización no cortara la síntesis, avisó hasta que alguien renombró la variable, y se borró apenas dejó de hacer falta. Lo que lo hizo funcionar fue ponerle un aviso — un fallback silencioso habría quedado para siempre porque nadie se habría enterado de que estaba ahí.

**Actualizar el motor ahora exige un renombre en el `.env`**: `GEMINI_API_KEY` → `MODELO_API_KEY`, mismo valor. Está dicho en `.env.example`, `roadmap.md`, `tech_stack.md` y el docstring de la migración `5f80e67d5404`. Si falta, el motor no sintetiza y lo dice; no adivina.

#### Y el aislamiento de los tests se generalizó

El `chdir` a un temporal dejó de ser una fixture de la clase de credenciales y pasó a ser **autouse de todo el archivo**. El problema no era de esos tests en particular: `_del_entorno` lee `dotenv_values(".env")` relativo al directorio actual, así que **cualquier** test del archivo que llegue a esa función lee el `.env` de la máquina. Los dos que sí quieren un `.env` se crean el suyo en su propio `tmp_path`.

---

## Token de operador para la API (punto 10 del backlog, 21/08/2026)

Salió de preguntarse si el punto 3 (alta de medios por el operador) podía arrancar. Resultó que no, y que el motivo estaba escrito en el propio roadmap sin que nadie lo hubiera juntado.

### Una deuda sin acreedor

Tres lugares pedían autenticación y **ninguno la tenía asignada**:

- El punto 3: *"Necesita autenticación y validación del destino desde el diseño, no después"*
- El punto 9: *"Es el mismo problema de fondo que el SSRF del punto 3 y probablemente se resuelvan juntos"*
- El punto 2 y varios comentarios del código: *"hasta que exista auth —punto 9—"*… **y el punto 9 es el mail de alertas**

Y la Fase 5 la dejaba explícitamente afuera: *"rate limiting de la API y autenticación pública no están en esta lista a propósito — son problemas de tráfico público que el proyecto todavía no tiene"*.

**Ese razonamiento era correcto cuando se escribió y dejó de serlo con el punto 2.** Desde que existe `POST /modelos`, la API tiene un endpoint que busca una URL arbitraria a pedido de quien llame **y le entrega una credencial**. Eso no es un problema de volumen: existe con un solo visitante.

**La confusión era de vocabulario.** Lo diferido es autenticación *pública* —usuarios, roles, rate limiting—, y eso sigue diferido con razón. Lo que hacía falta es otra cosa y mucho más chica: un token de operador. Conflatirlas es por qué quedó sin dueño.

### El inventario, que era lo que faltaba

Once endpoints, y **todos son del operador**: el back-end recibe las síntesis por push y no consulta nada, el frontend cuelga del back-end. Nada externo consume esta API, así que protegerla entera no rompe ninguna integración — eso hizo la decisión barata.

Dos riesgos que ninguna nota anterior nombraba:

- **`POST /synthesize` es un ataque financiero.** Cuesta plata por invocación, y el gasto en APIs es el límite duro del proyecto. Cualquiera podía vaciar la cuota.
- **`POST /ingest` es reputacional.** Hace que el motor golpee todos los feeds **con la IP y el User-Agent del operador**, contra medios cuyos términos de uso se revisaron con cuidado. En loop es un mini-DDoS con identidad ajena.

Y una contradicción concreta: **el `docker-compose.yml` ligaba `"8000:8000"`**, o sea `0.0.0.0`. La mitigación documentada decía *"desplegala en una red donde solo llegue el operador"* mientras el archivo que define el despliegue publicaba la API entera — y el pendiente operativo del roadmap es justamente elegir el VPS. Ahora liga a `127.0.0.1`.

### El diseño, y por qué es tan chico

**`API_TOKEN` definido → se exige. Sin definir → la API queda abierta y el motor lo avisa al arrancar.** Una sola regla, sin comportamiento que dependa del entorno.

Se evaluó un tercer modo —abierto en `development`, cerrado en `production`— y **se descartó por decisión del usuario**: el motor es software libre que otros despliegan, así que cómo se expone tiene que ser elección de quien lo corre, no del repo. Un interruptor, de su lado.

Tres decisiones menores que valen escribirse:

- **La puerta se declara en el `app`, no en cada ruta.** Un endpoint que se agregue mañana nace protegido en vez de nacer abierto hasta que alguien se acuerde del decorador. La excepción vive en un solo lugar (`auth.RUTAS_ABIERTAS`).
- **`GET /` queda abierto porque lo llama el `HEALTHCHECK` del Dockerfile** desde adentro del contenedor. Pedirle token lo rompería, o forzaría a meter la credencial en el `Dockerfile`. La documentación también, porque expone la forma y no los datos.
- **`hmac.compare_digest` y no `==`.** La comparación de strings de Python corta en el primer byte distinto, así que el tiempo de respuesta filtra cuántos caracteres acertó quien prueba. Como ningún test de comportamiento puede distinguir las dos —dan el mismo 401—, la guarda mira el código fuente.

### Verificado, no supuesto

Contra un servidor real: `/` y `/docs` responden 200 sin token, `/modelos` y `/clusters` dan 401, y con el token correcto pasan. Las 6 mutaciones sobre la puerta caen.

Y una que salió de probar: **la puerta corre antes que la validación de FastAPI.** Un pedido mal formado y sin token da 401, no 422 — si fuera al revés, un anónimo podría sondear qué campos existen y qué rangos aceptan. Depende de un detalle del orden de resolución de dependencias, así que quedó con test propio.

---

## El motor tenía logging pero no salida (punto 12 del backlog, 21/08/2026)

Apareció verificando el pipeline antes de pasar las mejoras post-1.0 a `main`, no buscándolo. Los 16 módulos de `src/` llaman a `logging`, y **no había un solo `basicConfig` ni handler en todo el proyecto**. Estaba anotado como *"persistir los logs"* en notas de planificación viejas y nunca llegó a ser un punto del backlog.

### La consecuencia no era estética

Sin handler en la raíz, `logging` no "usa el formato por defecto": descarta. Medido sobre el proceso real:

```
handlers en la raiz: NINGUNO
nivel efectivo de src.main: WARNING
-> un logger.info del motor SE PIERDE
```

Todo `logger.info` se tiraba, y todo `WARNING` para arriba caía en `logging.lastResort` —el handler de emergencia de la stdlib— sin fecha, sin nivel y sin nombre de módulo. Uvicorn no lo tapa: configura solo sus loggers `uvicorn*` y deja la raíz intacta **a propósito**, para no pisarle la configuración a la aplicación que hospeda. La aplicación es esta, y no la tenía.

Lo que se perdía: el resultado de cada paso del pipeline, con qué modelo se sintetizó y cuántos tokens costó, el aviso de exclusividad al activar un modelo, y **el porcentaje del ciclo que consumió la corrida** — que es, según el propio roadmap, el número con el que se calibra `INGEST_INTERVAL_MINUTES`. El motor medía su utilización y tiraba la medición.

**Un log que falta no se parece a un error**, y por eso esto sobrevivió a las cinco fases: nada fallaba.

### Las decisiones

**A stdout, no a un archivo.** El motor corre en un contenedor, y ahí un handler de archivo es la peor opción de las dos: lo esconde de `docker logs`, se lo lleva puesto cada recreación del contenedor, y sin rotación llena el disco del VPS —que también tumba a Postgres—. La persistencia y la rotación van al `docker-compose.yml`, donde el driver `json-file` las hace bien: 10 MB × 5 archivos. Medido en corridas reales, un ciclo con trabajo (11 síntesis) deja ~4,7 KB y uno sin nada que hacer ~1 KB, así que el techo son más de tres meses de historia a 96 ciclos por día.

**`LOG_SQL` aparte de `LOG_LEVEL`.** El SQL de un ciclo son miles de líneas: si viniera incluido en `DEBUG`, nadie podría poner el motor en DEBUG sin ahogarse.

**Techo en WARNING para las librerías ruidosas.** Sin él, subir la raíz a INFO —que es todo el punto— entierra al motor bajo el ruido de sus dependencias, y el resultado neto es peor que no tener logs: hay líneas, pero no se encuentra la que importa. `httpx` sola emite una por request, y un ciclo con extracción por URL pide decenas de artículos. **`apscheduler` queda deliberadamente afuera**: sus dos líneas por ciclo son la prueba de vida del scheduler, y hay un test que frena a quien lo agregue.

**Un `LOG_LEVEL` mal escrito no puede tumbar el arranque.** `basicConfig(level="INFOO")` levanta `ValueError`, y esto corre dentro del `lifespan`: un typo en el `.env` dejaría el motor sin levantar. Se cae a INFO y se avisa.

**No se le pisa la configuración a nadie.** Si la raíz ya tiene handlers, alguien más configuró el logging —uvicorn con `--log-config`, un gunicorn por delante— y ese handler manda. En ese caso tampoco se toca el encoding ni los loggers de uvicorn: si la salida es de otro, esas decisiones también.

### Tres hallazgos que no se buscaban

**1. `echo=(ENVIRONMENT == "development")` en el engine tenía que morir.** Con un echo verdadero, SQLAlchemy le cuelga un `StreamHandler` propio al logger de la Engine y **no le apaga la propagación** (verificado en `sqlalchemy/log.py`: `InstanceLogger.__init__` agrega el handler, nadie toca `propagate`). En cuanto la raíz tuviera handler, cada sentencia habría salido **dos veces, con dos formatos distintos**. Ahora `echo=False` siempre —que devuelve un `Logger` pelado— y el SQL se enciende subiendo el nivel del logger con `LOG_SQL`. Como no hay test de comportamiento que distinga las dos sin una base real conectada, quedó una guarda que mira el código, **descartando los comentarios**: el propio comentario que explica esto dice `echo=False`, y sin descartarlos la guarda daba positivo aunque el código dijera otra cosa.

**2. En Windows se perdían líneas enteras.** `sys.stdout` sale en `cp1252`, y una línea con un carácter que ese codec no tiene —un titular con `北京`, un apellido en cirílico— hace que `StreamHandler.emit` levante `UnicodeEncodeError`. `logging` no propaga esa excepción: escribe un `--- Logging error ---` con traceback y **descarta el mensaje**. Verificado con una corrida real. O sea que la línea rara, la que más ganas hay de leer, es justo la que no está. La salida se fuerza a UTF-8.

**3. La hora habría sido la del contenedor, o sea UTC.** `logging` usa `time.localtime`, mientras los mensajes que arma `tiempo.formatear` van en UTC-3 — el log habría tenido el prefijo en una zona y el contenido en otra, que es exactamente lo que `tiempo.py` existe para evitar y ya costó un bug real en la firma del webhook. La regla del proyecto es *"se guarda en UTC, se muestra en UTC-3"*, y un log es para mostrar.

Se descartó la solución obvia —`TZ` en el `docker-compose.yml`— porque **`python:3.12-slim` no trae `tzdata`**: fallaría de vuelta a UTC en silencio, que es peor que no intentarlo. En su lugar, un `converter` propio sobre `ZONA_LOCAL`, que es un offset fijo y no necesita base de zonas. El `-03` va escrito en el formato y no calculado con `%z`, porque `%z` sobre el `struct_time` que consume `strftime` daría el offset de la máquina — justo el que no queremos.

### Verificado, no supuesto

Dos corridas completas contra el back-end real, con el token puesto. Ciclo con trabajo: 11 síntesis, 13 entregas, 0 fallidas. El log resultante decodifica como UTF-8, los acentos salen bien (`Fusión`, `Ángulo`, `Síntesis`) y cada línea lleva fecha, nivel y módulo. **19 mutaciones, 19 detectadas.**

Dos de esas mutaciones no se detectaban al principio, y por el mismo motivo: **la máquina de desarrollo ya está en UTC-3**, así que `time.localtime` devuelve lo mismo que la hora argentina y el test no podía distinguir las dos implementaciones. Se arregló falseando el reloj del sistema dentro del test — parcheando los dos caminos, porque `logging.Formatter` guarda una *referencia* a `time.localtime` en un atributo de clase y pisar el módulo no la alcanza.

### Lo que el log destapó en su primera corrida

Una síntesis tituló *"Reforma previsional en Entre R&iacute;os para reducir el d&eacute;ficit"*: **entidades HTML sin decodificar** llegando al producto. Medido sobre la base: 38 de 5.390 `contenido_limpio` (0,7%), y `titulo` ninguno. Quedó como punto 13 del backlog.

Es el argumento entero de este punto en una línea: el defecto estaba ahí desde antes, y lo que faltaba para verlo era el log.

---

## Backlog punto 3: el alta de medios la hace el operador (03/09/2026)

Hasta la 1.1.0 el roster vivía hardcodeado en `scripts/seed_medios.py`. El problema no era de comodidad sino de a quién le corresponde la decisión: **el repo aceptaba los términos de uso de siete medios argentinos en nombre de cualquiera que lo desplegara**. La revisión del 19-20/08 mostró que esos términos varían muchísimo —Clarín licencia solo títulos y links, Perfil pide links de vuelta, Ámbito no tiene contrato de reuso, La Izquierda Diario reserva TDM en su `robots.txt`— y que cuál es aceptable depende del uso que le dé cada operador.

Se construyeron `GET /medios`, `POST /medios` y `PATCH /medios/{id}?activo=`.

### Lo que ya estaba hecho y nadie había notado

`Medio.activo` existe desde la Fase 1 y `ingerir_todos_los_medios` **ya filtraba por él** (`ingestion.py`, `select(Medio).where(Medio.activo.is_(True))`). O sea que la semántica "deshabilitar no es borrar" estaba implementada en el pipeline desde el principio y lo único que faltaba era el endpoint que diera vuelta la bandera. Encontrarlo cambió el tamaño del punto: la baja pasó de ser una funcionalidad a ser tres líneas más un test de integración que fija que la bandera efectivamente manda sobre lo que el motor sale a buscar.

### Las cinco decisiones

**1. El sondeo bloquea lo inservible e informa lo opinable.** Se evaluaron las tres posturas. Bloquear todo, como hace `POST /modelos`, es coherente con el precedente pero deja que un feed caído cinco minutos impida registrar un medio que el operador ya decidió sumar. Informar todo nunca traba, pero deja registrar un feed que da 404 permanente y enterarse quince minutos después por un mail de alerta.

Se eligió el mixto, y el criterio para partirlo es **si hay algo que decidir**. Un feed que no responde, no parsea o no trae un solo item utilizable está roto y ninguna decisión lo arregla — el `/feed/internacionales` de Perfil da 404 aunque Perfil lo publique en su propia página de RSS. En cambio que un medio retenga el cuerpo, que su `robots.txt` sea restrictivo o que la ventana parezca archivo son cosas sobre las que el operador tiene algo que decir, y el motor no puede dictaminar por él sin volver a cometer el error que este punto vino a corregir.

**Todos los feeds de la lista tienen que servir**, no alcanza con que sirva uno: la lista la manda el operador de forma explícita, así que uno roto es un error de tipeo que conviene ver ahora.

**2. `extraer_por_url` la decide el operador; el sondeo solo informa.** Es la tentación obvia —el sondeo ya sabe si el feed trae `content:encoded`, prenderla sola sería un renglón— y se descartó a propósito. Esa bandera marca los medios donde el motor va a buscar a la página el cuerpo que el medio **eligió no publicar** en su feed, y cruzar esa línea es exactamente la decisión que este backlog le devuelve a quien acepta los términos.

Se la contrastó con `modo_estructura` en `POST /modelos`, que sí se autodescubre, y la diferencia aguanta: allá lo que se descubre es un detalle técnico del protocolo (si el proveedor acepta `response_format` o solo tool-calling), acá lo que se decidiría es un permiso.

**3. Campos nuevos: `idioma`, `pais`, `logo_url`.** El logo se guarda como URL y no como bytes: mantiene la base chica y sin datos binarios, y el costo asumido —que el logo deje de verse si el medio mueve el archivo— es de presentación, porque nada del pipeline lo mira.

El sondeo los **propone** leyéndolos del canal RSS (`<language>` y `<image><url>`), pero el valor que se guarda es el que manda el operador: esos tags son opcionales y muchos feeds los traen mal o vacíos.

Se evaluó también sumar `terminos_url` y `descripcion` para dejar auditable qué términos aceptó el operador, y quedó afuera de esta tanda.

**4. No existe DELETE: la baja es `activo=False`.** Dos motivos que apuntan al mismo lado. El de producto: deshabilitar tiene que ser reversible sin perder nada, para apagar un medio hoy y volver a prenderlo el mes que viene. El de datos: `Noticia.medio_id` es `NOT NULL` con clave foránea a `medio.id` **sin cascada**, así que borrar un medio que ya ingirió algo violaría la restricción — y forzarlo con cascada se llevaría puestas noticias que quizá ya formaron clusters, se sintetizaron y se entregaron al back-end.

**5. El alta deja el medio habilitado.** Diferencia deliberada con `POST /modelos`, donde `activar` es `False` por default. Allá prender un modelo **apaga a los demás** (la credencial es una sola, dos proveedores prendidos son un estado inusable), así que activar es un interruptor y encenderlo solo sería tomar una decisión ajena. Acá los medios conviven —el clustering *necesita* varios para encontrar el mismo hecho contado por distintas redacciones— y sumar uno es aditivo. Dar de alta un medio para después tener que acordarse de prenderlo sería ceremonia sin contenido.

Por eso mismo `PATCH /medios/{id}` **no** apaga a los demás, a diferencia de su equivalente de modelos.

### El SSRF: por qué el validador se revisó y no se copió

El endpoint hace que el motor **pida una URL elegida por quien llama**, que es la definición de SSRF. El patrón ya estaba resuelto en `proveedores.base.validar_base_url`, y el roadmap avisaba que había que revisarlo y no copiarlo porque acá el destino es *cualquier sitio web* y no un endpoint de API con forma conocida.

Al leerlo apareció la diferencia concreta. `proveedores.base.REDES_PROHIBIDAS` bloquea **solo link-local**, y su comentario explica que no bloquear los rangos privados fue deliberado: un modelo de IA en `localhost:11434` o un vLLM en la red interna es justamente el caso que ese backlog existe para habilitar, y encima es el escenario donde los cuerpos de los artículos no salen de la máquina.

**Ese razonamiento no se traslada.** Un medio de noticias en `127.0.0.1` o en `10.0.0.5` no tiene ningún uso legítimo, y sin bloquearlos el endpoint sería un escáner de la red interna a pedido de quien llame: aunque el cuerpo no se devuelva, la diferencia entre "no responde" y "responde pero no es un feed" ya delata qué hay escuchando. Así que `medios.REDES_PROHIBIDAS` suma loopback, los tres rangos privados de IPv4, `fc00::/7` y `0.0.0.0/8`.

Hay un test que fija la divergencia a propósito (`test_es_mas_estricto_que_el_de_proveedores_y_a_proposito`): comprueba que `validar_base_url` acepte `localhost` y que `validar_url_de_feed` lo rechace. Si alguien "unifica" los dos validadores, se cae y explica por qué no hay que hacerlo.

**`url_base` pasa por el mismo validador que los feeds**, y no por simetría: el sondeo le pide `{url_base}/robots.txt`, así que sin validarlo el SSRF entraba por la puerta de al lado.

### Dos hallazgos de leer el código antes de escribirlo

**`_parser_robots` manda mails.** Reusarla tal cual para el sondeo habría hecho que cada alta contra un dominio caído le mandara un mail de alerta al operador — y que cualquiera con acceso al endpoint pudiera inundarle la casilla. Se extrajo `extraccion.leer_robots`, la parte pura sin caché ni alertas, y `_parser_robots` ahora la envuelve. La ingesta sigue alertando igual, que es lo correcto ahí: perder la cobertura de un medio entero sí es grave.

**`_descargar_feed` no sirve para sondear.** Trae `@retry` de 3 intentos con backoff exponencial hasta 10 s —casi 20 s por feed— y acá hay una persona esperando la respuesta de un alta. El sondeo lleva política de red propia y más corta, con el mismo criterio que ya había tomado `extraccion._descargar_pagina` frente a la misma tentación.

Lo que sí se reusa es `ingestion._parsear_entry`, aunque sea privado: **es la definición de qué cuenta como item utilizable**, y el sondeo tiene que contar exactamente lo mismo que después va a ingerir el pipeline. Una segunda copia del criterio haría que el alta prometiera items que la ingesta descarta.

### La ventana temporal se mide sobre la fecha declarada

`_parsear_entry` cae en `ahora_utc()` cuando el item no trae fecha, que es lo correcto para persistir pero daría una ventana de 0 h para un feed sin fechas — una medición inventada, justo la que el sondeo reporta. Se mide sobre `published_parsed` y, si no hay al menos dos fechas declaradas, se informa que no se pudo medir en vez de inventar un número.

El umbral de "esto huele a archivo" quedó en 72 h, contra lo medido: los feeds generales que ya corren son ventanas móviles de 7 h (La Nación, Perfil) a 23 h (TN), mientras que los de sección guardan meses. Es un aviso y no un rechazo — un medio chico que publica dos notas por semana tiene una ventana ancha y es perfectamente legítimo.

### Verificación

604 tests (52 nuevos), `ruff` limpio, `alembic check` contra Postgres.

**7 mutaciones, 7 detectadas.** Una de ellas encontró un test flojo antes de que llegara a `main`: la aserción del aviso "este feed no trae cuerpo" buscaba la subcadena `extraer_por_url`, que **también aparece en el otro aviso** —el de "solo algunos items traen cuerpo"—, así que borrar el primero pasaba desapercibido. Se ajustó a la parte distintiva del mensaje.

### Lo que quedó afuera, a propósito

**Retirar el roster del repo.** El roadmap lo pide, pero sacarlo obliga a resolver la migración de las instancias que hoy corren con siete medios cargados, y eso es una decisión aparte. `scripts/seed_medios.py` sigue existiendo y pasó a ser **datos de ejemplo**: se le reescribió el bloque de comentarios para que conserve el conocimiento medido que el alta por API no puede redescubrir sola —por qué Clarín, Ámbito y La Izquierda Diario quedan afuera, la trampa de los feeds de sección de TN, el `?outputType=xml` obligatorio de Ciudad Magazine— porque son juicios sobre términos de uso, no sobre feeds.

**Editar los datos de un medio ya cargado.** No hay `PATCH` de metadatos: los siete medios que ya existían se quedan con `pais` en `None` hasta que alguien los complete, y `idioma`/`pais`/`logo_url` quedaron fuera de `CAMPOS_SINCRONIZADOS` del seed porque son datos descriptivos y no configuración de ingesta —el seed no debe pisar lo que el operador ajustó a mano—.

### El ataque a los endpoints nuevos, y lo que encontró (03/09/2026)

Antes de commitear el punto 3 se atacaron los tres endpoints en vez de revisarlos de a ojo: servicio señuelo en loopback, ataques corriendo contra el motor vivo, y verificación en un contenedor `python:3.12-slim` de lo que no reproducía en Windows. Aparecieron seis cosas; se arreglaron tres en esta tanda y las otras quedan anotadas abajo.

#### 1. SSRF por redirect — `follow_redirects=True` hacía de adorno al validador

El agujero real y verificado: `validar_url_de_feed` aprueba la URL que le mandan, y **httpx después se va sola a donde diga el `Location`**, sin que nadie revalide. Un host público que responda `302 -> http://169.254.169.254/latest/meta-data/` atravesaba el filtro entero. En la prueba, el motor leyó el cuerpo de un servicio en `127.0.0.1` cuya URL directa el validador sí rechazaba.

**Se evaluaron las dos salidas y se midió antes de elegir.**

*Rechazar el redirect y pedirle al operador la URL final* era más simple y de superficie cero — no hay cadena que validar, así que no hay validador de cadena que se equivoque— y además dejaría mejor dato persistido: los feeds que redirigen pagan un salto en cada uno de los 96 ciclos diarios.

Se descartó por lo que dijo la medición sobre los 8 feeds del roster: **3 redirigen** (TN, El Cronista y Revista Gente, que además cambia de dominio a `revistagente.com`), o sea que habría dado de baja tres medios en producción. Y sobre todo por *por qué* redirigen: TN y El Cronista **ya migraron a Arc**, y ese 301 es lo que mantiene viva la URL que tenemos sembrada. Fijar la URL final nos rompería en la mudanza siguiente, en silencio.

**Elegido: seguir los redirects con un bucle propio que valida cada salto** (`bajar_siguiendo_redirects`). Tope de 5 saltos —ninguno de los 3 reales pasa de 1—, detección de bucles, y `urljoin` para resolver un `Location` relativo. La URL inicial se revalida aunque el llamador ya lo haya hecho, para que la función sea segura la llame quien la llame.

**No cierra el TOCTOU**, y está dicho en el código: entre validar la IP y conectar hay una segunda resolución de DNS. Es el mismo límite que `proveedores.base` ya asume; cerrarlo exige conectar a la IP validada preservando Host y SNI, artillería desproporcionada para un motor de un operador.

#### 2. `leer_robots` tenía el mismo agujero, y la salida obvia lo habría roto

También usaba `follow_redirects=True`, y le llega un `url_base` recién mandado por quien llama al alta. La salida evidente era `follow_redirects=False`, y la medición la descartó: **el `robots.txt` de Revista Gente redirige** (1 de 8 medidos). Cortarlos de plano habría dejado ese medio sin poder leer su `robots.txt` — o sea sin extracción, fallando cerrado, con un mail de alerta por ciclo. Usa el mismo bucle validado, con import local para no armar ciclo (el recurso que ya usa `search.listar_clusters`).

#### 3. Una IPv4 escondida en una IPv6 se colaba entera

`::ffff:127.0.0.1` apunta a loopback, pero como objeto es un `IPv6Address`, y `IPv6Address in IPv4Network("127.0.0.0/8")` da `False`. Pasaba el filtro.

**En Windows no conecta y parecía inofensivo.** Se verificó dentro de `python:3.12-slim`, que es el destino real de despliegue: **pasa el filtro y conecta**. Es la clase de hallazgo que se pierde si uno se conforma con que no reproduzca en la máquina de desarrollo.

Entró junto con el punto 1 y no como tanda aparte porque sin esto el arreglo del redirect es evitable: alcanza con redirigir a `http://[::ffff:127.0.0.1]/`.

El arreglo cambió la forma de preguntar. **La regla de fondo pasó a ser `is_global`, en positivo**: un medio de noticias vive en una dirección ruteable en internet, punto. Enumerar rangos malos es una carrera que se pierde, y se perdió en el acto — la primera versión del filtro dejaba pasar `100.64.0.1` (CGNAT) porque **en Python 3.13 `is_private` da `False` ahí**. La lista explícita se conserva igual, por dos motivos: es la parte auditable, y cubre `64:ff9b::/96` (NAT64), lo único que `is_global` considera global aunque encapsule una IPv4.

#### Lo que resistió el ataque

Vale anotarlo para no volver a probarlo:

- **XXE**: `<!ENTITY xxe SYSTEM "file:///...">` volvió como el literal `&xxe;`. feedparser no resuelve entidades externas.
- **Bomba de entidades** (billion laughs): "undefined entity", no expande.
- **Notación decimal/hex/octal de IP**: parecía evasión en Windows, pero en Linux `getaddrinfo("2130706433")` devuelve `127.0.0.1`, así que el validador sí la ve. Falsa alarma — se verificó antes de anotarla.
- Inyección SQL, mass assignment (`extra="forbid"`), y las formas directas de SSRF que el validador ya bloqueaba.
- **La extracción de artículos** (`extraccion.py`) toma URLs del feed, o sea contenido de terceros, y también sigue redirects — pero está protegida por su propia exigencia: pide poder leer el `robots.txt` del destino y falla cerrado si no puede, así que un `169.254.169.254` metido en un feed no llega a pedirse.

#### Lo que la prueba de mutación enseñó sobre las capas

14 mutaciones, **13 detectadas**. La que no se detecta está etiquetada como tal: romper el desenvolvimiento de IPv4-en-IPv6 no reabre nada, porque `is_global` ya las rechaza por su cuenta. Es capa redundante haciendo su trabajo, no un hueco — y se mantiene igual porque `ipaddress` **ya cambió de semántica una vez y nos mordió** (el CGNAT), así que apoyar una defensa en una sola propiedad de la librería estándar es la apuesta que acabamos de perder.

Una mutación sí destapó un hueco real: **`follow_redirects=True` no se detectaba**, porque todos los tests mockean `httpx.get` entero y el mock ignora ese kwarg. Habrían quedado en verde con el agujero abierto en producción. Se agregó un test que mira el kwarg y no el resultado.

#### Lo que quedó abierto, y por qué

Tres hallazgos del mismo ataque **no** se arreglaron acá, para no mezclar tandas:

- **Amplificación de pedidos.** `feeds_rss` no tiene techo de elementos ni deduplica. Medido lineal: 12 copias del mismo feed = 12 pedidos reales. Con 10.000 entradas, un solo POST convierte al motor en un ariete contra un tercero con nuestra identidad en el User-Agent, y bloquea un worker sincrónico por horas.
- **Campos sin techo y `logo_url` sin validar.** Se persistieron 500 KB de `url_base` y 500 KB de `logo_url`, este último empezando con `javascript:`. `GET /medios` lo devuelve tal cual: es XSS almacenado esperando a la interfaz de escritorio.
- **`PATCH /medios/{id}` con un `id` mayor que bigint da 500** (desborde en Postgres). No filtra nada, pero contradice la regla de que una entrada mala es 4xx con mensaje.

### Tanda 2 de la auditoría: la credencial de IA no sale hacia un host sin declarar (03/09/2026)

**Cierra SR-01**, el hallazgo crítico. `POST /modelos` le mandaba `MODELO_API_KEY` como Bearer al `base_url` que le indicaran —dos veces, una por cada mecanismo de estructura que prueba el sondeo— **antes de saber si el proveedor servía**. Verificado con un captor local, que registró la llegada de la credencial sin imprimir su valor. El alta devolvía 422 y no guardaba nada, pero la key ya había viajado.

`validar_base_url` bloqueaba solo link-local, y eso era deliberado: el comentario decía que un modelo en `localhost:11434` es el caso que el punto 2 existe para habilitar. Lo que faltaba no era bloquear más adentro sino **mirar hacia afuera**.

#### La decisión: lista blanca, con la regla invertida

Se evaluaron tres formas.

*Prohibir loopback y privadas, como en `services/medios.py`*, era lo simétrico. Se descartó rápido: mata el modelo local —el único escenario donde los cuerpos de los artículos no salen de la máquina— y **no arregla lo que importa**, porque la exfiltración hacia un host externo seguiría permitida. Habría sido gastar el cambio en el lado equivocado.

*Sondear en dos pasos, primero sin credencial*, no necesita configuración nueva. Se descartó porque casi todos los proveedores contestan 401 sin key, así que el primer paso no distingue un destino sano de uno hostil — y encima no impide que alguien confirme igual hacia un host malicioso.

**Elegido: `MODELO_HOSTS_PERMITIDOS`.** Un destino público tiene que estar declarado; la red interna no. Lo importante es que **la regla queda invertida respecto de `medios.py`**, y eso no es una inconsistencia sino la consecuencia de que se protege otra cosa: allá lo interno es lo sospechoso porque el riesgo es que nos usen de escáner de la red; acá lo interno es lo confiable porque el riesgo es que la credencial se vaya lejos. Los dos comentarios se remiten mutuamente para que nadie los "unifique".

#### Arranca vacía, y el error dice qué agregar

Fue decisión del usuario entre tres opciones. Sembrarla con los proveedores conocidos no rompía nada, pero era el repo decidiendo a quién confiarle la credencial del operador — exactamente lo que el punto 3 acababa de corregir para los medios. Hacerla opt-in como `API_TOKEN` era coherente con `auth.py`, pero ahí el riesgo es exponer un servicio y acá es filtrar tu propia credencial por un error de tipeo: un default inseguro pesa distinto.

Vacía, entonces, con el mismo criterio de "informar, no decidir" que el alta de medios. El 422 no dice "no permitido" sino la línea exacta:

```
MODELO_HOSTS_PERMITIDOS=ya.declarado.test,nuevo.test
```

**Rompe configuración al actualizar**, y va avisado: una instancia que use `openai_compatible` contra un proveedor público deja de sintetizar hasta declarar su host. La que corría acá no se vio afectada —su modelo activo es el Gemini nativo, que no usa `base_url`— pero su `groq-qwen` apagado sí necesita la declaración para volver a prenderse.

#### Los detalles que se decidieron y por qué

**Comparación exacta, no por sufijo.** Declarar `openai.com` no habilita `openai.com.atacante.net` ni `evil.openai.com`, y un homógrafo con cirílico no coincide con el host latino. Cada endpoint regional se declara aparte: explícito es el punto.

**Se normaliza caja, punto final y se ignora el puerto.** DNS no distingue mayúsculas, `api.groq.com.` es el mismo host, y si confiás en un host el puerto no cambia a quién le hablás.

**Un host que no resuelve cuenta como público**, o sea que hay que declararlo. Es fallar cerrado: desde el validador no se distingue un `.local` que resolverá por mDNS al hacer el request de un dominio que todavía no existe pero existirá mañana, y lo que está en juego es una credencial. El mensaje de error lo dice, para que un problema de DNS no se lea como un problema de lista.

**La lista habilita, no levanta las otras defensas.** Declarar `169.254.169.254` no alcanza: link-local se comprueba antes y sigue bloqueado. Hay un test que lo fija.

#### Lo que el arreglo NO cierra, dicho con todas las letras

La credencial **sigue saliendo hacia loopback y hacia la red interna sin declaración**, y se comprobó volviendo a correr el captor: recibió el Bearer igual. Es el precio elegido, y es el correcto — quien pueda levantar un servicio en tu máquina ya tiene tu máquina, y la red interna es justamente lo que esta funcionalidad existe para soportar.

#### Un defecto de la suite que destapó el `.env`

Al definir `API_TOKEN` en la tanda 1, **63 tests de endpoints pasaron a fallar con 401**. No estaba mal el código: el resultado de la suite dependía de si quien la corría tenía un token configurado. `test_modelos.sin_el_env_de_la_maquina` ya aislaba las credenciales, pero esta variable se le había escapado.

Se agregó `api_sin_token` como fixture `autouse` en `conftest.py`, que la neutraliza para todos los tests; `test_auth.py` la pisa con sus propias fixtures, porque ahí el token es el objeto del test y no ruido del entorno. Comprobado corriendo la suite con `API_TOKEN` y `MODELO_HOSTS_PERMITIDOS` forzados desde el entorno a valores distintos: 642 pasando igual.

#### Verificación

642 tests (13 nuevos), `ruff` limpio, **6 mutaciones y 6 detectadas** sobre la comprobación nueva —incluidas la coincidencia por sufijo, la pérdida de normalización y el host que no resuelve—. Y el ataque original repetido contra el código parcheado, esta vez **con un token válido**, o sea en el peor caso: `https://proveedor-malicioso.test/v1` rechazado con 422 y la línea que falta.

### Tanda 3 de la auditoría: lo que entra por la API tiene forma y tiene techo (04/09/2026)

Cierra los **cuatro hallazgos menores** que quedaron anotados al final de la tanda 1. Ninguno filtraba datos, y por eso fueron a una tanda aparte; lo que tenían en común es que el motor aceptaba y **persistía** entradas que después alguien más iba a tener que interpretar o renderizar. Los cuatro se reprodujeron contra el motor antes de tocar nada y se volvieron a correr después.

#### El más importante no es el que más ruido hacía

`logo_url` aceptaba `javascript:alert(document.cookie)`, lo guardaba, y `GET /medios` lo devolvía intacto. No es un problema del motor —el motor nunca baja esa URL— y por eso el escáner de la tanda 1 lo dejó al final de la lista: **es XSS almacenado esperando a que exista un visor**, y el visor es justamente la aplicación de escritorio que se está evaluando. Un hallazgo que hoy no hace nada y que se dispara solo el día que se escriba la pantalla de fuentes es peor que uno ruidoso, porque para entonces nadie va a estar mirando este endpoint.

Se cierra con `validar_url_de_logo`, que exige http o https. **Comprueba la forma y no la red, y la diferencia con `validar_url_de_feed` es deliberada:** el feed lo baja el motor, así que ahí `REDES_PROHIBIDAS` cierra un SSRF real; el logo lo pide el navegador de quien mire la interfaz, y a esa altura la dirección privada que alcanza es la suya. Resolver DNS acá sería pagar una consulta de red en cada alta para defender algo que no existe, y encima rompería un logo servido desde la intranet de quien despliega esto. Hay un test que fija las dos mitades: `http://192.168.1.10/logo.png` entra como logo y sigue rechazado como feed.

Las tres comprobaciones que no tocan la red —esquema, dominio, credenciales embebidas— se extrajeron a `_validar_forma_de_url` y ahora las comparten los dos. La alternativa era una segunda copia de la lista de esquemas peligrosos, que es la clase de duplicado que se desincroniza el día que haya que agregar uno.

#### La amplificación: deduplicar primero, y después medir

`feeds_rss` no deduplicaba ni tenía techo. Medido: 500 copias de la misma URL en un solo POST daban **501 pedidos reales** al mismo servidor —lineal, uno por copia— con nuestro User-Agent puesto, y la fila quedaba con las 500 repetidas adentro.

El orden de las dos operaciones fue la decisión, y va al revés de lo obvio: **primero se deduplica y después se mide el techo.** Una lista pegada con repetidas pide pocos feeds distintos aunque sea larga; cortarla primero por largo la rechazaría entera y obligaría al operador a limpiarla a mano para descubrir que siempre estuvo dentro del límite. Así, las 500 copias entran como un feed y cuestan un pedido.

El techo quedó en **20 feeds distintos**, y lo fija el peor caso de latencia y no el gusto: el sondeo consulta cada feed con 10 s de timeout, así que veinte que no respondan ocupan un worker 200 s — acotado y reportable, que es lo que antes no era. Contra la realidad medida sobra: los 7 medios del roster usan **un** feed cada uno y el experimento más grande que se hizo llegó a 8. Se eligió el lado generoso a propósito, porque desde la tanda 1 `POST /medios` pide token: **el atacante anónimo ya no existe**, y lo que esta cota frena hoy es sobre todo el error de tipeo que destapó el ataque.

Se deduplica **en dos lugares, y no es redundancia**: `AltaMedio` lo hace para que la fila quede limpia, y `sondear` lo hace de nuevo porque cubre el otro camino — `PATCH` sondea la lista **guardada**, que en una fila anterior a esta versión, o cargada por `scripts/seed_medios.py` (que no deduplica), puede traer repetidas. Sin la segunda, rehabilitar esa fila reabría la amplificación entera. Hay un test por cada camino.

#### Las cotas de largo, y dónde se traza la línea

Se persistieron 500 KB en `url_base` y otros 500 KB en `logo_url`. El techo quedó en **2048 caracteres** para toda URL que entre por la API: es el límite histórico de Internet Explorer, y por eso es el número bajo el que se quedó todo lo que quiere ser alcanzable. La URL más larga del roster tiene 62 caracteres, así que entra 33 veces.

Va en el **elemento** de `feeds_rss` y no solo en la lista, porque sin eso un único feed de 500 KB seguía pasando. Y se le puso la misma cota a `base_url` de `AltaModelo`, que tenía el mismo agujero: a dónde puede apuntar ya lo decide `MODELO_HOSTS_PERMITIDOS` desde la tanda 2, pero su largo no lo decidía nadie.

`idioma` y `pais` pasaron de `max_length` a `pattern`. Ocho caracteres alcanzan para `<script>` —que es exactamente el largo de `idioma`—, y son códigos y no texto libre, así que la forma se puede exigir entera: BCP-47 corto y ISO 3166-1 alfa-2.

**`nombre` queda como texto libre, y es la línea.** Se acotan las URLs y los códigos porque son valores con forma, y en el caso del logo porque el esquema es la parte ejecutable. `nombre` es texto para mostrar: escaparlo al renderizar es trabajo de quien lo renderiza, y filtrarlo acá rompería un medio que se llame `Página/12` o `AM 750 & Co.`. Hay un test que lo fija, para que la próxima pasada de seguridad no lo "arregle".

#### Los dos 500 por una entrada mala

`PATCH /medios/{id}` y `PATCH /modelos/{id}` con un id más grande que la columna reventaban con `OverflowError` — medido en SQLite, que es donde corre la suite. No filtraba nada; contradecía la regla de que una entrada mala es 4xx con mensaje, y esa regla es lo que hace que un 500 signifique "se rompió algo nuestro".

Ambos `id` son `integer` de 32 bits, consultado al esquema vivo de Postgres y no supuesto (`information_schema.columns` sobre `medio` y `modelo_ia`), así que la cota es `2**31 - 1`. Y `ge=1` del otro lado, porque las secuencias arrancan en 1 y un id negativo es un error de quien llama, no un 404 que sugiere que alguna vez existió. Un id válido que no está sigue siendo 404: hay un test que separa "imposible" de "no está".

`POST /vectorize?limite=-1` devolvía `{"pendientes": -1}` con un 200. No vectorizaba nada, pero **informaba un número imposible como si lo hubiera medido**, que es lo que lo vuelve un problema y no una curiosidad. `/search` y `/clusters` ya acotaban su `limite` desde la Fase 4; a este endpoint se le había pasado. Queda `ge=1` sin techo, porque el backlog real ya lo acota y pedir de más no cuesta nada.

#### Verificación

687 tests (45 nuevos), `ruff` limpio, `alembic check` contra Postgres sin operaciones pendientes (la tanda no toca el esquema).

**18 mutaciones y 18 detectadas.** Se rompió a propósito cada protección nueva, incluidas las tres que no son evidentes: invertir el orden de dedup y techo, sacarle la cota al elemento de la lista dejándosela a la lista, y hacer que el logo resuelva DNS como el feed. Las dos primeras las cazó un test distinto del que estaba escrito para ellas —`-x` corta en el primer fallo—, así que se volvieron a correr dirigidas contra su propio test para confirmar que no era casualidad.

Y **los cuatro ataques repetidos contra el motor vivo**, sobre Postgres real, por HTTP de verdad y con token válido (el peor caso: atacante autenticado). Las 500 copias del feed de Perfil ahora se guardan como una y cuestan un pedido; 21 feeds distintos dan 422 nombrando el límite; `javascript:` y `data:text/html` en el logo dan 422 diciendo que el campo es el logo; los 500 KB dan `string_too_long`; y los seis `id` imposibles contra los dos `PATCH`, más `?limite=-1` y `?limite=0`, dan 422 en vez de 500. En la misma corrida se comprobó que lo legítimo sigue pasando: alta real de Perfil (50 items, 1 aviso), y el ciclo de deshabilitar y rehabilitar. Las filas de prueba se borraron después; la base quedó con los 8 medios que ya tenía.


### Backlog punto 8: purga de cuerpos — borrar el texto ajeno una vez que cumplió su función (04/09/2026)

**Cierra el punto 8 del backlog, parcialmente y a propósito.** El alcance es solo las noticias huérfanas —sin cluster, con su ventana ya vencida—; los clusters cerrados y entregados quedan afuera, aunque son candidatos por el mismo argumento de antigüedad. Es una segunda población con su propia condición de seguridad (la re-síntesis), y mezclarla acá habría resuelto dos problemas con una sola comprobación. Queda anotada como tanda aparte en `roadmap.md`.

Medido antes de tocar nada: **22 MB de texto de terceros**, de los cuales **4.083 noticias (69%) ya habían vencido la ventana de 7 días** sin haber formado cluster nunca. Después de purgar: **6,58 MB**, **15,5 MB liberados**. Se corrió de verdad contra la base de producción, no solo contra los tests — ver "Verificación en vivo" más abajo.

#### La decisión que definió el punto 4 del plan: medir antes de escribir código

El plan original dejaba abierto cómo resolver el único consumidor que sí lee el corpus entero, `get_vectorizador` (TF-IDF de `preprocessing.py`), con dos caminos: excluir las purgadas del corpus, o dejar que aporten solo su título. Antes de elegir se armaron los dos vectorizadores contra la base real y se corrió `_terminos_propios` —lo que sale publicado como "qué destacó cada medio"— sobre 15 clusters ya sintetizados (38 pares cluster×medio):

| | Vocabulario | Cambio de código |
|---|---|---|
| Hoy (cuerpo completo) | 109.127 términos | — |
| Excluir del corpus | 29.679 términos | filtro en el `select` + resincronizar el disparador de reajuste |
| Dejar que degrade solo | 32.457 términos | **ninguno** |

El segundo camino no necesita tocar `preprocessing.py`: la línea que arma el corpus ya es `f"{n.titulo}. {n.contenido_limpio}"`, así que una fila con `contenido_limpio=''` se reduce sola a "solo título". Es la consecuencia automática de la **marca** elegida (`purgado_en` + `contenido_limpio=''`) y no una decisión aparte. Ganó por ser al mismo tiempo el más simple —cero código— y el que evita el riesgo que el propio plan había anotado: si el corpus excluyera filas, el conteo `total` que dispara el reajuste (línea 100) tendría que mirar exactamente el mismo filtro, y desincronizarlos sería el bug silencioso de siempre. Confirmado contra la base real después de purgar: **38.730 términos** (más que la simulación porque el corte real usó 7 días y no "toda huérfana", así que quedaron más noticias recientes aportando cuerpo completo).

En los 38 pares revisados, `_terminos_propios` cambió en todos —nunca es idéntico al de hoy— pero los términos siguieron siendo coherentes con el tema de cada cluster; el "núcleo común" (lo que comparten los medios) se mantuvo prácticamente estable entre las tres variantes.

#### La condición de seguridad que no estaba en el plan original

`_limite_de_purga` no usa `DIAS_RETENCION_CUERPO` solo: usa `max(DIAS_RETENCION_CUERPO * 24, HORAS_CLUSTER_ABIERTO)`. La razón es un caso real y no una cautela decorativa: `HORAS_CLUSTER_ABIERTO` es configurable por el operador, y el propio comentario de `DIAS_RETENCION_CUERPO` invita a subirlo como la recuperación ante un problema (es lo que ya hace `HORAS_MAXIMAS_SIN_SINTETIZAR` con la síntesis). Si `HORAS_CLUSTER_ABIERTO` quedara alguna vez por encima de los 7 días de default, un límite de purga fijo le comería el cuerpo a noticias que `agrupar_pendientes` todavía considera candidatas a cluster — la purga rompiendo el clustering, en silencio. El `max()` hace que ese error de configuración no pueda pasar, en vez de confiar en que dos números configurados por separado se mantengan en el orden correcto. Hay dos tests que fijan exactamente este borde, subiendo `HORAS_CLUSTER_ABIERTO` a 300 h y comprobando que una huérfana de 200 h sobrevive mientras una de 310 h no.

#### Qué sobrevive y qué no

Se borra **solo `contenido_limpio`**, nunca la fila: sobreviven título, URL, guid, fecha, medio y el `embedding`, así que `GET /search` no se entera. La marca es `purgado_en` (nueva columna, migración `5a246beb14bd`) más `contenido_limpio=''` — no alcanza con la cadena vacía sola, porque sin la fecha no se puede distinguir "se purgó" de "nunca tuvo cuerpo" (que hoy no pasa: la ingesta descarta antes de insertar cualquier nota sin cuerpo, salvo por extracción).

**Es irreversible, y se documentó así antes de escribir el endpoint.** El texto no vuelve —la ventana del feed que lo trajo ya pasó, ni re-ingiriendo se recupera— y el costo real no es perderlo para el producto (ya cumplió, se vectorizó antes de que este paso corra) sino no poder revectorizar si algún día cambia `EMBEDDING_MODEL`. Por eso `POST /purge?solo_contar=true` existe como primera clase: mide exactamente la misma condición sin escribir, para comprobar el alcance contra datos reales antes de tocarlos.

`purgar_cuerpos_vencidos` corre como último paso de `_job_ingesta_programada`, después de la entrega — a propósito, para que nada de la corrida dependa de que haya pasado antes — y es idempotente por construcción: `purgado_en IS NULL` en la condición hace que correrla de más no tenga costo.

#### Un defecto que destapó la propia disciplina de mutación, dos veces

Al mutar a propósito el `session.commit()` (romperlo para confirmar que algún test lo cazaba), no lo cazó nada. La causa es del entorno de tests y no del código: la base SQLite en memoria usa una sola conexión (`StaticPool`), así que `session.refresh()` ve la escritura pendiente aunque nunca se haya confirmado — no hay una segunda conexión real contra la que distinguir "escrito" de "confirmado". El síntoma que sí importa (que la corrida siguiente, con una sesión nueva, no vea el cambio) no es observable en ese entorno. Se agregó un test que espía `session.commit` directamente (`patch.object(session, "commit", wraps=session.commit)`), primer caso de este patrón en la suite: verificar que el método se llamó es lo único equivalente que el entorno permite comprobar.

**El segundo hallazgo fue un defecto del propio proceso de mutación, no del código.** El primer intento de correr la tanda de mutaciones se mató por timeout a mitad de una corrida, y quedó una mutación real aplicada y sin restaurar: el endpoint `POST /purge` había quedado con `solo_contar=False` hardcodeado, ignorando el parámetro de la query. La revisión posterior del diff lo encontró antes de commitear nada — ahí es donde sirvió releer el propio cambio en vez de confiar en que "la suite ya había pasado en verde" (había pasado, pero *antes* de que el script de mutaciones corrompiera el archivo). Corregido, y la tanda completa se volvió a correr desde una base confirmada limpia.

#### Verificación

707 tests (20 nuevos), `ruff` limpio, `alembic check` contra Postgres sin operaciones pendientes. **12 mutaciones y 12 detectadas** sobre `purga.py` y su cableado —incluidas las tres no evidentes: el `max()` de la ventana de seguridad, el orden solo-contar/escritura, y el `commit()` recién descripto—.

**Verificación en vivo, contra la base de producción real:**
1. `pg_dump` completo antes de tocar nada (22,3 MB, formato custom, verificado con `pg_restore --list`).
2. `solo_contar=true`: 4.083 noticias, 16.246.730 bytes — coincidió exacto con la corrida real.
3. Purga real: `{"evaluadas": 4083, "purgadas": 4083, "bytes_liberados": 16246730}`.
4. Confirmado contra la base: `noticia` sigue en 5.880 filas (nada se borró), texto total 22 MB → 6,58 MB, y **`purgadas_con_cluster = 0`** — ninguna noticia agrupada fue tocada.
5. Segunda corrida: `{"evaluadas": 0, "purgadas": 0}` — idempotente contra datos reales.
6. Spot-check de filas purgadas: título y `embedding` intactos, `contenido_limpio` vacío.
7. `get_vectorizador` reconstruido contra la base ya purgada: 38.730 términos, sin excepciones.
8. `agrupar_pendientes` corrido contra el estado post-purga: sin excepciones.

**Deliberadamente no se corrió `/synthesize` ni `/deliver`** como parte de esta verificación: el primero gasta la cuota de la API de síntesis y el segundo entregaría al back-end real, y ninguno de los dos hace falta para confirmar que la purga no rompió el pipeline — `agrupar_pendientes` y `get_vectorizador` ya lo prueban sin gastar nada.


#### Revisión independiente antes de commitear, y las correcciones que salieron de ella (04/09/2026)

Se pidió una revisión con un modelo distinto (Opus, sin el contexto de esta sesión) enfocada en un solo criterio: qué podía causar pérdida de información valiosa, dado que la purga es irreversible por diseño. Corrió mutaciones reales contra una copia del repo y auditó la corrida en producción con SQL de solo lectura, sin tocar nada. Encontró un hallazgo urgente, uno de alcance ya ejecutado, y varios menores. Los primeros dos se resolvieron con las decisiones del usuario; los menores se corrigieron todos.

**El hallazgo urgente: `_condicion_de_purga` no exigía `embedding IS NOT NULL`.** El docstring del módulo daba por sentado que toda huérfana "ya se vectorizó antes de que este paso corra" — una suposición sobre el orden del pipeline, no algo que la consulta obligara. Si la vectorización fallara alguna corrida (`_correr_paso` está diseñado para que un paso roto avise y siga, no para frenar el job), esa noticia queda sin `embedding` y sin cluster posible para siempre —`agrupar_pendientes` exige el embedding—, y a los 7 días la purga le habría borrado el cuerpo igual: el único insumo del que sale ese embedding, justo cuando más hace falta para reintentar. Se agregó `Noticia.embedding.is_not(None)` a la condición. **Costo cero sobre lo ya purgado**: auditado contra la base real antes de tocar el código, 0 filas en ese estado.

**El hallazgo de alcance ya ejecutado: 238 de las 4.083 filas purgadas eran notas sin hecho** (111 opinión, 80 recetas, 46 horóscopo, 1 juegos) — las que `categorias.py` excluye del agrupamiento a propósito y que ese mismo módulo promete conservar disponibles para que el back-end decida qué hacer con ellas. Nunca pueden agruparse, así que son huérfanas para siempre y quedaron adentro del alcance de "solo huérfanas" sin que nadie lo pensara como una decisión aparte. **Se evaluó y se decidió no revertir**: el back-end nunca leyó `contenido_limpio` (`GET /search` no lo devuelve, y no hay otro consumidor), así que la letra de la promesa de `categorias.py` sigue en pie aunque el espíritu no se haya discutido a tiempo. Queda anotado acá para que la próxima vez que se toque el alcance de esta purga, se decida a propósito y no por default. El backup (`pre_purga_20260904.dump`) sigue disponible si en algún momento se necesitara revertir específicamente esas 238 filas.

**Las cuatro correcciones menores, todas aplicadas:**

- **Los "bytes" contaban caracteres, no bytes.** `func.length()` de Postgres sobre `text` cuenta caracteres; con acentos y eñes de sobra en español, subestimaba el texto real liberado (verificado: `"ñññ"` da 3 con `length` y 6 con `octet_length`, tanto en Postgres como en el SQLite de los tests). Cambiado a `func.octet_length`. Los `16.246.730` que quedaron documentados arriba, de la corrida real, están subestimados por este motivo — no se recalculan retroactivamente porque el texto que los generó ya no existe para volver a medirlo.
- **`purgadas` se copiaba del `SELECT COUNT` en vez de leer el `rowcount` real del `UPDATE`.** Son la misma condición evaluada dos veces con una ventana de tiempo en el medio; una purga concurrente sobre alguna de las mismas filas haría que el conteo previo sobrestimara lo que esta corrida tocó de verdad. Ahora `purgadas` sale de `resultado_update.rowcount`. `bytes_liberados` se queda atado al `SELECT` de arriba a propósito —no hay forma de medir el largo de un texto después de blanquearlo—, así que en el caso raro de una purga concurrente esa cifra queda como aproximación mientras `purgadas` es exacto.
- **El comentario de `_limite_de_purga` comparaba 7 contra 12 en vez de 168 contra 12** (le faltaba el `*24` en la comparación, aunque el código sí lo tenía). Corregido en el código y en el docstring de la clase de test que lo ejercita — es exactamente el comentario que alguien podría usar para "simplificar" el código mal el día de mañana.
- **La migración `5a246beb14bd` afirmaba que había dos consultas usando el índice; hay una sola.** Corregido el docstring, y se anotó que con ~30% de la tabla en `NULL`, Postgres probablemente prefiera un seq scan para ese `IS NULL` de todos modos — no se creó un índice parcial sin medir primero que haga falta, siguiendo la misma regla de "medir antes de resolver" del resto del backlog.

**Cobertura agregada junto con las correcciones**: un test que prueba que una huérfana sin `embedding` no se purga; uno que confirma el conteo en octetos y no en caracteres; uno que fuerza (con un `session.exec` espiado) que `rowcount` difiera del conteo previo y confirma que `purgadas` sigue al primero; dos tests de punta a punta contra `POST /purge` **sin mockear el servicio** — cerraban un hueco real: los tres tests previos del endpoint mockeaban `purgar_cuerpos_vencidos` entero, así que nada probaba la garantía de `solo_contar=true` a través del camino HTTP completo; y `test_corre_los_ocho_pasos` pasó a verificar también el ORDEN de ejecución (que la purga corre último), no solo que cada paso se llamó una vez — antes hubiera pasado igual si alguien la movía al principio del job.

**Verificación**: 712 tests (5 nuevos), `ruff` limpio, `alembic check` sin operaciones pendientes. **4 mutaciones y 4 detectadas** sobre las cuatro correcciones —sacar el chequeo de embedding, volver a `length`, volver a usar `evaluadas` como `purgadas`, y mover la purga al principio del job—. Confirmado con un dry-run contra Postgres real después de aplicar todo: `{"evaluadas": 0, ...}`, consistente con que ya no queda nada pendiente de purgar desde la corrida del punto anterior.


### Backlog punto 6-bis: multimodelo — un modelo por cluster, y una cadena que no pierde la corrida (05/09/2026)

**Cierra 6-bis en su mitad reactiva.** El motor pasa de sintetizar todo con un modelo a poder elegir uno por cluster, y de perder la corrida cuando el proveedor falla a caer al siguiente.

#### El reencuadre que lo destrabó

Este punto estuvo frenado desde el 21/08 por un argumento que era correcto pero de alcance más chico del que aparentaba: *"la cadena no termina en «si falla, probá el siguiente»; para que sirva de verdad hay que decidir cuánto mandarle a cada proveedor según los créditos que le queden"*.

Eso es cierto del **reparto proactivo** de carga, y no del **fallback reactivo**. Caer al siguiente cuando el primero ya falló no necesita saber cuánto crédito queda: la información llega sola, en forma de error. Separadas las dos mitades, la reactiva se podía hacer sin tocar la pesada — que sigue sin hacer falta.

#### La mitad del trabajo ya estaba hecha

Leer el código antes de planificar cambió el tamaño del punto. Ya existían, sin que hiciera falta migración ni esquema nuevo:

- `sintetizar_cluster(session, cluster, modelo)` **ya recibía el modelo por parámetro**, con un centinela `_RESOLVER` puesto justamente para poder pasarlo resuelto.
- `leer_api_key` **ya aceptaba la forma con sufijo** (`MODELO_API_KEY_GROQ`), con un comentario que decía "es lo que va a necesitar el punto de multimodelo".
- `Sintesis.modelo_usado` ya guardaba qué modelo produjo cada síntesis, y **se actualiza en una re-síntesis**.
- `modelo_activo` ya toleraba varias filas activas y desempataba de forma determinista.

Lo que faltaba era chico: que el alta aceptara el campo, armar la cadena, recorrerla, y exponerlo por API.

#### Las decisiones

**`activo` sigue significando "el default desatendido".** Hay uno solo, `_apagar_los_demas` se queda, y el scheduler no se enteró del cambio: sigue llamando a `sintetizar_pendientes(session)` sin parámetros. Se evaluó la alternativa —varios activos más una columna `es_default`— y se descartó: cambia el significado de un endpoint que ya está en uso y pide migración, a cambio de nada que esto necesite.

**La cadena la forman el activo y los suplentes con credencial propia**, no todos los modelos dados de alta. El criterio no es un proxy de la intención del operador: es lo único que hace que el suplente sirva. Un modelo que comparte `api_key_env` con el titular comparte su credencial y por lo tanto su **cuota** — caer de Gemini a Gemini no resuelve nada cuando lo agotado es la cuota de Gemini. Como efecto, configurar la variable con sufijo **es** el opt-in, y no hace falta una columna que diga quién es suplente.

**El activo encabeza aunque su `prioridad` sea peor.** `prioridad` ordena a los suplentes entre sí; si pudiera adelantarse al activo, prender un modelo dejaría de significar algo.

**El cortocircuito, y por qué son dos fallos y no uno.** Un modelo que falla sale de la cadena por lo que queda de la corrida. Sin eso, con la cuota del titular agotada cada cluster paga sus 3 reintentos de `tenacity` con espera creciente hasta 30 s antes de caer al suplente: con 26 clusters, minutos de sleeps puros por corrida. Es el mismo modo de falla que ya se había corregido una vez, cuando `SintesisSinConfigurar` dejó de reintentarse.

Dos y no uno porque un fallo suelto puede ser un JSON mal armado de ese cluster puntual, y sacar al titular por eso cambiaría de proveedor —y con él lo que queda escrito en `modelo_usado`— por una casualidad. El costo del segundo intento está acotado: ~60 s en el peor caso, sobre un ciclo de 15 minutos. `SintesisSinConfigurar` es la excepción y agota de una, porque una credencial que falta no se arregla entre un cluster y el siguiente.

**Qué cae al siguiente y qué no:**

| Fallo | ¿Cae? | Por qué |
|---|---|---|
| Rate limit u otro error del proveedor | Sí | Es el caso que la cadena existe para cubrir |
| `SintesisSinConfigurar` | Sí, y agota de una | Antes cortaba la corrida entera; con cadena significa "usá el siguiente" |
| `SintesisBloqueada` | **No** | El proveedor rechazó el contenido por sus filtros. Buscar otro que sí lo acepte es rodear una negativa de seguridad, y además destruye la señal: su propio docstring dice que si pasa seguido, lo que informa es que el producto no puede cubrir cierto material, y eso es una decisión de producto |

**Un `modelo_id` explícito apaga la cadena.** Si alguien eligió un modelo, caer en silencio a otro contradice la elección, y dejaría en `modelo_usado` una serie histórica que dice que se usó uno que nadie pidió — justo la comparación que esa columna existe para habilitar. Por lo mismo, un `modelo_id` que no existe es **404 y no se sintetiza**: caer al default gastaría cuota del proveedor equivocado.

**Concurrencia: secuencial.** Se evaluó un hilo por modelo y se descartó por ahora, con dos razones medidas. Ninguno de los dos modos de uso corre dos modelos a la vez —"paso a paso" es un cluster y un modelo; "todas con el default" es un solo modelo—, y la síntesis de 24 ángulos usa el 23% del ciclo de 15 minutos. Además el costo es concreto: la `Session` de SQLAlchemy no es thread-safe, así que cada hilo necesitaría la suya, y el `expunge` anti-N+1 y el commit por cluster habría que rediseñarlos. Se retoma con el síntoma, no antes.

#### Dos hallazgos del camino

**Una regresión propia, cazada por los tests que ya estaban.** Al reescribir el bucle, el cluster que destapaba el agotamiento de la cadena quedaba contado como `fallido`, cuando lo que pasó fue que el motor se quedó sin proveedores. Es el mismo diagnóstico engañoso que este archivo ya documenta dos veces —"apunta a un problema con los clusters cuando el problema es la configuración"— y lo agarró el test que se había escrito la primera vez.

**Un agujero preexistente, más serio: la suite podía gastar la cuota real.** `test_synthesis.py` no aislaba el `.env` (solo lo hacía `test_modelos.py`, en su propio archivo), así que un test que llegara a `llamar_modelo` de verdad leía la credencial del desarrollador. Lo destapó un parche mal puesto de esta misma tanda: el test le pegó a Gemini y falló con un 404 **del proveedor**, o sea que la llamada salió. En un proyecto con límite de costos duro eso no puede depender de que ningún parche se equivoque.

Se agregó `conftest.sin_credencial_de_ia`, `autouse` para toda la suite, que cierra las dos puertas por las que entra la credencial: el entorno del proceso y el `.env` del directorio actual (`_del_entorno` mira las dos). Mismo criterio que `api_sin_token`, que ya existía por un problema de la misma familia.

#### Verificación

756 tests (44 nuevos), `ruff` limpio, `alembic check` sin operaciones pendientes — esta tanda no toca el esquema.

**15 mutaciones y 15 detectadas.** Dos no se detectaban en la primera pasada, y las dos eran informativas:

- **El validador de `api_key_env` en el alta** es indetectable desde el endpoint, porque `leer_api_key` vuelve a validar dentro del sondeo y sacarlo da exactamente el mismo 422. Es una capa redundante a propósito —lo que aporta es no depender de un efecto secundario del sondeo— así que se le escribió un test que la ejercita **en aislamiento**, sobre el modelo Pydantic, donde la otra capa no la tapa.
- **La fixture que impide gastar cuota** no la cubría nada, porque solo actúa cuando un parche está mal puesto. Ahora hay dos tests que comprueban directamente que ninguna credencial queda visible durante la suite.

**Lo que NO está verificado, dicho de frente: la cadena nunca corrió contra dos proveedores reales.** Hace falta una segunda credencial de un proveedor distinto en `MODELO_API_KEY_<SUFIJO>`, y hoy no hay ninguna configurada. Todo lo de arriba está probado contra mocks y con mutación, que prueba que la lógica hace lo que dice — no que el segundo proveedor conteste. Es lo primero que hay que hacer el día que aparezca esa credencial, y conviene hacerlo sobre **un cluster puntual** con `POST /clusters/{id}/synthesize`: es una llamada al proveedor, no veintiséis.


#### Corrección: el punto 6-bis publicaba el nombre de la variable de entorno (05/09/2026)

Una revisión independiente con otro modelo, antes de commitear, encontró que la tanda anterior **reabría por la puerta de al lado la fuga que la tanda 2 había cerrado**. Verificado corriendo sondas contra `TestClient`, no deducido.

**Lo que salía, y en un 200:**

```
POST /synthesize -> 200
{... "agotados": {"titular-groq": "configuracion: titular-groq: La variable
 'MODELO_API_KEY_GROQ' no está definida o está vacía. …"}}
```

`agotados` es un campo nuevo de 6-bis y llevaba el mensaje entero de `ProveedorNoConfigurado`, que nombra la variable de entorno. El mismo dato salía por el `detalle` del 422 de `POST /clusters/{id}/synthesize`.

**Por qué importa, y por qué es peor de lo que parecía.** Es exactamente lo que `_vista_publica` filtra de `GET /modelos` desde la tanda 2, con el mismo modelo de amenaza: quien sabe qué variable nombrar puede dar de alta un modelo con `base_url` propio y `api_key_env` apuntando ahí, y el motor le entrega la credencial del operador durante el sondeo. Con `API_TOKEN` sin definir —configuración soportada y documentada— lo lee cualquiera que alcance el puerto. Y a diferencia del 422, el 200 **no requiere provocar ningún error**: es el endpoint que menos sospecha levanta.

**Un tercer camino, preexistente:** el mensaje del placeholder interpolaba el **valor** de la variable (`base.py`), y ese mensaje llega al 422 de `POST /modelos`. Que un placeholder empiece con `tu_` acota el daño pero no lo cierra — es una convención de nombres, no una garantía.

#### El arreglo, en la frontera y no en cada consumidor

Tres cambios, todos sobre el mismo principio, que además ya estaba escrito en este repo: **al log lo que sirve para diagnosticar, a la respuesta lo que se puede decir sin abrir una puerta.** Es la regla que `modelos.sondear` aplica y documenta para el cuerpo del proveedor; a esta rama se le había escapado.

1. **`llamar_modelo` sanea al convertir la excepción.** `ProveedorNoConfigurado` se loguea entero y se re-levanta como un mensaje que dice qué modelo falla y que el detalle está en el log. Se separó de `AdaptadorNoImplementado`, que **sí viaja entero** y no es una excepción a la regla: su mensaje dice qué adaptador falta y qué usar en su lugar, sin nombrar variables ni configuración del operador.
2. **`agotados` lleva categorías cerradas** (`sin_configurar`, `fallos_seguidos`) en vez de texto libre. Sanear la frontera ya cerraba la fuga; esto hace que el campo **no pueda volver a filtrar** por un mensaje que mañana se vuelva sensible, y de paso es lo que una interfaz necesita para mostrar el motivo sin parsear prosa.
3. **El valor de la variable no se interpola nunca**, ni siquiera el del placeholder. El operador sabe qué puso ahí; no hace falta devolvérselo.

#### Los dos huecos de método que lo dejaron pasar

**El invariante no estaba testeado donde hacía falta.** "Ninguna respuesta publica `api_key_env`" tenía tests para `GET /modelos` y `POST /modelos`, y para ningún otro endpoint. Por esa grieta entraron los dos caminos. Ahora hay una clase de tests que lo verifica **sin mocks**, sobre el camino real, más uno que comprueba la otra mitad: que el detalle completo **sí** siga estando en el log, porque sacarlo de la respuesta no puede costar el diagnóstico.

**Y un test que aparentaba cubrir el camino.** `test_sin_configurar_es_422_y_no_500` inyecta un mensaje inventado y benigno y asertaba sobre él, así que pasaba en verde mientras el mensaje real filtraba. Es el caso de "mock que tapa el camino real": verificaba el código de estado y parecía verificar el contenido. Se le sacó la aserción sobre el mensaje y se le escribió en el docstring qué cubre y qué no, con el puntero a los tests que sí lo cubren.

#### Verificación

761 tests (5 nuevos), `ruff` limpio. **3 mutaciones y 3 detectadas**: devolver el mensaje entero a `agotados`, sacar el saneo de la frontera, y volver a interpolar el valor de la variable. La tercera no se detectaba con los tests nuevos —nada ejercitaba el camino del placeholder por HTTP— y se le escribió el suyo antes de darla por cerrada.

Corregido además el conteo de tests de la entrada anterior, que decía 746: son 756, y la aritmética del propio archivo lo delataba (712 + 44).

#### El patrón de fondo: tests que pasan porque el mock les da la respuesta (05/09/2026)

La fuga de arriba no se escapó por falta de tests: se escapó porque **el test que cubría ese camino mockeaba el servicio que estaba probando**, así que el mensaje sobre el que asertaba lo ponía el propio mock. Pasaba en verde con la fuga abierta.

Se revisó el patrón en toda la suite de endpoints en vez de arreglar solo ese caso.

**El relevamiento salió mejor de lo temido.** De los 50 tests de `test_api.py`, la enorme mayoría son de **cableado** —"¿le llega el parámetro al servicio?", "¿devuelve sus stats?", "¿un id imposible es 422 sin llamar a nadie?"— y ahí mockear es correcto **y completo**: lo que el test promete es exactamente lo que puede probar. Solo tres asertan sobre el contenido de la respuesta, y de esos uno era el que fallaba.

**El hueco real era otro, y estructural.** `TestCadenaDeFallback` mockea `sintetizar_cluster` entero. Es la decisión correcta para probar la lógica de la cadena —qué cae al siguiente, qué agota un modelo, qué corta la racha— pero por eso mismo **nunca ejercita** el camino que va de `leer_api_key` a `ProveedorNoConfigurado` a `SintesisSinConfigurar`, que es justamente donde se construía el mensaje que filtraba. La cadena estaba probada como lógica y no como integración.

Se agregó `TestLaCadenaConTraduccionRealDeExcepciones`, donde el único mock es la frontera de red: resolver la credencial, levantar, traducir, caer y sanear son el código real. **Verificado que sirve**: se reintrodujeron las dos mutaciones de la fuga y las cazó las dos, o sea que ahora hay dos capas independientes que la ven.

**Un hallazgo del propio test.** El primer intento montaba dos modelos con variables inexistentes para forzar dos fallos de credencial, y no entró ninguno a la cadena: `_tiene_credencial_propia` filtra a los suplentes cuya variable no resuelve, así que **"un suplente sin credencial" es un estado que no existe**. La cadena solo puede tener suplentes que sí pueden autenticarse. Quedó escrito en la fixture, porque es una propiedad del diseño que no era evidente.

**Y las clases mockeadas ahora dicen hasta dónde llegan.** No se desmockeó nada que estuviera bien mockeado —eso habría sido cambiar tests correctos por tests lentos— pero cada clase apunta a dónde se prueba lo que ella no puede probar. Un test que dice qué cubre vale más que uno que aparenta cubrir todo.

#### Las seis afirmaciones pendientes, verificadas — y las dos que se arreglaron (05/09/2026)

La tanda del 6-bis cerró con **seis afirmaciones de la revisión independiente sin verificar**, dicho de frente en el commit: después de que dos de diez no resistieran, ninguna merecía crédito hasta comprobarla. Se comprobaron las seis.

**El paso cero fue recuperar el texto literal**, y no es ceremonia. Las seis estaban resumidas por mí, no en las palabras del revisor — y una de las dos que se había caído lo hizo justamente por un encuadre mal parafraseado. Verificar sobre la paráfrasis era arriesgarse al mismo error, así que se fue a buscar el reporte original al transcript de la sesión antes de tocar nada.

**El método: cada afirmación se resuelve por algo que corre**, o por lectura contra la fuente cuando la pregunta es de criterio y no de comportamiento — diciendo cuál de las dos fue. Es la contracara del error que hizo caer a las dos primeras: el revisor había probado lo que su propio mock devolvía.

| Afirmación | Cómo se resolvió | Estado |
|---|---|---|
| Amplificación de costo en `/clusters/{id}/synthesize` | Sonda con contador | **Confirmada**: 5 POST = 5 llamadas al proveedor |
| Reset de `enviado_backend`/`intentos_envio` | Lectura + sonda | **Confirmada, y deliberada** — el comentario de `_persistir` ya la explica |
| Re-síntesis de un cluster `descartado` | Sonda | **Confirmada**: 200, y la síntesis queda lista para entregar |
| `ErrorDeProveedor` → 500 | Sonda | **Confirmada, y peor**: no había ni handler genérico |
| `POST /modelos` como oráculo de variables | Sonda comparativa | **Confirmada**, impacto bajo |
| Siete comentarios desactualizados | Lectura mecánica | **Confirmados los siete**, palabra por palabra |

**Y una séptima que se reabrió.** El hallazgo del "modelo apagado que entra a la cadena" se había descartado por encuadre equivocado, y al releer el reporte literal resultó que **el descarte respondía a una versión más débil de la afirmación que la que el revisor había corrido**. Lo que no significa nada es que un no-titular esté en `activo=False` — eso es cierto por construcción, porque `_apagar_los_demas` garantiza como mucho un activo. Lo que sí importa es lo otro: si el operador reemplaza un titular que resultó malo prendiendo otro, el viejo **vuelve solo a la cadena como suplente** si tiene credencial propia, contradiciendo el docstring de `activar_modelo`, que promete que apagar es la marcha atrás. Verificado con sonda. Queda anotado, sin arreglar todavía: cambiar qué significa `activo` es una decisión de producto y merece su propia discusión.

Lo que **no** se pudo verificar sigue sin verificarse, y se dice: la sub-afirmación de que las re-síntesis duplican ángulos depende de si el modelo obedece la instrucción en prosa del prompt —`construir_prompt` sí le manda los `id` de los ángulos existentes— y eso no se prueba con un mock que ya decidió desobedecerla.

#### Corrección 1: `POST /modelos` era un oráculo de qué variables existen

`OpenAICompatible.__init__` leía la credencial **antes** de validar el host. Como una variable inexistente cortaba ahí, el mensaje de error revelaba si esa variable existía en el servidor **sin necesidad de un host permitido**: bastaba con nombrarla. Dos 422 distinguibles, corridos y comparados.

Se invirtió el orden. Ahora cualquiera que pruebe con un host no confiable —el único caso que le sirve a quien ataca, porque exfiltrar necesita ese host igual— recibe siempre el mismo "host no declarado", exista la variable o no. Un operador legítimo, con su host ya declarado, sigue viendo el mensaje específico de credencial faltante: **no se le sacó diagnóstico, se le sacó al desconocido**.

Se evaluó la alternativa —un mensaje genérico en el 422— y se descartó: cierra el síntoma dejando el orden intacto, y le cuesta al operador la pista de cuál de los dos problemas tiene.

**El arreglo destapó un tercer hueco de aislamiento en la suite.** `test_synthesis.py` construye modelos con `base_url="https://proveedor.test/v1"` y **nunca declaraba `MODELO_HOSTS_PERMITIDOS`**. Hasta el reorden nunca se notó, porque la credencial fallaba primero y el chequeo de host no llegaba a correr. Invertido el orden, el resultado de ese test pasó a depender de lo que hubiera en el `.env` real de la máquina. Es la misma familia que `sin_credencial_de_ia` y `api_sin_token`, que nacieron las dos de un problema idéntico: **un test no puede depender de una variable que nadie en ese archivo pidió**. Se le agregó al archivo su propia fixture `hosts_declarados`, como la que `test_modelos.py` ya tenía.

#### Corrección 2: un 429 del proveedor terminaba en un 500 sin manejar

`llamar_modelo` traducía todo `ErrorDeProveedor` a un `ValueError` pelado para que `tenacity` lo reintentara. `sintetizar_pendientes` lo atrapaba igual —su `except Exception` no distingue—, pero `POST /clusters/{id}/synthesize` solo atajaba `SintesisSinConfigurar` y `SintesisBloqueada`. Resultado: **un rate limit del proveedor, que es la condición más esperable de ese endpoint, salía como un 500 sin cuerpo** — justo lo que este archivo ya documentó al fijar `MAX_ID` que un 500 no puede significar.

Se agregó `SintesisFallida`, que es lo que queda de un `ErrorDeProveedor` cuando los tres intentos se agotaron, y el endpoint la traduce a 422. **El retry no cambió**: la clase nueva tampoco está en `retry_if_not_exception_type`, así que se sigue reintentando exactamente igual; lo único que cambia es qué queda cuando los reintentos terminan.

Se evaluó atrapar `ValueError` a secas en el endpoint —una línea en vez de una clase— y se descartó por lo de siempre en este repo: taparía un bug propio que levante `ValueError` por otro motivo y lo reportaría como "el proveedor tuvo un problema", que es un diagnóstico que manda a buscar el error al lugar equivocado.

#### Verificación

768 tests (5 nuevos), `ruff` limpio, `alembic check` sin operaciones pendientes.

**2 mutaciones y 2 detectadas**: revertir el orden en `OpenAICompatible.__init__` (lo caza el test nuevo del oráculo, levantando la excepción equivocada) y sacar el `except SintesisFallida` del endpoint (lo caza el test de endpoint, con el 500 sin manejar de vuelta). Las dos se restauraron y se re-corrió la suite entera después.

Las sondas de las seis afirmaciones se re-corrieron contra el código ya arreglado: el oráculo devuelve mensajes idénticos para variable existente e inexistente, y el endpoint devuelve 422 con los tres reintentos de `tenacity` corridos de verdad.

**Un error propio, encontrado revisando el diff antes de commitear.** Al insertar el test nuevo, un `Edit` empujó la línea `mock.assert_not_called()` del test vecino hasta el final del mío; al ver el `NameError` se la tomó por un resto de copy-paste y se la borró, sacándole en silencio una aserción a un test que ya existía. Lo delató `ruff` con un `F841` sobre una variable que quedó sin usar, y el `git diff` contra `HEAD` mostró qué había pasado. Queda como recordatorio de por qué el diff se lee antes de commitear y no después.

#### Lo que la realidad dijo sobre el multimodelo (05/09/2026)

La cadena de fallback sigue **sin probarse contra dos proveedores reales**, y ahora se sabe por qué con datos y no por falta de intentos:

- **`groq-qwen` no sirve en el tier gratuito.** Con el host declarado y la credencial válida, el sondeo pega contra un techo de **1000 tokens de salida por minuto** para `qwen/qwen3.6-27b`. Con `max_tokens` acotado a 900 el primer mecanismo devuelve un JSON inválido —no le alcanza para armarlo— y el segundo ya no entra en la cuota del mismo minuto. Una síntesis real necesita bastante más que eso.
- **`gemini-3.8-flash` está saturado del lado de Google**: `503 UNAVAILABLE` en 3 de 3 intentos a lo largo de varios minutos, con dos credenciales distintas. Que el problema es del modelo y no de la cuenta quedó aislado con un control: la misma credencial nueva sondeó bien contra `gemini-3.5-flash-lite`, y el titular con la credencial vieja respondió al primer intento en el mismo momento.

Como efecto de la prueba, `gemini-3.8-flash` quedó apuntando a `MODELO_API_KEY_GEMINI2` en vez de compartir la variable del titular — que era lo que lo dejaba fuera de la cadena por `_tiene_credencial_propia`. Está cargada y lista para el día que Google libere capacidad. **No hay endpoint para cambiar `api_key_env` de una fila ya creada**: el `PATCH` solo acepta `activo`, así que se hizo por SQL directo. Vale anotarlo como hueco de la API, no como decisión.

#### Una trampa de sesión que el `expunge` no cubre

Una corrida completa del pipeline a mano falló en la síntesis con `DetachedInstanceError`, y **reproducida en aislamiento** resultó ser una precondición que nadie había escrito: `sintetizar_pendientes(session, modelo)` no tolera un `ModeloIA` cargado **antes** de otros pasos que commitean. Con `expire_on_commit=True` ese objeto queda expirado, y el `expunge` de adentro —que existe para evitar el N+1— lo desprende *sin valores*, así que el primer acceso a un atributo revienta.

Ningún camino de producción la pisa hoy: `POST /synthesize?modelo_id=` carga el modelo justo antes de llamar. Pero **el caso de uso para el que se construyó 6-bis sí la pisaría**: una app de escritorio que orquesta pasos y después elige modelo es exactamente un llamador que sostiene ese objeto a través de commits. El propio código ya advierte de esta trampa para su orden interno (`synthesis.py`, "es la misma trampa que ya está documentada más arriba"), pero la advertencia no cubre la precondición del llamador. Es la tercera vez que esta trampa aparece en el repo.

#### La corrida completa, con los números

Corrida real de punta a punta con el modelo titular, **sin el paso de entrega al back-end**, para tener números frescos:

| Paso | Resultado |
|---|---|
| Ingesta | 363 noticias nuevas de 7 medios, 30 duplicadas, 0 feeds fallados — 72,1 s |
| Vectorización | 363 de 363 — 11,4 s |
| Cierre de vencidos | 63 evaluados → 53 procesados, 10 descartados |
| Agrupamiento | 27 clusters creados, 158 sin match |
| Fusión | 27 evaluados → 1 fusionado |
| Síntesis | **26 de 26**, 30 ángulos creados, 0 fallidos, 0 bloqueados, 0 descartados |
| Purga (solo contar) | 1 huérfana, 1.711 bytes, 0 purgadas |

**Las mediciones viejas se sostienen**, que es lo que esta corrida venía a comprobar: **9,2 s por cluster** contra los ~8,7 s que este archivo ya tenía anotados, y **26,7% del ciclo** de 15 minutos para 26 clusters contra el 23% que se había medido para 24. La estimación con la que se descartaron los hilos por modelo era buena.

`agotados` quedó vacío y `por_modelo` en `{'gemini-por-defecto': 26}`: la cadena **no se activó ni una vez**, así que esta corrida no dice nada sobre el fallback. Lo único que probó es que el titular solo alcanza.

#### La guarda contra la amplificación de costo del endpoint por cluster (05/09/2026)

Primero de los tres efectos que la verificación de hallazgos había confirmado y dejado sin arreglar. Los otros dos —el reset de `intentos_envio` y la re-síntesis de un cluster `descartado`— siguen abiertos.

**El hallazgo, medido con sonda:** 5 POST a `POST /clusters/{id}/synthesize` daban **5 llamadas reales al proveedor**, contra **1** de `POST /synthesize` con los mismos 5 POST — porque el barrido filtra por `clusters_pendientes` y el endpoint por cluster no filtraba por nada. Entre recibir el request y llamar al modelo no había ninguna guarda: ni de frecuencia, ni de estado, ni de "esto ya se sintetizó recién".

Y no era negligencia: el docstring decía que saltear `clusters_pendientes` era a propósito, *"ese filtro existe para que el barrido no gaste de más, pero acá hay alguien eligiendo"*. El razonamiento vale **para una persona en una app de escritorio**. El problema es que hoy no hay app: hay un endpoint HTTP, y con `API_TOKEN` opcional —configuración soportada— un doble clic, un reintento por timeout o un script en bucle gastan cuota sin que nadie haya decidido gastarla.

#### La guarda: una de las dos condiciones del barrido, no las dos

`clusters_pendientes` exige dos cosas. Acá se reusa **solo la primera**:

1. **¿Llegaron noticias desde el último intento?** — sí se usa. Es exactamente la pregunta que separa "repetir con la misma entrada" de "hay algo nuevo que sintetizar". Cuando se repite, la evidencia y el prompt son idénticos, así que la llamada no puede aportar nada que la anterior no haya aportado.
2. **¿El material sin ángulo alcanza para un ángulo nuevo?** — **no** se usa. Ésa es la condición económica del barrido, y aplicarla acá rompería el caso que este endpoint existe para habilitar: re-sintetizar un ángulo que ya existe con otro modelo, donde no hay material nuevo ni se busca un ángulo nuevo.

La condición se extrajo a `synthesis.hay_material_nuevo(cluster)`, que ahora usan los dos lados. Es el mismo criterio de `_tiene_credencial_propia`: **una sola pregunta no puede tener dos respuestas posibles** según quién la haga.

#### `forzar=true`, y por qué esa palabra y no otra

Con la guarda sola quedaba bloqueado el caso que el propio docstring promociona —*"volver a sintetizar con otro modelo algo que ya salió"*—, así que hace falta una salida explícita. Se evaluaron dos:

- **`?forzar=true`**, elegida. Reusa el vocabulario que la API ya tiene en `POST /deliver?forzar=`, no inventa un parámetro, y deja una sola palabra como línea entre "no gastes al pedo" y "sé lo que hago".
- **Que un `?modelo_id=` explícito implicara forzar.** Menos fricción para el caso promocionado, pero mezcla dos significados en un parámetro y reabre la amplificación entera para cualquiera que pase `modelo_id`. Descartada.

**Para una interfaz la fricción es cero**, que es lo que terminó de decidirlo: en el modo paso a paso, el botón "volver a sintetizar" **es** el acto explícito, así que la app manda `forzar=true` y listo. La palabra sobra solo para quien escribe la URL a mano — justo donde conviene que se note.

#### Dónde vive cada mitad, y qué contesta

**El predicado en el servicio, la política en el endpoint.** Decidir si vale la pena gastar una llamada es política de API, no un invariante de la síntesis; pero la pregunta que la sostiene tiene que ser compartida para que no diverja.

Cuando corta contesta **200 y no un 4xx**: no falló nada, el motor decidió no gastar, y un 4xx haría que una interfaz muestre un error ante una condición perfectamente normal. El cuerpo lleva `"sintetizado": false` y `"motivo": "sin_material_nuevo"` — categoría cerrada, mismo criterio que se adoptó para `agotados` después de la fuga.

**`sintetizado` viaja también en la rama que sí sintetiza**, y eso salió de escribir los tests: un campo que aparece solo a veces obliga a quien consume a escribir `.get("sintetizado", True)` y a saber cuál es el default. Con las dos ramas declarándolo, la respuesta se lee sola.

#### Lo que esta guarda NO cierra

Frena la repetición **accidental**, que es el modo de falla más probable. **No frena a alguien decidido**: con `forzar=true` en un bucle la amplificación vuelve entera. Ese vector es el de `API_TOKEN` opcional y se trata aparte — el roadmap ya tiene el precedente en el punto 9, donde se marca que algunos endpoints son candidatos a exigir token siempre y no solo cuando el operador lo activó.

#### Verificación

772 tests (4 nuevos), `ruff` limpio. **3 mutaciones y 3 detectadas**, y cubren las dos direcciones del error: sacar la guarda entera, ignorar `forzar`, y aflojar el predicado de `>` a `>=` — que no bloquea de más sino de menos, y se caza igual.

El test que importa no mockea `sintetizar_cluster` sino la frontera de red, porque lo que se prueba es **cuántas veces se llega de verdad al proveedor**: con el servicio mockeado, el conteo sería el del mock.

#### El efecto 3 no era lo que yo había dicho, y la corrección importa más que el arreglo (05/09/2026)

Segundo de los tres efectos de la amplificación de costo. **Lo primero que hay que decir es que mi caracterización anterior estaba mal**, y estaba mal por el mismo error de método que este archivo ya documenta cuando lo cometió un revisor externo.

**Lo que había afirmado:** que un cluster `descartado` podía sintetizarse y llegar al back-end, contradiciendo la regla que define el producto — sin dos voces no hay enfoques que comparar.

**Por qué era falso.** La sonda que lo "probaba" construía un cluster `descartado` **con dos medios**, y ése es un estado que el motor no puede producir. La cadena de hechos, leída en la fuente:

1. `descartado` se pone **solo** cuando `medios_distintos < MIN_MEDIOS_CLUSTER` (`cerrar_clusters_vencidos`). Un descartado tiene, por definición, menos de dos medios.
2. El agrupamiento solo asigna noticias a clusters **`abierto`** (`_cargar_clusters_abiertos`). Un cluster ya cerrado queda **congelado**: nunca puede sumar un segundo medio.
3. Por lo tanto un descartado tiene menos de dos medios para siempre, y `_persistir` descarta todo ángulo nuevo que no cubra el mínimo.

**Verificado con una sonda sobre un descartado realista** —un solo medio, dos notas de ese medio—: 1 llamada al proveedor, `creados: 0`, `descartados: 1`, **cero filas de `Sintesis`**. Las defensas aguantan en dos capas: `_comparativa_validada` tiró el medio alucinado que el modelo inventó, y `_persistir` descartó el ángulo por tener un solo medio.

Es exactamente el error que se le objetó al tercer par de ojos en la tanda anterior: **armar un setup que no puede existir y reportar lo que ese setup produce**. Que haya vuelto a pasar, del lado propio esta vez, es el argumento más fuerte a favor de la disciplina de reproducir cada hallazgo antes de darlo por bueno.

#### Lo que sí quedaba, más chico y real

> El endpoint gasta una llamada al proveedor en un cluster que **estructuralmente no puede producir nada**, y eso se sabe antes de llamar.

Dos cosas lo achicaban todavía más: la guarda del efecto 1 ya lo había acotado de N llamadas a **una** —la segunda queda bloqueada por falta de material nuevo—, y nadie lo pisa por accidente, porque hay que apuntarle por `id` a propósito. En la base real hay 87 clusters descartados y ninguno se sintetizó nunca.

Se arregló igual, y el criterio para hacerlo fue el que la skill `medir-antes-de-resolver` acaba de incorporar: **el arreglo es más chico que el problema**. Es una comprobación gratuita, con datos ya en memoria, que expresa una regla que el producto ya tiene.

#### La guarda: por medios, no por estado

Se comprueba **`medios_distintos < MIN_MEDIOS_CLUSTER`**, no `estado == descartado`. Cubre más con menos: además del descartado atrapa un `abierto` de un solo medio al que alguien le apunte por `id`, expresa la regla real en vez de un proxy, y no depende de que los estados se sigan usando como hoy.

**Es incondicional: `forzar=true` no la saltea.** `forzar` significa "re-sintetizá aunque no haya material nuevo", no "gastá en algo imposible". Dejarla pasar solo habilitaría desperdiciar la llamada a mano, sin ningún caso de uso detrás.

**Y no se pierde nada al cortar.** Un cluster que no llega al mínimo tampoco puede tener una síntesis previa que actualizar: para crearla habría necesitado el mínimo, y a un cluster no se le quitan noticias. Así que la rama de actualización de `_persistir` —la única que no aplica este filtro— no es una excepción a considerar.

El predicado vive en `clustering.alcanza_el_minimo_de_medios` y lo usan los dos lados: `cerrar_clusters_vencidos`, para decidir `procesado` contra `descartado`, y el endpoint. **Va en `clustering` y no en `synthesis` por la dirección de los imports**: `synthesis` ya depende de `clustering`, así que al revés sería circular.

#### Un fixture irrealista que la guarda destapó

Los cinco tests de cableado de `TestSynthesizeDeUnCluster` empezaron a fallar, y el motivo era del fixture y no de la guarda: `_cluster()` creaba un cluster **sin ninguna noticia**. Servía para probar el cableado porque el servicio estaba mockeado, pero es un estado que no puede producir nada, así que el endpoint ahora corta antes de llegar al mock. Se hizo el fixture realista —dos medios con una nota cada uno— en vez de aflojar la guarda.

Es la contracara del error de arriba: un setup imposible tapaba un hallazgo en un caso, y en el otro hacía pasar un test que probaba algo que no puede ocurrir.

#### Verificación

775 tests (3 nuevos), `ruff` limpio. **3 mutaciones y 3 detectadas**: sacar la guarda, hacer que `forzar` la saltee, y aflojar el predicado de `>=` a `>`.

Los tests cuentan **llamadas reales al proveedor** y fallan si hay alguna: el mock de `llamar_modelo` levanta `AssertionError` si lo tocan.

#### El efecto 2 se evaluó y NO se arregla, con el motivo escrito (05/09/2026)

Tercero y último de los efectos de la amplificación de costo. A diferencia de los otros dos, **se decidió dejarlo como está**, y queda documentado para que la decisión no haya que volver a tomarla desde cero.

**La afirmación era:** `_persistir` pone `enviado_backend = False` e `intentos_envio = 0` en toda actualización, así que repetir la re-síntesis reinicia el presupuesto de reintentos y `WEBHOOK_MAX_INTENTOS` deja de ser un techo real.

Literalmente cierto. Lo que estaba mal era el **alcance de la consecuencia**.

**Para qué existe el techo.** `entregar_sintesis` cuenta el intento haya salido bien o mal, y su docstring explica por qué: *"si el contador solo avanzara con el éxito, un back-end permanentemente caído nunca alcanzaría `WEBHOOK_MAX_INTENTOS` y el barrido lo reintentaría cada 15 minutos para siempre, sin que nadie se entere"*. Sus dos trabajos son **cortar el reintento silencioso** y **disparar la alerta**, que además va con `ignorar_cooldown=True` porque es terminal.

**Por qué el reset no lo rompe: está acotado a 12 horas.** La cadena, leída en la fuente:

1. El reset ocurre **solo** en la rama de actualización de `_persistir`, o sea cuando se actualiza un ángulo que ya existía.
2. Para que el barrido re-sintetice, `clusters_pendientes` exige material nuevo **y** noticias sin ángulo que cubran `MIN_MEDIOS_CLUSTER` medios. Las dos condiciones necesitan **noticias nuevas**.
3. Las noticias nuevas solo se asignan a clusters **`abierto`** (`_cargar_clusters_abiertos`).
4. `cerrar_clusters_vencidos` cierra a las `HORAS_CLUSTER_ABIERTO` (12 h).

Cerrado el cluster no entran noticias, no hay re-síntesis, el contador deja de resetearse, llega al techo y la alerta sale. Es la misma propiedad de "cluster congelado" que resultó decisiva para corregir el efecto 3.

**Y además no molesta, por tres motivos:**

- **La alarma es poblacional, no por síntesis.** Con el back-end caído, todas las que no se están actualizando llegan al techo igual y disparan el aviso. El operador se entera lo mismo.
- **No cuesta nada.** Son requests HTTP al back-end propio, no llamadas al proveedor de IA.
- **El comportamiento es defendible y no un descuido.** Si el cuerpo cambió es un payload distinto, y el back-end pudo haber rechazado el viejo y aceptar el nuevo. Darle presupuesto nuevo a contenido nuevo es lo correcto — que es exactamente lo que el comentario de `_persistir` argumenta.

Sumado a que la guarda del efecto 1 ahora exige `forzar=true` para repetir sin material nuevo, el bucle a mano dejó de ser gratis.

**Lo único que sí se pierde es observabilidad.** Volver a cero borra que esa síntesis ya había fallado antes, así que quien depure "por qué esto no se entregó" ve un contador que miente sobre el pasado. Si algún día molesta, el arreglo **no** es cambiar el comportamiento sino no perder el dato: un contador acumulado que nunca se resetee, o loguear el reset. No se hizo ahora porque agregar una columna para un problema que nadie tuvo es justamente lo que este repo decidió no hacer.

**El criterio con el que se cerró** es el que la skill `medir-antes-de-resolver` acaba de incorporar: antes de aceptar una mejora, estimar lo que cuesta y dejar que eso realimente si hace falta. Acá el síntoma no apareció, el daño está acotado y es gratis, y el comportamiento se sostiene solo. La mejora no se justifica.

---

Con esto **el bloque de amplificación de costo queda cerrado**: efecto 1 arreglado (guarda de material nuevo con `forzar`), efecto 3 corregido y arreglado (guarda de medios mínimos, incondicional), efecto 2 evaluado y descartado como defecto.

### La app de escritorio del operador: qué es, y las decisiones que la definen (06/09/2026)

Sesión de grillado completa antes de escribir una línea. Se documenta acá porque son decisiones estructurales que condicionan trabajo **en el motor**, no solo en la app.

#### El reencuadre: "escritorio" significaba otra cosa

El punto 6-bis dejó la interfaz fuera de alcance con la frase *"se construye cuando exista la app"*, y el plan original la había llamado "app de escritorio" contrastándola con *"una API que funciona alojada en un servidor y le pega a un back-end cada 15 minutos"*.

Ese contraste era **atendido contra desatendido** —alguien mirando y decidiendo, contra el scheduler corriendo solo—, **no nativo contra web**. La segunda pregunta nunca se había hecho, y el nombre venía arrastrando una decisión que nadie había tomado. Se hizo explícita, y la respuesta terminó siendo nativa igual pero por un motivo distinto al que el nombre sugería: **no querer hostear nada siempre activo**.

#### Lo que la app es

La cabina del motor, para un solo operador. Arranca como **sala de control de síntesis** y queda estructurada para crecer a la consola completa.

Eso último no es ambición: es un patrón que ya estaba en el backlog sin que nadie lo nombrara. Los puntos **2** (el modelo lo elige el operador), **3** (el alta de medios la hace el operador), **9** (el mail de alertas) y **11** (la URL del webhook) tienen todos la misma forma — *"esto lo decide el operador, no el `.env`"*. Hoy "decidir" significa editar un archivo y reiniciar un contenedor, o sea decidir de mentira. **La app es lo que vuelve real esa tesis.**

#### Maneja el motor, no lo empaqueta

La decisión más cara de la sesión, y se tomó con números medidos.

Empaquetar el motor en un instalador único significa **~2 GB antes de escribir una línea de interfaz**: el `.venv` de este repo pesa 1,8 GB, con `torch` en 527 MB (lo arrastra `sentence-transformers`), `scipy` en 115 MB y el modelo de spaCy en 52 MB, más los 458 MB del modelo de embeddings que se baja en la primera corrida. Y hay un costo peor que el peso: **habría que sacar pgvector**, migrando a algo empaquetable como `sqlite-vec` — riesgo puro sobre lo único del sistema que no tiene plan B escrito.

La alternativa elegida: la app **prende y apaga los contenedores que ya existen** y le habla a `localhost:8000`. Da exactamente lo que se pedía —local, sin hosting, sin nada siempre activo— y deja el motor tal como está, probado. Docker ya es dependencia de este entorno, así que no suma ninguna.

**El empaquetado no se pierde como aprendizaje**: el shell chico es justamente lo que se empaqueta, y ahí se aprende instalador, ícono, bandeja y actualizaciones peleando contra el problema real y no contra 2 GB de dependencias de ML.

#### Dos formas de cerrar, y por qué merece ser explícito

**Minimizar** deja el pipeline vivo ingiriendo y guardando; **cerrar** lo detiene. Es una elección del operador y no un efecto secundario de dónde se hace clic.

La razón está medida sobre las dos corridas del 05/09. Los feeds RSS no guardan historia, guardan las últimas N notas:

| Medio | Capacidad del feed | Nuevas por hora | Se da vuelta en |
|---|---|---|---|
| La Nación | ~90 | 23 | **~4 h** |
| TN | ~95 | 16 | ~6 h |
| Ciudad Magazine | ~66 | 2 | días |

**Apagado más de ~4 horas, se empieza a perder La Nación de forma permanente.** Ya pasó: el contenedor estuvo caído ~20 h y la primera corrida trajo 363 notas, prácticamente la capacidad completa de los siete feeds. Al ritmo medido de ~46/hora se habrían publicado unas 920, así que **se perdieron del orden de 550 notas** — estimado a partir de una hora de medición, no medido directamente.

#### El stack, y por qué no el que ya había

Se descartó **reusar el front público** (`Sin Ruido/front-end`, React + Vite + shadcn): es otro producto, con otro consumidor —le habla al back-end del otro equipo, no a este motor— y otro ciclo de vida.

Se eligió **Tauri**: la UI en React, que es terreno conocido; el shell en Rust es mínimo (lanzar `docker compose`, un ícono en la bandeja), así que no es aprender Rust; y el instalador y el updater vienen resueltos, que es lo que se quería aprender bien hecho. Se descartó **Electron** por peso (~150 MB para un shell) y **Python + pywebview + PyInstaller** porque, aunque unifica el lenguaje con el motor, PyInstaller en Windows no trae updater y enseña más sufrimiento que empaquetado.

**Solo Windows**, a propósito: es la máquina del único usuario, y multiplataforma sin un segundo usuario es costo sin demanda.

#### Vive dentro de este repo, contra la recomendación inicial

Se había recomendado un repo aparte, con un argumento real: `motor-noticias` es público y AGPL, describe un motor de servidor que otros pueden desplegar, y meterle una GUI personal de Windows le cambia lo que promete — sobre todo en `roadmap.md` y `change_logs.md`, que pasarían a mezclar decisiones del motor con decisiones de la interfaz.

**Ganó el repo único**, y el argumento que lo dio vuelta es el *version skew*: la app consume endpoints que el motor define, y en dos repos un cambio de API la rompe sin que nadie avise. En uno, el cambio y su adaptación caen en el mismo commit. Para un desarrollador solo eso muerde todos los días; el problema de identidad del repo muerde solo cuando lo lee otro, y hoy no lo lee nadie.

La objeción de CI —el motor corre `pytest` y `ruff` sobre Python, y Tauri mete Node y Rust— **se desarma con filtros por path**, que GitHub Actions soporta de fábrica.

**Tres condiciones** para que la convivencia no degrade el repo: CI separada por paths, los docs de la app en `app/` y no en `specs/`, y el contrato de endpoints que la app consume documentado y sostenido por un test — el mismo criterio con el que `webhook_contract.md` fija lo que el back-end espera.

#### Dos huecos del motor que la app destapó

Buscando qué le puede pedir hoy una interfaz al motor aparecieron dos faltantes, y ninguno es menor:

1. **El motor no sabe devolver sus propias síntesis.** De sus 16 endpoints, cuatro son `GET` y **ninguno devuelve un ángulo**: `listar_clusters` trae noticias y medios, no síntesis. Las síntesis salen del motor **solo empujadas por el webhook**. O sea que el modo paso a paso —elegir, sintetizar y **ver qué salió**— no se puede construir contra la API de hoy.
2. **El motor no sabe en qué anda.** `GET /` devuelve salud, base, entorno y hora; nada del scheduler. Y no hay tabla de corridas: `_job_ingesta_programada` mide duración y utilización del ciclo, lo loguea, y ahí muere.

#### Las decisiones sobre esos dos huecos

**`GET /sintesis` propio, en vez de engordar `GET /clusters`.** Son preguntas distintas —qué hechos hay y quién los cubrió, contra qué se produjo— y las síntesis tienen filtros que un cluster no tiene: si se entregó, con qué modelo, cuántos intentos lleva. Antecedente: `_vista_publica` ya estableció que no todo lo que hay en la fila sale por la API.

**Lista resumida más `GET /sintesis/{id}` para el detalle.** Una síntesis completa trae la comparativa entera, que son varios párrafos por medio; para una lista de veinte es demasiado. Es además la distinción que la pantalla necesita: una grilla para elegir y un panel para leer.

**Paginación por cursor sobre `(fecha_generacion, id)`, no `offset`.** Con offset, el scheduler insertando arriba cada 15 minutos hace que la página 2 repita ítems ya vistos. Se aceptó además que **las síntesis nuevas aparecen al refrescar** y no se inyectan en vivo, lo que simplifica la UI y deja el cursor solo para paginar hacia atrás.

**Tabla de corridas: una fila por corrida, con los pasos en `JSONB`.** Se descartó normalizar en `corrida` + `corrida_paso` porque las preguntas que existen son "la última" y "las últimas N", no "todas las veces que falló la vectorización" — y `JSONB` ya es el idioma de la casa (`puntos_clave`, `comparativa_enfoques`, `topicos`).

La razón para persistir va más allá de la app: ese número **ya se calcula y hoy se tira**. `medir-antes-de-resolver` dice que el número medido tiene que quedar donde justifica la decisión, y la decisión que justifica —¿subir el intervalo?, ¿ya hacen falta los hilos por modelo?— hoy depende de que alguien lea logs.

La contra que se aceptó: sacar series de adentro de un `JSONB` es incómodo. Quedó relativizada por un argumento del usuario que la debilita: la duración cruda depende de cuántos medios publicaron y cuánto, así que el número que serviría para graficar no es ése sino uno normalizado —segundos por cluster, hoy 9,2— y ése se deriva del JSON igual.

#### La v1, y las dos pantallas

La v1 hace cuatro cosas y ninguna es negociable hacia abajo: **ver los clusters con su estado y si ya tienen síntesis · sintetizar uno eligiendo modelo · leer lo que salió · ver en qué anda el pipeline**. Sacar cualquiera deja de ser una sala de control: sin ver el resultado no se decide nada, y sin el estado del pipeline no se sabe si lo que se mira está fresco.

**Dos pantallas cohesivas, una responsabilidad cada una**: la lista de trabajo (mirar, decidir, disparar) y el feed de lectura (los ángulos en tarjetas, paginado). Se descartó una sola pantalla que hiciera las dos: son ritmos distintos, y mezclarlos da una que hace las dos a medias — el mismo error que se evitó del lado del motor al decidir que `GET /clusters` no cargue los ángulos.

El feed nació de una pregunta del usuario —si servía una interfaz tipo Instagram— y la respuesta fue que encaja en la mitad de lectura y no en la de decisión: Instagram optimiza scroll sin fin ni orden estable, y una lista de trabajo quiere orden determinístico, filtros y una acción por fila.

#### Autenticación

El token se pega una vez y se guarda en el **Credential Manager de Windows**. Se descartó **no usar token** apoyándose en que la API liga a `127.0.0.1`: este repo ya decidió que el token existe, y saltearlo por comodidad sería desarmar una defensa que costó una tanda de auditoría. Se descartó también **leer el `.env` del motor**: es más cómodo y tiene el argumento de ser la misma máquina, pero mete ese archivo en el camino de un segundo programa — y es justamente el archivo que en este proyecto **ya se filtró una vez**, con una key de Groq que hubo que rotar.

Además es lo más portable en el sentido que importa. No hace que el secreto viaje —el Credential Manager es por máquina y por usuario, y eso es correcto— pero **no depende de ninguna ruta del disco**, que es lo que rompe la alternativa de leer el `.env`. Y el crate `keyring` mapea la misma llamada a Credential Manager, Keychain y Secret Service, así que el día que deje de ser solo Windows el código no cambia.


#### Los cuatro faltantes del motor, construidos (06/09/2026)

Paso 2 del punto 14: sin esto la app no se puede construir, porque no habría contra qué. Los cuatro salieron de buscar qué le puede pedir hoy una interfaz al motor y encontrar que **no podía pedirle ni lo que produce ni en qué anda**.

#### Leer las síntesis: `GET /sintesis` y `GET /sintesis/{id}`

Van **dos y no uno**: una síntesis completa trae la comparativa entera —varios párrafos por medio— y en una lista de veinte eso es pagar la lectura completa para tomar una decisión. Es la misma distinción que hace la pantalla: una grilla para elegir, un panel para leer. El resumen deja afuera `resumen_neutro`, `puntos_clave` y `comparativa_enfoques` a propósito, y hay un test que lo sostiene.

**El cursor es opaco** (base64 de `fecha|id`) y no un par de números legibles. Si se pudiera armar a mano, el criterio de orden quedaría congelado como parte del contrato; así, el día que cambie, cambia en un solo lugar.

**La comparación va desarmada** —`fecha < f OR (fecha = f AND id < i)`— y no como tupla, porque la suite corre sobre SQLite y producción sobre Postgres, y la forma con tupla no se comporta igual en los dos.

**Se pide un ítem de más** para saber si hay página siguiente sin contar la tabla entera; el de más no se devuelve, solo responde "¿hay más?".

**Un cursor mal formado es 422 y no 500**: es una entrada de quien llama, no algo que se rompió del lado del motor. Mismo criterio que la tanda 3 aplicó a todo lo que entra por la API, con su cota de largo incluida (`MAX_LARGO_CURSOR`).

De paso se corrigió el docstring de `search.py`, que decía ser solo búsqueda semántica cuando ya albergaba `listar_clusters`. Dejar un encabezado mintiendo es el problema de los siete comentarios que esta misma rama acababa de arreglar.

#### Saber en qué anda: la tabla `corrida` y `GET /pipeline`

**La fila se abre antes del primer paso y se completa al final.** Así una corrida que muere a mitad de camino igual deja rastro de que ocurrió — y de paso, `fin IS NULL` es la forma de saber si hay una en curso sin depender del estado en memoria de APScheduler, que se pierde en cada reinicio.

**Pero `fin IS NULL` solo no alcanza, y ese es el detalle que importa.** Si el proceso muere, esa fila queda abierta para siempre y el motor diría "corriendo" eternamente. Así que `corriendo` exige además que la corrida sea **reciente**, con el ciclo configurado como vara, y el otro caso se informa aparte en `huerfana` en vez de esconderse: una corrida abierta y vieja es el síntoma de que el proceso se cayó, y eso vale saberlo.

**`iniciar_corrida` devuelve el `id` y no la fila**, y es deliberado: entre la apertura y el cierre corren ocho pasos, y `_correr_paso` hace `rollback()` cuando uno falla. Sostener un objeto vivo a través de eso es exactamente la trampa del `expunge` que este archivo ya documentó dos veces y que costó un `DetachedInstanceError` en una corrida real. Un `int` no se expira.

**El registro no puede tumbar el pipeline.** `cerrar_corrida` atrapa lo que sea y loguea: que no se pueda escribir la contabilidad es un problema, pero hacerlo explotar hacia arriba convertiría un fallo de observabilidad en un fallo de producción.

**Anotar va adentro de `_correr_paso` y no en cada llamada**, que es lo que hace que un paso nuevo no pueda quedar fuera del historial por olvido. Y un paso que falla queda en `None`, que **no es un hueco**: `None` es "se intentó y no salió", ausente es "no llegó a correr". Son estados distintos y el historial tiene que poder separarlos.

#### Verificación

793 tests (18 nuevos), `ruff` limpio, `alembic check` sin operaciones pendientes. La migración se aplicó **contra la base real**: la tabla quedó con `pasos` en `jsonb` y su índice sobre `inicio`.

**6 mutaciones y 6 detectadas**, en dos tandas. Las de las síntesis: cursor por `offset`, la lista devolviendo el contenido completo, y el cursor inválido reventando en vez de dar 422. Las del registro: `corriendo` mirando solo `fin IS NULL` sin la vara del ciclo, un paso fallido que no se registra, y la fila que no se abre al principio.

**Un test propio que no probaba lo que decía.** `test_lo_que_entra_arriba_mientras_paginas_no_corre_la_pagina` —el que justifica haber elegido cursor sobre `offset`— **pasaba en verde con la mutación a `offset` puesta**. Dos causas, las dos del test: el helper nombraba los ángulos igual en cada tanda, así que la aserción por título no distinguía una repetición real; y las síntesis "nuevas" usaban la misma base horaria, así que no entraban realmente arriba. Corregido el helper, la mutación pasó a hacer caer los dos tests que le corresponden. Es el mismo patrón que esta rama viene persiguiendo, encontrado esta vez por la mutación y no por casualidad.


#### Primera revisión con `/revisar`, y los cuatro arreglos que salieron (06/09/2026)

Estreno de la skill sobre los endpoints del punto 14, contra `main`. Dos ejes en paralelo y aislados, cada uno con el diff capturado una sola vez y sus fuentes pegadas.

**El protocolo nuevo se midió contra el viejo.** La revisión improvisada del 05/09 gastó 195.355 tokens en 47 llamadas y 15,8 minutos. Ésta: **~190.000 tokens en 9 llamadas y 1,8 minutos**, entregando **dos revisiones independientes** en vez de una. El costo total quedó igual; lo que cambió es que no se gastó nada descubriendo el alcance, y que por el mismo precio salieron dos ejes.

Nueve hallazgos entre los dos ejes. Se verificaron los nueve antes de tocar nada, que es lo que la skill deja explícitamente de nuestro lado.

#### Una refutada

**La zona horaria de `estado_del_pipeline`.** El eje Spec marcó que `ahora_utc() - ultima.inicio` podía dar `TypeError` en Postgres si `ahora_utc()` devolvía un datetime con zona. No: `tiempo.ahora_utc` hace `.replace(tzinfo=None)` **a propósito**, y su docstring explica que cambiarlo obligaría a migrar las columnas. Naive menos naive no rompe en ningún motor.

Vale anotar cómo llegó el hallazgo: el revisor dijo *"no puedo resolverlo sin ver `src/tiempo.py`"* en vez de afirmarlo. Es exactamente la conducta que el protocolo busca — un hallazgo marcado como no decidible cuesta un minuto de verificación; uno afirmado de más cuesta la confianza en los otros ocho.

#### El arreglo que importaba: el registro tumbaba el pipeline

`iniciar_corrida` no tenía guarda, y corre **antes del primer paso**. Así que un fallo al escribir la fila de la corrida abortaba la corrida entera — un problema de contabilidad convertido en uno de producción, y **exactamente lo contrario de lo que la entrada anterior de este archivo afirmaba**: *"el registro no puede tumbar el pipeline"*. La afirmación solo era cierta de `cerrar_corrida`, que sí tenía su `try`.

**Verificado con sonda antes de arreglar: 0 de 8 pasos llegaban a correr.** Después del arreglo, 8 de 8 y el job no levanta.

El `rollback` del `except` no es cosmético: sin él la sesión queda inutilizable y los ocho pasos fallarían igual, por un motivo distinto al original — que es el modo de falla más difícil de diagnosticar y el que `_correr_paso` ya documentaba evitar.

**Y una trampa propia en el camino.** La primera versión de la sonda parcheaba `iniciar_corrida` entera con un `side_effect` que levantaba, así que **salteaba la guarda que quería probar** y seguía dando 0 de 8 después del arreglo. Es el mismo error que este archivo ya documentó dos veces —medir el propio mock— cometido esta vez sobre el arreglo recién hecho. Corregida para que el fallo ocurra *adentro* de la función, mostró 8 de 8. El test permanente hereda ese cuidado y lo dice en su docstring.

#### Los otros tres

**`historial` documentaba al revés.** El parámetro de `GET /pipeline` es el **total** —con `historial=3` vuelven una `ultima` y dos `anteriores`—, pero se documentaba como "cuántas corridas anteriores devolver". El código hacía lo correcto y el texto mentía. Corregido el texto, en el endpoint y en el servicio.

**Código muerto.** `listar_corridas` y `ultima_corrida` no las importaba nadie ni las tocaba ningún test: las escribí por si acaso. Borradas — es lo que `medir-antes-de-resolver` dice de no adelantarse.

**"Deja rastro" prometía más de lo que deja.** La fila se abre antes del primer paso, así que una corrida que muere deja rastro **de que existió** (`inicio` puesto, `fin` en `None`), pero **no el detalle de los pasos**: `pasos` se persiste recién al cerrar. La frase del roadmap decía lo primero de un modo que sugería lo segundo. Precisada.

#### Lo que no se arregló, y por qué

**Las tres condiciones de convivencia** —CI por paths, docs en `app/`, test de contrato— siguen sin cumplirse, y es correcto: **son del paso 3**, cuando la app exista. Hoy no hay `app/` ni workflow que filtrar. Lo que sí se corrigió es la ambigüedad que las dejaba pareciendo parte del paso ya cerrado.

**Los constructores de test duplicados.** El eje Convenciones marcó, contra la regla escrita *"fixtures reutilizables en `tests/conftest.py`"*, que las cuatro clases nuevas traen cada una su constructor de `Cluster` + `Medio` + `Noticia`. Es cierto contra la regla — y también es cierto que **el repo nunca la aplicó a estos constructores**: `conftest.py` no tiene ninguno y **seis archivos de test tienen el suyo**. Se siguió la práctica real. Que la regla y la práctica diverjan es el hallazgo de fondo, y resolverlo es una decisión aparte que toca seis archivos ajenos a este diff.

#### Verificación

794 tests (1 nuevo), `ruff` limpio. **1 mutación y 1 detectada**: sacarle la guarda a `iniciar_corrida` hace caer el test nuevo.


#### La imagen instalaba PyTorch con CUDA para no usarlo nunca (06/09/2026)

Apareció construyendo la app de escritorio, y no tiene nada que ver con ella: es un problema del `Dockerfile` que estaba desde siempre y que nadie había mirado porque la imagen se reconstruye poco.

**El síntoma:** dos reconstrucciones seguidas hubo que abortarlas porque el build se comía **10 GB de RAM**. Cortarlo a mano no era una anomalía del entorno — era lo único que se podía hacer.

**La causa.** `requirements.txt` pide `sentence-transformers`, que arrastra `torch`. En Linux, pip resuelve por defecto la variante con CUDA:

| Paquete | Peso |
|---|---|
| `torch` (CUDA) | 554 MB |
| `nvidia_cudnn_cu13` | 553 MB |
| `nvidia_nccl_cu13` | 216 MB |
| `nvidia_cusparselt_cu13` | 170 MB |
| + `cublas`, `cusolver`, `cufft`, `curand`, `cusparse`, `cuda-toolkit`, `triton`… | cientos más |

Son **más de 2 GB de librerías de GPU**, y desempaquetarlas es lo que se comía la memoria.

**Y no se usa ni una.** Dos comprobaciones, las dos sobre el código y no sobre la intuición:

1. `services/vectorization.get_modelo()` construye el `SentenceTransformer` **sin `device=`**, y en todo `src/` no hay una sola mención a `cuda`, `device` ni `torch`.
2. El `docker-compose.yml` **no le pasa ninguna placa** al contenedor: no hay `deploy.resources.devices` ni `runtime: nvidia`.

O sea que el contenedor no podría usar una GPU aunque el código la pidiera, y el código no la pide.

#### El arreglo, y por qué va en el Dockerfile

Se instala la variante de CPU **antes** de los requirements. Cuando pip resuelve `sentence-transformers`, `torch` ya está satisfecho y no baja nada de CUDA:

```dockerfile
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt && \
    python -m spacy download es_core_news_md
```

**Va en el `Dockerfile` y no en `requirements.txt`** porque ese archivo lo comparte el entorno local de Windows, y no hay motivo para cambiarle el suyo por un problema que es de la imagen. Se evaluó ponerlo como `--extra-index-url` en el `.txt` y se descartó por eso.

#### Medido antes de gastar la reconstrucción

El build completo tarda y ya había fallado dos veces, así que el arreglo se comprobó primero con `pip install --dry-run`, que resuelve dependencias sin bajar los wheels:

| | Paquetes que instalaría | De GPU |
|---|---|---|
| Índice por defecto | 29 | **23** |
| Índice de CPU | 10 | **0** |

Recién con eso a la vista se corrió el build largo. Terminó solo, sin abortar. Verificado después **dentro del contenedor corriendo**: `pip list` no encuentra un solo paquete de nvidia y `torch` reporta `2.14.0+cpu`. La imagen quedó en 3,13 GB.

#### Dos hallazgos del mismo día, y una atribución equivocada

**El contexto de build se había ido a 4,2 GB.** Crear `app/` para la app de escritorio metió `src-tauri/target` (4,1 GB) y `node_modules` (85 MB) adentro del contexto, porque el `COPY . .` los alcanzaba. Se agregó `app/` al `.dockerignore` — y de paso quedó anotado ahí que las reglas como `node_modules/` **matchean solo en la raíz** del contexto, no anidadas. Verificado construyendo una imagen de prueba que solo copia el contexto: pasó de 4,2 GB a **1,8 MB**.

**La atribución equivocada.** Los dos primeros builds fallidos se le achacaron enteros a ese contexto. Con el contexto ya arreglado, el tercero igual se comió la RAM: la causa real era el `pip install` de CUDA. Los dos problemas eran ciertos, pero uno se llevó el crédito del otro. Vale anotarlo porque el patrón se repite: la primera causa plausible que aparece tapa a la que sigue, y solo se separan midiendo cada una por su lado.

#### Y una consecuencia operativa que conviene recordar

El bucle de reinicio que destapó todo esto no era un bug: **aplicar una migración desde el host deja la imagen atrás.** La base quedó marcada en `b963fe84825f` y la imagen no tenía ese archivo, así que el `alembic upgrade head` del arranque no encontraba la revisión y el contenedor moría en loop. Se arregla reconstruyendo, y es algo a tener presente cuando la app de escritorio levante contenedores por su cuenta.

### `codebase-memory-mcp` se evaluó y NO se instala, con la condición que lo reabre (07/09/2026)

Se propuso sumar [`DeusData/codebase-memory-mcp`](https://github.com/DeusData/codebase-memory-mcp) a los proyectos: un servidor MCP que indexa el código en un grafo persistente para que el agente consulte estructura en vez de leer archivos. Queda documentado porque **la herramienta no tiene nada malo** — el motivo del descarte es del proyecto, no de ella, y sin eso escrito la discusión se repite.

#### Lo que la herramienta es, verificado contra la API de GitHub y no contra el README

42.564 estrellas, 3.474 forks, MIT, creado el 24/02/2026 y con push el mismo día de esta evaluación. Binario estático en C, 158-162 lenguajes vía tree-sitter vendorizado, SQLite en `~/.cache/`, **todo local: sin API keys, sin telemetría, sin servicios hosteados**. La postura de seguridad tampoco es marketing: cada artefacto de release trae su bundle de Sigstore, hay `sbom.json`, evidencia de VirusTotal, y en CI están `codeql.yml`, `scorecard.yml` y `dco.yml`. El zip de Windows lleva 30.459 descargas.

Los dos reparos de madurez, dichos igual: es **v0.10.8** (pre-1.0, última release del 19/08) con 545 issues abiertos, y de ~1.900 commits **1.611 son de una sola persona** — los 147 contribuidores son mayormente parches sueltos. Si se rompe, se depura C ajeno.

*Lo que se corrió:* la API de GitHub. *Lo que no:* no se descargó el binario ni se validó una firma; solo se comprobó que los bundles existen.

#### El motivo del descarte: el problema no existe acá

| | archivos | líneas |
|---|---|---|
| `src/` | 36 | 9.530 |
| `app/` (Rust + TS, sin bindings) | 12 | 2.086 |
| `alembic/` | 16 | 1.154 |
| **Fuente** | **64** | **12.770** |
| `tests/` | 19 | 11.508 |

El archivo más grande del sistema es `src/main.py`, con 1.420 líneas. **Un `grep` encuentra cualquier cosa de este repo en una llamada**, y el árbol de fuente entero entra en un contexto.

El "99,2% menos tokens" que la herramienta publica está medido contra explorar archivo por archivo el kernel de Linux: 28 millones de líneas. Es un ahorro real y es de otro problema. A cambio, el costo se paga siempre: ~40 MB de binario, un índice por proyecto, y **15 descripciones de herramientas entrando en cada turno**, haya o no algo que buscar.

#### El reparo que pesó más: un índice desactualizado es una mentira nueva

Los errores que este proyecto viene coleccionando no son de búsqueda. En una sola sesión: PowerShell destruyendo el UTF-8 de un `.rs`, `cargo` resolviendo `.cargo/config.toml` desde el directorio actual y no desde el manifiesto, `ts-rs` escribiendo fuera del directorio vigilado, una mutación que reportó "SOBREVIVE" porque en realidad no había compilado, y el puente `invoke()` que ningún compilador ve.

**Un grafo de código no habría cazado ninguno.** Todos tienen la misma forma: *el artefacto en disco no es el que yo creía*. Son fallas de verificación, no de navegación — y agregar una capa que contesta rápido sobre una copia del código es exactamente el modo de falla contra el que este repo entrena. Es el caso de `bindings:check` otra vez: el guardián pasaba en verde **porque** medía un artefacto viejo, y por eso el error sobrevivió tanto.

#### El otro camino, y su contra

La alternativa evaluada era instalarlo acotado y medirlo contra `grep` en tres preguntas reales del repo. Se descartó por el costo de la prueba misma, no por el resultado esperado: **el instalador auto-configura 45 superficies de agentes**, o sea que escribe en los archivos de configuración de Claude Code y de todo lo demás que haya instalado. Es reversible, pero hay que auditar qué tocó, y eso es más trabajo que el que ahorraría.

#### La condición que lo reabre

Esto se vuelve a discutir cuando pase **cualquiera** de las dos:

- el árbol de **fuente** supera las ~40.000 líneas (hoy 12.770), o
- arranca un segundo proyecto grande cuyo mapa no esté en la cabeza de nadie.

Mientras tanto la decisión es la regla que este repo ya tiene escrita: no resolver problemas de escala que todavía no existen.
### El puente `invoke()`, y el `Set<cluster_id>` medido y resuelto (07/09/2026)

Primer tramo de la fase 4 de la app. Dos cosas que el plan había dejado abiertas a propósito: una capa sin verificar y una decisión esperando un número.

#### Los seis comandos que ningún compilador estaba mirando

La fase 3 dejó siete comandos escritos y probados, pero **sólo `motor_salud` se había invocado desde la ventana**. No era desconfianza en el código: entre Rust y el webview hay una capa que ningún compilador ve. `cargo test` llama las funciones directo, y `tsc` tipa el retorno de `invoke<T>()` con lo que uno le declare — no sabe qué comandos existen ni qué argumentos piden. `invoke("comando_inexistente", { fruta: 3 })` compila perfecto.

Se cruzó con un **andamio descartable** (`app/src/Andamio.tsx`) que corre solo al montar y es todo de lectura, salvo un POST detrás de un botón aparte. **Diez pruebas, cero fallidas.** Cruzaron los seis comandos, el `flatten` de `DetalleSintesis`, y `listar_modelos` llegó sin `api_key_env` ni `base_url` — la primera vez que esa respuesta real llega a la ventana.

#### La conversión a camelCase quedó probada, no supuesta

El riesgo conocido era que **Tauri convierte los nombres de los argumentos a camelCase**. Hasta la fase 2 ningún comando tenía argumentos de más de una palabra, así que la conversión era la identidad y nunca se había ejercitado.

En vez de afirmarlo, se midió: la misma consulta con las dos grafías. **`clusterId` filtró y devolvió 1 fila; `cluster_id` fue ignorado y devolvió 100.** Escrito en snake_case compila, pasa `tsc` y no filtra nada. La fuente lo confirma: `ArgumentCase::Camel` es el default en `tauri-macros 2.6.3` (`wrapper.rs:51`, la conversión en `:506`).

Un segundo control, y **gratis**: pasarle `cluster_id` a `sintetizar_cluster` —donde el argumento es obligatorio— hace que el puente rechace mientras deserializa los argumentos, antes de entrar al cuerpo del comando y por lo tanto antes de que salga un pedido HTTP. Probar la trampa en el comando que gasta plata no cuesta nada.

#### El `Set<cluster_id>`: el número que faltaba

`GET /clusters` no decía si un cluster ya tenía síntesis, y la v1 lo iba a resolver del lado del cliente paginando `/sintesis` para armar un `Set<cluster_id>`. El plan lo había marcado como **la parte más floja** y había dejado escrito que se mediría antes de resolverlo.

**Medido desde la ventana, con la base real:**

| | pedidos | tiempo |
|---|---|---|
| Paginar `/sintesis` (lo que iba a hacer la v1) | 5 | **201 ms** |
| `cantidad_sintesis` en `GET /clusters` | 1 | **14 ms** |

201 ms antes de dibujar una sola fila, bajando 438 objetos completos —con sus arrays de tópicos y medios— para calcular un conjunto de enteros, y para responder una pregunta sobre los veinte clusters que se ven.

**Y no era el número de hoy lo que decidía, sino su pendiente.** El histórico crece a ~28,5 síntesis por día activo (428 en 15 días activos). A ese ritmo: ~480 ms en tres semanas, ~960 ms en dos meses. Peor que la lentitud, hay un punto donde **deja de ser lento y pasa a ser incorrecto**: cualquier tope de páginas del lado del cliente empieza a truncar el `Set` en silencio, y la pantalla dice que un cluster no tiene síntesis cuando sí las tiene.

#### El arreglo, y por qué es del lado del motor

Se evaluaron dos caminos. **Acotar del lado del cliente** —pedir `/sintesis?cluster_id=X` sólo para los veinte clusters visibles— se descartó: cambia 5 viajes por 20, se rehace con cada filtro, y probablemente empeora la latencia que se quería arreglar.

El elegido es el que el propio plan anticipaba: **un campo del lado del motor**. `listar_clusters` devuelve `cantidad_sintesis`, de **una consulta agrupada** sobre los ids que la lista ya trajo — el mismo criterio del `selectinload` que ya estaba ahí para no ser 1 + N. Sin migración y sin cambio de esquema.

**`0` y nunca ausente.** Un campo faltante obligaría a la ventana a tratar "no tiene síntesis" igual que "no vino el dato", que son cosas distintas.

Esto **no contradice** la decisión de la fase anterior de no engordar `GET /clusters` con los ángulos: eso era traer el contenido, esto es un entero que responde "¿ya está resuelto?". La regla sigue siendo que el cluster no carga las síntesis.

#### El banco de medición se estaba midiendo a sí mismo

La primera corrida dio **240 ms y 19 ms**. Los números buenos son **201 y 14**, y la diferencia no fue ruido: en los logs del motor **cada GET aparecía dos veces**.

La causa es `React.StrictMode`, que en desarrollo invoca cada efecto dos veces a propósito para destapar efectos que no son idempotentes. El del andamio lo es —correr las pruebas dos veces no rompe nada—, así que no fallaba nada visible. Pero **no era gratis**: salían dos corridas simultáneas compitiendo por la red, y los tiempos que el andamio reportaba venían inflados por su propia duplicación.

Se arregló con una guarda de `useRef` sobre el efecto de montaje, y se comprobó del lado del motor: dos corridas separadas por 48 segundos, **un solo pedido por endpoint en cada una**.

Lo anotable es cómo apareció. No lo encontró un test: **salió de ir a mirar el log del motor para verificar otra cosa**. Se estaba comprobando que `sintetizar_cluster` con `forzar: false` no llamara al proveedor —y ahí sí, la ausencia de la línea `Sintetizando con` es evidencia, porque el motor la escribe siempre antes de llamar (`synthesis.py:1175`)—. La duplicación estaba a la vista en el mismo log, sin relación con lo que se buscaba.

#### Verificación

- Motor: 796 tests y `ruff` limpio. **Tres mutaciones, las tres cazadas**: quitar el default `0` y contar sobre un solo id caen en el test de conteo; volver el conteo una consulta por cluster tumba los dos guardianes de cantidad de queries.
- App: 53 tests, `cargo fmt`, `clippy -D warnings`, `tsc --noEmit`, `npm run build` y `bindings:check`.
- **El fixture se recapturó del motor real** después de reconstruir la imagen, y trae a propósito un cluster con `0` y dos con `1`. **Dos mutaciones más, cazadas**: si el motor deja de mandar el campo revienta la deserialización, y si una recaptura pierde el caso del cero el test lo dice.

#### Lo que queda de la fase 4

La pantalla en sí y prender la CSP — las dos hechas después, ver las entradas siguientes.

**Sobre borrar el andamio, la decisión cambió.** Al revisar qué se perdía se vio que casi nada necesitaba rescate: el chequeo de fuga de `/modelos` ya vive en el motor (`tests/test_modelos.py`) y más fuerte, y las mediciones ya están escritas. Pero quedaban dos cosas sin cubrir —`listar_sintesis` y `detalle_de_sintesis`, que espera el feed de la fase 5, y las comprobaciones que exigen una ventana abierta, como que la CSP se aplique—. Así que **se mantiene como banco de pruebas del puente hasta la fase 9**. En su lugar se agregó una guarda automática: un test que falla si aparece un `invoke` fuera de los envoltorios, nombrando el archivo.
### La pantalla de trabajo, y la paleta que no era la de Sin Ruido (07/09/2026)

Tercer tramo de la fase 4: la pantalla en sí. Se documenta por la paleta, que fue una corrección de fondo y no un ajuste estético.

#### La identidad estaba, el código no la tenía

La app venía con un verde-gris "con sesgo neutro", justificado en su propio comentario. **Ese verde-gris no era de Sin Ruido**: lo inventó esta sesión en la fase 1 y nadie lo había contrastado con la marca. Al preguntarlo aparecieron los colores reales: **#22486A azul, #E5A823 oro y blanco**, con el blanco predominante y el azul mandando en modo oscuro.

Vale anotar el error de razonamiento: al evaluar una herramienta de diseño se argumentó que no convenía tocar el color porque "la app ya tiene una identidad decidida y documentada". La identidad estaba documentada, sí — pero en la cabeza del dueño del proyecto, no en el repo. **Que algo esté escrito con seguridad en un comentario no lo vuelve la decisión correcta.**

#### La restricción que ordenó todo el diseño salió de medir

**El oro sobre blanco da 2,11:1.** No llega ni al 3,0 que WCAG pide para texto grande. Eso no es una preferencia: cierra la puerta.

De ahí sale la regla: **en tema claro el oro nunca es texto**. Es relleno, filete y marca, y lo que se apoya encima va en azul (4,52:1). Cuando hace falta ese calor *en* un texto sobre blanco se usa una variante oscurecida, medida en 4,55:1.

En tema oscuro se da vuelta, y es la combinación natural de la marca: sobre el azul, el oro lee a 7,33:1 y pasa a ser el acento. Un solo token —`--acento`— cambia de color con el tema y las dos lecturas son correctas.

Los tres niveles de tinta son **el tono más claro que todavía cumple** su objetivo (13:1, 7:1 y 4,5:1 contra el peor fondo), así que la jerarquía es lo más suave posible sin dejar de ser legible. Todo derivado del matiz del azul de marca, moviendo sólo luminosidad y saturación.

#### Lo que la paleta anterior escondía

Antes de reemplazarla se la midió, y **no pasaba**: `--tinta-tenue` daba 2,58:1 y estaba puesto justo en el texto más chico de la interfaz —chips, datos de tarjeta, textos de ayuda—, usado en 17 reglas. Además había seis tamaños de fuente por debajo de 12px, el menor en 9,8px, y eran exactamente los pintados con ese gris. Gris flojo más letra chica.

Se corrigió antes de conocer los colores de marca, y la corrección se rehízo después sobre la paleta nueva. **Re-medir encontró un cuarto problema que la primera pasada no vio**: `--ok` fallaba contra `--hundido` porque en la primera medición sólo se lo había comparado contra `--superficie`.

#### La grilla, y por qué las fichas miden todas igual

La lista pasó de una tarjeta por fila a una grilla `auto-fill`. Lo que la volvía irregular no era la grilla sino el contenido: título de uno a tres renglones, un aviso que aparecía sólo en algunas tarjetas, y las notas desplegables.

Se fijó el alto de fila con `grid-auto-rows` y **el título reserva sus tres renglones aunque tenga uno**.

**Corrección de lo que se escribió acá primero.** Se afirmó que tres renglones alcanzaban para los 537 títulos porque "a ese ancho entran ~186 caracteres" contra un máximo de 179. Ese 186 salió de un valor por renglón inventado, no medido. Hecha la cuenta con los anchos de columna que la grilla produce de verdad, la capacidad es de **133 a 161 caracteres según el tamaño de la ventana**: los títulos más largos sí se recortan. El recorte va con puntos suspensivos y el texto completo queda en el `title` del elemento, así que la decisión de fijar el alto se sostiene — lo que no se sostenía era la justificación. Las notas desplegadas entran en el mismo hueco que los datos, con scroll propio, para que abrir una tarjeta no estire su fila.

#### Un bug que la barra fija destapó

La barra de estado siempre visible sondea el motor **en reposo**, y ahí se vio que `Sondeo::Rechazada` se leía siempre como `arrancando`. Era correcto mientras el único que sondeaba era el bucle de arranque —ahí ya se le había pedido a Docker que levantara—, pero en reposo el mismo dato significa lo contrario: está apagado.

Se separó en `estado_al_arrancar` y `estado_en_reposo`, dos funciones y no un parámetro booleano: lo que cambia no es el sondeo sino qué se acaba de hacer, y eso lo sabe quien llama. **Mutado**: volver a la lectura única tumba el test que documenta el caso, así que queda de guarda contra que vuelva.

#### Accesibilidad, con lo que aportó y lo que no

Se evaluó y se instaló la skill `ui-ux-pro-max` (MIT, búsqueda local sobre CSV, sin red ni credenciales). Su aporte real fue acotado y conviene registrarlo sin inflarlo: **confirmó** el criterio de contraste que ya se estaba aplicando, **no sirvió** para el color —su dominio devuelve paletas para elegir, y acá la paleta venía dada— y **aportó una cosa que no estaba considerada**: *Focus Not Obscured* (WCAG 2.2 AA), o sea que una barra `sticky` puede tapar el control que acaba de recibir el foco. Se arregló con `scroll-padding-top`.

Del mismo repaso salieron el diálogo sin manejo de teclado —ahora con foco inicial, Escape, tabulador atrapado y foco devuelto al cerrar— y el botón de sólo ícono sin nombre accesible.

### La CSP del webview, y por qué el valor que teníamos escrito no servía (07/09/2026)

Segundo tramo de la fase 4. La política estaba planificada desde la fase 2 con un valor propuesto, y **ese valor era incorrecto**.

#### `default-src 'self'` habría matado la app

El `invoke()` de Tauri no viaja por HTTP: usa el esquema **`ipc://localhost`**, que `'self'` no cubre. Con la política que el roadmap y el README venían proponiendo, ni un solo comando habría cruzado el puente. Está documentado en la fuente de `tauri-utils`, que trae el ejemplo correcto:

```
csp: "default-src 'self'; connect-src ipc: http://ipc.localhost"
```

Se descubrió leyendo la fuente antes de escribir, no rompiendo la app y depurando después.

#### La política quedó mínima porque el inventario lo permitió

Antes de escribirla se revisó qué carga la ventana: `index.html` trae un único script del mismo origen, las tipografías son del sistema, el logo entra por `url()` local, y **no hay un solo estilo en línea** —el build emite el CSS como archivo aparte con `<link>`—. Nada de eso exige aflojar la política. En el build, además, Tauri la endurece agregando nonces y hashes.

#### En desarrollo no hay CSP, y no es un descuido

`devCsp` está escrito y **no se aplica**. La CSP se inyecta en un solo lugar de Tauri —`manager/mod.rs`, dentro de `get_asset`, cuando Tauri sirve el frontend por su protocolo— y en desarrollo el HTML lo sirve Vite. Tauri nunca lo toca.

Eso invalida además una advertencia que el README repetía: *"una CSP mal puesta rompe el HMR de Vite"*. Acá no puede romperlo, porque nunca llega a aplicarse en dev. Era una advertencia genérica que en este montaje no aplica.

#### Cómo se comprobó, y por qué la comprobación obvia no sirve

**Una política escrita pero no aplicada se ve idéntica a una que funciona.** Un `fetch` a un host externo falla en los dos casos: con CSP porque la bloquea, sin CSP porque CORS la rechaza. Mirar si el `fetch` falló habría dado verde en los dos.

Lo que distingue los casos es el evento **`securitypolicyviolation`**, que sólo existe si la política se aplica. La sonda que se agregó al andamio lo escucha, y por eso encontró algo: en la ventana de desarrollo reportó *"SIN VIOLACIÓN"*, destapando que `devCsp` era letra muerta. En el ejecutable de release reportó el bloqueo con su directiva.

**Y hace falta la mitad complementaria**: una política que bloquee todo —incluido el IPC— se vería igual de exitosa en esa línea. Se verificó del otro lado, en el log del motor: la app de release le mandó **26 pedidos**, entre ellos los de la pantalla real. Bloquea lo de afuera y deja pasar lo nuestro.

#### Lo que costó

Se eligió compilar en release para probarla de verdad, en vez de dejar la de producción sin comprobar hasta la fase 9. El build tardó varios minutos y de paso dejó los dos instaladores (MSI y NSIS) en `target/release/bundle/`, que no se pidieron y que recién hacen falta en la fase 9.
### Fase 5: el feed de lectura, y tres cosas que salieron de mirarlo funcionando (07/09/2026)

La segunda pantalla: leer lo que el motor produjo. Ritmo distinto al de la lista de trabajo —allá se mira, se decide y se dispara; acá se lee de corrido— que es la razón por la que son dos y no una.

La unidad del feed es el **ángulo**, no el hecho: un cluster produce varias síntesis porque separar el material en recortes es trabajo del modelo. Por eso las tarjetas titulan con `titulo_angulo`. El contenido —resumen, puntos clave y comparativa— se pide aparte con `detalle_de_sintesis`, igual que del lado del motor: traer la comparativa completa de veinte ítems para elegir uno sería pagar la lectura entera para tomar una decisión.

**La comparativa se pinta como una columna por medio**, con qué destacó, qué omitió y la cita textual que lo respalda. Enfrentadas y no en párrafo corrido, porque leerlas juntas es la tesis del proyecto.

#### El 422 resetea la lista, y la categoría se verificó antes de confiar en ella

Un cursor caducado es una entrada nuestra que quedó vieja, no un error del motor —que por eso devuelve 422 y no 500—. Si se dejara el cursor roto en el estado, un tropiezo se convertiría en una pantalla que ya no carga más y quien mira no tendría cómo saber por qué. Así que se avisa y se recarga desde cero.

Esa rama bifurca sobre `tipo === "invalida"`, y **eso era una suposición**: si el motor devolviera otra categoría, el reset no dispararía nunca y la pantalla quedaría trabada — justo lo que el código existe para evitar. Se verificó eslabón por eslabón: el motor devuelve 422 con tres cursores basura distintos, `api.rs:149` mapea `UNPROCESSABLE_ENTITY` a `Invalida`, serde lo etiqueta `invalida`, el binding lo confirma, y una sonda del andamio comprobó que llega así **cruzando el puente**, que era el único eslabón que no se podía leer.

#### El `Modal` se extrajo al aparecer el segundo, no antes

Con un solo diálogo, el manejo de foco vivía adentro del componente y estaba bien ahí. Con dos, dejar duplicadas cuarenta líneas de lógica de teclado es pedir que se desincronicen — y lo que se desincroniza en silencio es siempre la mitad menos visible, o sea la del teclado.

Se le sumó el **bloqueo del scroll de fondo**: sin eso la rueda del mouse mueve la lista de atrás y quien cierra el diálogo aparece en otro lugar sin haber pedido moverse. Va por clase y no tocando `style`, porque el atributo `style` lo bloquea la CSP de producción. Y `scrollbar-gutter: stable` en `html`, porque al bloquear el scroll desaparece la barra y todo el contenido salta unos píxeles.

#### `pasos` es un mapa abierto, y tratarlo como struct se veía

La barra del pipeline mostraba `[object Object]` en cada paso. La causa: `pasos` no es un struct sino un mapa cuyas claves decide `_correr_paso`, y el render hacía `String(valor)` sobre cada uno.

Medido sobre las 35 corridas de la base, el segundo nivel trae **cinco tipos**: enteros, booleanos, strings, listas y objetos anidados. Con `String()` los últimos tres se perdían. El normalizador nuevo baja un nivel, suma los numéricos de `ingesta` —que es una lista con un objeto por medio— y lo que no encaje lo resume en vez de descartarlo, porque este mapa puede cambiar del lado del motor sin avisar. Verificado con un port del mismo algoritmo contra las 35 corridas: 1.472 pares etiqueta/valor, ninguno cae en `[object Object]` ni en el descarte.

El detalle salió de la barra a un desplegable: ocho pasos con sus contadores no entran en una línea. Los contadores en cero se atenúan en vez de esconderse — que un contador exista y esté en cero dice algo distinto de que no exista.

#### La cabecera, y por qué se pegan juntas

La barra del motor y las pestañas quedaron envueltas en un solo contenedor `sticky`. La alternativa era darle a las pestañas un `top` igual al alto de la barra: un número que se desactualiza en silencio en cuanto cambia el logo o el alto de un botón.

Eso obligó a recalcular el `scroll-padding-top` del criterio *Focus Not Obscured*: pasó de 5rem a 7rem, porque ahora lo pegajoso mide ~103px y no ~62.

**La barra dejó de tener `max-width`.** Se le había copiado el límite del cuerpo, y ese límite tiene sentido para el texto que se lee, no para el cromo: en pantalla grande el logo quedaba flotando lejos del borde izquierdo y los botones lejos del derecho.

#### Una sonda que gritaba lobo

La prueba de la CSP marcaba en rojo, en cada corrida de desarrollo, una condición que en desarrollo es **la correcta**: ahí no hay CSP y no puede haberla. Un instrumento que da falsa alarma fija entrena a ignorarlo, y el andamio ahora es permanente. Ahora distingue: en desarrollo informa que no dictamina; en un ejecutable compilado, la ausencia de violación sí es un fallo.
### Fase 6: la bandeja, y las dos formas de salir con nombre propio (07/09/2026)

Un programa que maneja contenedores tiene **dos cierres legítimos**: irse dejando el motor produciendo, o pararlo todo. El pipeline corre cada 15 minutos y no necesita la ventana abierta, así que cerrar la cabina y cerrar el motor son decisiones distintas.

La decisión de fondo, que venía del grillado: **cuál ocurre no puede depender de dónde se hizo clic.** Así que la cruz de la ventana no cierra — pregunta —, y el menú de bandeja ofrece las dos salidas escritas con todas las letras en vez de un "Salir" ambiguo.

#### La bandeja no ejecuta nada por su cuenta

Cada opción del menú le avisa a la ventana y la ventana hace el trabajo. Dos motivos: parar el motor tarda segundos, y hacerlo desde el menú dejaría a quien mira sin ninguna señal de que algo está pasando; y la alternativa era duplicar en Rust la lógica que la pantalla ya tiene.

Antes de pedir la decisión, la ventana se trae al frente: puede estar minimizada, y un diálogo mostrado donde no se ve no es un diálogo.

**Si detener falla, no se sale igual.** Cerrar dejaría los contenedores corriendo justo cuando se pidió lo contrario, y sin nadie mirando. Se muestra qué pasó y se deja elegir de nuevo.

#### Minimizar minimiza

El plan original pedía que minimizar escondiera la ventana en la bandeja. Se cambió: va a la barra de tareas como cualquier programa de Windows. El motivo es que esconderla deja **una sola forma de volver**, y el ícono de bandeja puede quedar oculto en el desplegable de Windows sin que nadie lo note. Con la barra de tareas hay siempre dos vías.

#### Un bug encontrado antes de que existiera

La bandeja estaba declarada en `tauri.conf.json` **y** construida en `bandeja.rs`. Leyendo la fuente apareció que Tauri arma una desde la config si esa clave existe (`app.rs:2420`, *"initialize default tray icon if defined"*), así que habrían salido dos íconos con el mismo id. Como el menú sólo se puede definir en código, la config quedó sin `trayIcon` y `bandeja.rs` es la única fuente.

#### Los tests, y por qué el primero que escribí no servía

La primera versión afirmaba que las etiquetas contenían la palabra "motor" — sobre literales escritos **en el propio test**. Pasaba siempre, dijera lo que dijera el menú. Es medir el propio mock, en su forma más pura.

Corregido en dos pasos: las etiquetas pasaron a ser constantes que el menú usa de verdad, y la tabla que decide qué pedido emite cada opción se extrajo de `al_elegir` —que necesita un `AppHandle` inexistente en un test— a una función propia. Ahora el test afirma sobre el código que corre.

**Tres mutaciones, las tres cazadas**: un id del menú que deja de matchear, alguien que acorta una etiqueta a "Salir", y las dos salidas diciendo lo mismo. La última es la que importa: es exactamente lo que esta fase existe para evitar.

#### Verificación de los tres caminos

Lo que vale de esta tabla no es que den verde, sino que **dos dan resultados opuestos**. Si el segundo hubiera parado el motor se habría visto idéntico al primero, así que se probó con el motor arriba y mirando que el contador de `Up` no se reiniciara.

| camino | el motor quedó |
|---|---|
| Cruz → Detener motor y salir | parado, con 11s entre un contenedor y el otro |
| Cruz → Salir dejando el motor corriendo | vivo, sin reiniciar, contestando 200 |
| Bandeja → Detener motor y salir | parado, y la ventana se trajo al frente sola |

Queda sin probar la cuarta combinación —bandeja con "salir sin detener"—, que va por el mismo despachador cambiando sólo qué pedido emite.

#### Los íconos siguen siendo los de Tauri

`tauri icon` exige una imagen **cuadrada** y el logotipo es 1,84:1. Metido en un cuadrado ocupa el 100% del ancho y el 54% del alto: a los 16×16 que Windows dibuja en la bandeja, eso son 16 × 8,7 píxeles, donde un nombre no se lee. Hace falta el **isotipo** —el símbolo solo, sin el nombre—, que es para lo que las marcas tienen las dos versiones. Queda pendiente para la fase 9, y no bloquea nada: es un archivo que se reemplaza sin tocar código.
### Fase 7: el test de contrato, y dos cosas que encontró al escribirlo (08/09/2026)

El motor y la app se versionan juntos pero **se rompen por separado**: renombrar un campo de una respuesta compila perfecto en Python, pasa todos los demás tests, y hace que la ventana muestre una tarjeta vacía o falle al deserializar. Ningún compilador cruza esa frontera.

El contrato vive del lado del motor —`tests/test_contrato_api.py`— porque es el motor el que puede romperlo sin enterarse.

#### La regla: subconjunto y no igualdad

La respuesta tiene que traer **al menos** los campos pactados. Agregar campos no rompe la app —los structs de Rust no llevan `deny_unknown_fields`, y fue deliberado— pero renombrar o borrar sí. El motor puede crecer sin pedir permiso; no puede achicarse sin avisar. Hay un test que fija esa asimetría, para que nadie "endurezca" los demás a igualdad sin darse cuenta de lo que rompe.

#### Estos tests no mockean, y esa es la decisión de fondo

Los tests de endpoints de `test_api.py` mockean la capa de servicio, y está bien: prueban el endpoint, no el servicio. **Un test de contrato no puede hacerlo.** Uno que mockee `listar_sintesis` afirma sobre la forma de su propio mock y pasaría en verde con el motor roto — es medir el propio mock, en el lugar exacto donde eso es fatal. Acá se siembran datos reales y se ejercita el camino entero.

Del mismo criterio salió una guarda del sembrado: hay un `assert` de que la lista devuelta **no venga vacía**. Sin él, `GET /modelos` sin modelos sembrados pasaba el test sin haber mirado un solo registro.

#### El diccionario tiene un guardián, y hacía falta

El plan pedía un diccionario literal endpoint → campos. Es legible, pero **es una segunda copia**: la primera son los structs de `tipos.rs`. Sin nada que las ate, alguien agrega un campo obligatorio en Rust, la app empieza a exigirlo, y el contrato sigue protegiendo el de antes.

Se agregó un test que compara el diccionario contra los **bindings generados** por `ts-rs` desde esos structs. Se eligió parsear el TypeScript generado y no el Rust a mano porque lo primero es estable por construcción. **Mutación**: se agregó un campo obligatorio a `ResumenSintesis`, se regeneraron los bindings, y el guardián lo detectó.

Contra: acopla la suite del motor a archivos de la app. **Hay que tenerlo en cuenta al partir la CI por rutas en la fase 8**, o un cambio del lado de la app no dispararía el chequeo de deriva.

#### Dos cosas que el trabajo encontró y la lectura no

**Las rutas `PATCH`.** El inventario manual se hizo con un `grep` de `get|post|put|delete` y **se comió `PATCH /medios/{id}` y `PATCH /modelos/{id}`**. El motor tiene 19 rutas, no 17. El test las encontró en su primera corrida porque lee `openapi.json` en vez de confiar en una lectura del código. Quedó anotado en `CONTRATO.md` como evidencia de para qué sirve el chequeo.

**`hay_material_nuevo` no mira la síntesis.** Al sembrar un cluster "ya sintetizado" para probar el corte, el test devolvió **422 en vez de 200**: el pedido había llegado hasta el proveedor. La función mira `cluster.noticias_al_sintetizar` —una marca con cuántas noticias había al sintetizar— y no la existencia de la síntesis. El sembrado no representaba un cluster real, y sin el test eso no se habría notado.

#### El POST se cubre en dos de sus tres desenlaces, y sin gastar

Se había dicho que `POST /clusters/{id}/synthesize` no era cubrible sin llamar al proveedor. **Es falso, y la corrección la trajo el usuario**: los dos cortes —`sin_medios_suficientes` y `sin_material_nuevo`— ocurren antes de cualquier llamada, y antes incluso del chequeo de credencial. Se ejercitan de verdad sembrando los datos que los provocan.

Queda sin cubrir sólo `sintetizado: true`, el único que cuesta plata. Su forma sigue en `fixtures/derivados/post_sintetizado.json`, **derivada leyendo el código y no capturada**, y por eso vive en `derivados/`: es un supuesto fundado, no evidencia. Cerrarlo requiere una síntesis real, que se paga una vez.

#### Verificación

22 tests nuevos, 815 en total del lado del motor, `ruff` limpio. **Seis mutaciones, las seis cazadas**: renombrar `titulo_angulo` (la que pedía el plan), borrar `cantidad_sintesis`, un campo obligatorio nuevo en `tipos.rs`, renombrar `motivo`, inventar un motivo que la app no discrimina, y que desaparezca `sintetizado` de la respuesta que corta.
