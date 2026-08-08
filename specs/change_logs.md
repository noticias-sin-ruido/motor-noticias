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
Criterio de admisión: el feed debe traer el artículo completo vía el tag `content:encoded`. Sin cuerpo completo no hay enfoque editorial que comparar, que es todo el valor del producto. Seed en `seed_medios.py`:

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
Validado contra Postgres 16 + pgvector real vía `docker-compose.yml` (que estaba vacío/sin contenido real hasta esta validación — se creó desde cero). Ver `VALIDACION_FASE2.md` para el paso a paso completo y las queries de chequeo. Extensión `vector` y tablas se crean correctamente, `POST /ingest` corre de punta a punta contra los feeds reales.

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

## Fase 3 — Análisis pendiente: exclusión por género y umbrales por tópico

Debate abierto el 8/8/2026, **con recomendación pero sin implementar**. Se retoma cuando Fase 4 permita juzgar calidad por las síntesis generadas y no por los títulos.

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

### Recomendación (en este orden)
1. **Exclusión por género, primero.** Sacar del agrupamiento los segmentos que no reportan eventos: `columnistas`, `opinion`, `cocina`, `recetas`, `horoscopo`, `lifestyle`. Ataca directo lo que falla, es una lista de segmentos en vez de una segunda superficie de calibración, y de paso baja el gasto de Gemini en Fase 4 al no sintetizar lo que no lo merece.
2. **Umbrales por tópico, solo si después sigue haciendo falta** — calibrados con más datos y esperando que `deportes` baje y `espectaculos` suba, no al revés.
3. **Esperar a Fase 4 para calibrar.** Hoy la calidad se juzga mirando títulos; con síntesis generadas se ve cuál mezcla dos hechos. Calibrar N umbrales con 5-15 clusters por tópico es mucho riesgo de sobreajuste.

---

## Fase 4 — Síntesis Neutra con IA (diseño cerrado, implementación pendiente)

### Entrega de síntesis al backend web/mobile
El motor no expone la síntesis vía polling: la empuja por webhook al back-end del producto (web/mobile), que la persiste en su propia BD junto a atributos propios (likes, comentarios, etc.).

- **Sin entidad nueva**: no hace falta una clase `NoticiaProcesada` separada — el estado de entrega se guarda como campos directos en `Sintesis` (`enviado_backend: bool`, `fecha_envio: Optional[datetime]`, `intentos_envio: int`). Se descartó una tabla de log de envíos aparte por sobre-ingeniería: hoy hay un solo backend destino.
- **Reintentos**: `tenacity` en el momento de enviar (mismo patrón que la ingesta). Si se agotan, la `Sintesis` queda con `enviado_backend=False` y un **job periódico sobre el `APScheduler` ya existente** (no una cola de mensajes) barre las síntesis no entregadas y reintenta. Sin reenvío manual — se descartó por depender de que un operario vea una alerta y actúe.
- **Autenticación del webhook**: firma HMAC-SHA256 sobre el cuerpo del request + timestamp en el header (para poder rechazar requests viejos y mitigar replay), con secreto compartido vía variable de entorno en ambos lados. Se prefirió por sobre un token estático porque el secreto nunca viaja en la red (se manda una firma derivada, no el secreto en sí) — defensa en profundidad más allá de lo que ya da TLS.
- **Idempotencia del lado del backend receptor**: queda a resolver por el equipo de backend/mobile, no es una decisión de este repo.
