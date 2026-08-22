from typing import Dict, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la aplicación, cargada desde variables de entorno o desde el archivo .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: Optional[str] = None
    ENVIRONMENT: str = "development"

    # --- Logging (ver src/logging_config.py) ---

    # Nivel del motor. En INFO se ve lo que hizo cada paso del pipeline, con qué
    # modelo se sintetizó y **qué fracción del ciclo consumió la corrida** — el
    # número con el que se calibra INGEST_INTERVAL_MINUTES. Bajarlo a WARNING
    # deja solo los problemas.
    #
    # Un valor que no se entienda NO tumba el arranque: se cae a INFO y se
    # avisa. Cambiar el detalle de los logs no puede dejar el motor sin levantar.
    LOG_LEVEL: str = "INFO"

    # El SQL que ejecuta SQLAlchemy, sentencia por sentencia. **Apagado por
    # defecto y separado de `LOG_LEVEL` a propósito**: son miles de líneas por
    # ciclo, así que si viniera dentro de `DEBUG` nadie podría poner el motor en
    # DEBUG sin ahogarse.
    #
    # Antes esto era `echo=(ENVIRONMENT == "development")` en `database.py`, o
    # sea que el modo de desarrollo decidía por su cuenta escupir todo el SQL.
    # Ahora es una decisión aparte, y sale por el mismo handler y con el mismo
    # formato que el resto.
    LOG_SQL: bool = False

    # Token de operador para la API. **Opcional a propósito**: sin él la API
    # queda abierta y el motor lo avisa al arrancar. Ver `src/auth.py` para el
    # razonamiento completo — en dos líneas, este motor lo despliega gente
    # distinta en contextos distintos y cómo lo exponen es decisión suya.
    API_TOKEN: Optional[str] = None

    # Cada cuánto corre el pipeline completo (ver specs/change_logs.md, Fase 2 --
    # "Scheduler", para el razonamiento del intervalo uniforme).
    #
    # Es configurable a propósito, a diferencia de los otros parámetros del
    # scheduler —que viven como constantes en `main.py` porque son
    # estructurales—: este es el que hay que **calibrar con datos**, y que
    # hacerlo exija editar código y redeployar convertiría una prueba de una
    # tarde en un cambio de versión. El canario de duración loguea la
    # utilización de cada corrida justamente para poder decidirlo.
    #
    # La decisión puede ir para cualquier lado: si una corrida normal usa una
    # fracción chica del ciclo, lo sensato es acortarlo para tener noticias más
    # frescas; si se acerca al techo, alargarlo. Subirlo da margen contra el
    # solapamiento de corridas, pero NO contra los atrasos en lanzarlas, que no
    # dependen de cuánto dure el pipeline.
    INGEST_INTERVAL_MINUTES: int = 15

    # Alertas de fallo de ingesta (ver specs/change_logs.md, Fase 2 --
    # "Manejo de errores por medio").
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    ALERT_EMAIL_TO: Optional[str] = None

    # Segundos que se espera al servidor SMTP. **Sin esto el default de
    # `smtplib` es bloquear indefinidamente** (`socket.getdefaulttimeout()` es
    # `None`), y `enviar_alerta` se llama desde el hilo del job en cinco
    # lugares: un servidor colgado deja el pipeline trabado para siempre, con
    # `max_instances=1` salteando todos los ciclos siguientes y sin más
    # recuperación que reiniciar el proceso. O sea que el avisador, cuyo
    # trabajo es que los fallos se vean, pasaba a ser lo que mata la corrida
    # en silencio.
    #
    # OJO: acota **cada operación de socket**, no la sesión entera. Con las
    # cuatro del envío (connect, starttls, login, send) el peor caso es ~4x
    # este valor, no este valor. Contra un ciclo de 900 s sigue siendo chico.
    # Gmail responde en 1-3 s, así que 10 deja margen de sobra.
    SMTP_TIMEOUT_SEGUNDOS: float = 10.0

    # Tiempo mínimo entre dos avisos del mismo problema. Un fallo permanente
    # dispararía 96 mails por día al intervalo de 15 minutos del scheduler, y a
    # partir del tercero ya nadie los lee.
    ALERT_COOLDOWN_MINUTOS: int = 60

    # --- Vectorización y clustering (Fase 3) ---
    # Todos estos valores se calibraron contra 620 noticias reales; el
    # razonamiento completo está en specs/change_logs.md, Fase 3.

    # Multilingüe y de 384 dims (coincide con EMBEDDING_DIM en models/noticia.py).
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    # Caracteres del cuerpo que se suman al título para armar el texto a vectorizar.
    # No se usa el artículo completo: el modelo trunca a ~256-512 tokens y, por la
    # pirámide invertida, el qué/quién/dónde ya está en el arranque de la nota.
    EMBEDDING_CHARS_CUERPO: int = 500

    # Similitud coseno mínima para considerar que dos noticias son el mismo hecho.
    # Deliberadamente configurable: el juez real de este valor es la calidad de las
    # síntesis de Fase 4, así que se ajusta con datos de producción sin tocar código.
    # Medido: 0.80 -> 51 clusters publicables | 0.75 -> 57 | 0.70 -> 63 (con más
    # falsos positivos) | 0.65 -> se degradan los clusters de 3 medios.
    UMBRAL_SIMILITUD: float = 0.75

    # Horas que un cluster acepta noticias nuevas antes de cerrarse. El plazo corre
    # desde `Cluster.fecha_creacion` y NO se reinicia con cada artículo: con ventana
    # deslizante, una historia de cobertura larga (ej. la visita del Papa) nunca
    # cerraría. Aplica también a la elegibilidad de las noticias sueltas.
    HORAS_CLUSTER_ABIERTO: int = 12

    # Medios distintos que necesita un cluster para generar síntesis. Con menos,
    # se cierra como "descartado": sin dos voces no hay enfoques que comparar.
    MIN_MEDIOS_CLUSTER: int = 2

    # Similitud entre centroides a partir de la cual dos clusters abiertos se
    # consideran el mismo hecho y se fusionan. El clustering busca cobertura y
    # deja la separación por ángulo para Fase 4, así que fusionar de más no es
    # el riesgo: el riesgo es mezclar hechos ajenos, y a 0.85 no se observó.
    #
    # Bajado de 0.90 tras verlo fallar con datos reales: dos clusters de la
    # muerte de Jorge Messi quedaron a 0.8806 y publicaron ángulos solapados.
    # Pasa cuando un cluster acumula un tipo de cobertura (repercusiones) y su
    # centroide se corre, de modo que el material nuevo del mismo hecho (las
    # fotos del velatorio) ya no lo alcanza y arma un cluster aparte.
    # Subirlo a 1.01 desactiva la fusión, porque el coseno nunca supera 1.
    UMBRAL_FUSION_CLUSTERS: float = 0.85

    # Notas que no reportan un hecho: no entran al agrupamiento, pero **no se
    # descartan** — quedan con su categoría, que es lo que habilita tratarlas
    # como producto propio (un tag suscribible, por ejemplo el horóscopo).
    #
    # Se buscan en la URL completa y no en el segmento de sección; ver
    # `services/categorias.py`. Los patrones son angostos a propósito: se probó
    # `signos` a secas —que sobre 1.200 noticias reales no dio un solo falso
    # positivo— y se sacó igual, porque "signos de recuperación" es español
    # corriente y el riesgo no compensa. Las notas sueltas que se escapen no
    # llegan a formar cluster: les falta el segundo medio.
    # "opinion" va ANCLADO a segmento de URL y no como palabra suelta. Medido
    # sobre las 4.532 noticias reales: `opinion` a secas caza 30 y el patrón
    # anclado 29; la que sobra es un falso positivo genuino de Paparazzi ("la
    # letal opinión de Yanina Latorre..."), que no es una columna.
    #
    # Aporte de cada rama, medido: `/opinion/` 29 · `/columnistas/` 56 (El
    # Cronista) · `/editoriales?/` 4 (La Nación) · `/modo-fontevecchia/` 1.
    #
    # La rama `-` aporta UNA sola nota —`/economia/opinion-los-municipios...`
    # de La Nación, cuyo título arranca con "Opinión."— y se mantiene a
    # sabiendas de que es la más floja: no hay regex que la distinga de un
    # titular de noticia que empiece con "opinión dividida sobre...". Se
    # conserva por la asimetría de los errores. Un falso positivo deja la nota
    # sin agrupar pero **guardada y buscable**; un falso negativo mete una
    # columna al agrupamiento y termina con la síntesis comparando una opinión
    # contra crónicas, que es justo lo que este filtro existe para evitar.
    #
    # Quedan AFUERA por cero coincidencias medidas: `/opiniones/`, `/analisis/`,
    # `/columnistas-invitados/`, `/tribuna/`, `/firmas/` y `/opinión/` con
    # tilde. Mismo criterio que sacó `signos` de acá: no se agrega lo que no
    # tiene beneficio medido, aunque parezca inofensivo.
    CATEGORIAS_NO_EVENTO: Dict[str, str] = {
        "horoscopo": r"horoscopo|zodiaco|zodiacal|astrolog",
        "recetas": r"receta",
        "juegos": r"loteria|quiniela",
        # OJO con `(?:es)?`: escrito `editoriales?` el opcional cae sobre la
        # `s` sola y el patrón pasa a exigir "editoriale", así que `/editorial/`
        # en singular no matchearía. Lo encontró un test.
        "opinion": r"/opinion[/-]|/columnistas/|/editorial(?:es)?/|/modo-fontevecchia/",
    }

    # --- Preproceso de evidencia para la síntesis (Fase 4) ---
    # Ver specs/change_logs.md, Fase 4 -- "El cálculo señala, el modelo juzga".

    # Modelo de spaCy para NER. El chico (`sm`) confundía nombres propios
    # ("Iara" por "Lara") y etiquetaba verbos como entidades; el mediano acierta
    # bastante más y sigue siendo liviano. Se instala aparte:
    #     python -m spacy download es_core_news_md
    SPACY_MODEL: str = "es_core_news_md"

    # Caracteres del cuerpo que se analizan con NER. Las entidades de un hecho
    # aparecen en los primeros párrafos; el resto suele ser contexto y cierre.
    NER_CHARS_CUERPO: int = 4000

    # Términos distintivos que se extraen por medio y para el núcleo común.
    TFIDF_TERMINOS_POR_MEDIO: int = 8

    # Cuánto tiene que crecer el corpus para reajustar el TF-IDF (0.2 = 20%).
    # Reajustar es lo caro del preproceso y los pesos no cambian con cada nota.
    TFIDF_REFIT_RATIO: float = 0.2

    # **Piso** de notas por medio: cada medio entra con al menos sus N notas más
    # representativas. Que ningún medio quede afuera es lo único que no se puede
    # resignar — sin su voz no hay comparativa. Si el piso choca con el techo de
    # abajo, gana el piso.
    SINTESIS_NOTAS_POR_MEDIO: int = 2

    # **Techo** global de notas por cluster. Antes el límite era el piso mismo, y
    # eso recortaba justo donde había más para contar: medido sobre un cluster de
    # 14 notas, mandando 6 el modelo encontró 1 ángulo, mandando 9 encontró 1, y
    # mandando las 14 encontró **3 ángulos publicables**. El tope por medio no
    # ahorraba casi nada (13 centavos al mes) y costaba dos publicaciones.
    #
    # 30 cubre entero ese caso y casi entero el peor real medido (46 notas de
    # una sola muerte), y deja el gasto acotado por arriba: ~34k tokens de
    # entrada en el peor caso, contra el millón que admite un request.
    SINTESIS_MAX_NOTAS: int = 30

    # Antigüedad a partir de la cual un cluster deja de ser candidato a
    # sintetizarse. Existe para que arrancar el sistema sobre una base con
    # historia no dispare la síntesis de todo el backlog de una.
    #
    # Antes esto era `HORAS_CLUSTER_ABIERTO * 2` (24 h), y ese acoplamiento no
    # tenía razón de ser: son dos preguntas distintas. Medido sobre datos
    # reales, con 24 h **30 clusters publicables con 85 notas se perdieron sin
    # que se intentara sintetizarlos nunca**. 72 h le da margen a una caída de
    # fin de semana largo (viernes a la noche a lunes a la mañana son ~60 h).
    #
    # Lo que caduca ya no se recupera solo: eso es a propósito —una noticia de
    # hace tres días no es noticia— pero ahora avisa en vez de desaparecer en
    # silencio. Ver `descartar_vencidos_sin_sintetizar`.
    HORAS_MAXIMAS_SIN_SINTETIZAR: int = 72

    # --- Síntesis ---
    #
    # **Acá ya no se configura ningún modelo.** El modelo, su temperatura y su
    # nivel de razonamiento viven en la tabla `modelo_ia`, que es lo que permite
    # cambiarlos sin redeployar y comparar dos configuraciones en la misma
    # instancia. Ver specs/roadmap.md, backlog punto 2, etapa 4.
    #
    # Lo único que queda del lado del entorno es la credencial, en
    # `MODELO_API_KEY` — y no como campo de `Settings`, porque no la lee la
    # configuración sino el adaptador, en el momento de usarla. Ver
    # `services/proveedores/base.leer_api_key`.

    # --- Entrega al back-end por webhook (Fase 4) ---
    # Ver specs/webhook_contract.md para el payload y specs/change_logs.md para
    # el razonamiento. Sin URL ni secreto la entrega no corre: las síntesis
    # quedan en la base con `enviado_backend=False` y salen cuando se configura.
    WEBHOOK_URL: Optional[str] = None

    # Secreto compartido con el back-end para la firma HMAC-SHA256. Nunca viaja
    # por la red: lo que se manda es una firma derivada de él.
    WEBHOOK_SECRET: Optional[str] = None

    # Segundos de espera por respuesta. Corto a propósito: el receptor solo
    # tiene que aceptar y encolar, no procesar. Si tarda más, algo le pasa y
    # conviene reintentar en la corrida siguiente antes que bloquear el paso.
    WEBHOOK_TIMEOUT: float = 10.0

    # Corridas del barrido tras las que una síntesis deja de reintentarse. El
    # barrido es idempotente y corre cada 15 minutos, así que sin tope una
    # síntesis que el backend rechaza siempre se reintentaría para siempre. Al
    # llegar acá se avisa por mail y se la deja de tomar; una re-síntesis (que
    # trae contenido nuevo) resetea el contador, y `POST /deliver?forzar=true`
    # las vuelve a incluir cuando el problema del otro lado está resuelto.
    WEBHOOK_MAX_INTENTOS: int = 5

    # --- Base de datos (Fase 5) ---
    # Ver specs/tech_stack.md, punto 2 de Escalabilidad. Quedaba sin definir a
    # propósito hasta fijar el despliegue real: el valor correcto depende de
    # cuántos procesos compiten por conexiones, y eso lo decide Fase 5 (un
    # solo proceso Uvicorn, sin `--workers` -- ver Dockerfile).

    # Conexiones que el pool mantiene siempre abiertas. Aunque los endpoints
    # son síncronos, FastAPI los corre en el threadpool de Starlette, así que
    # un solo proceso Uvicorn sí atiende varias requests a la vez, cada una
    # con su propia conexión vía `get_session`. El job del scheduler suma una
    # conexión más, sostenida durante todo el pipeline. 5 alcanza con margen:
    # hoy no hay tráfico público (ver mission.md, "no resolver problemas de
    # escala que todavía no existen").
    DB_POOL_SIZE: int = 5

    # Conexiones extra que el pool abre por encima de `DB_POOL_SIZE` ante un
    # pico, y cierra después. Con 10 más el techo de este proceso queda en 15
    # conexiones -- lejos del `max_connections` por defecto de Postgres (100),
    # con margen para conectarse a mano (psql, un script) sin agotar el pool
    # de la app.
    DB_MAX_OVERFLOW: int = 10

    # Segundos que una request espera una conexión libre del pool antes de
    # fallar. Es el valor por defecto de SQLAlchemy -- se deja explícito acá
    # para que quede documentado y no dependa de un default implícito de la
    # librería. Si esto empieza a saltar, el diagnóstico correcto es "el pool
    # quedó chico o algo lo está reteniendo", no subir el número a ciegas.
    DB_POOL_TIMEOUT: int = 30

    # Segundos tras los que una conexión se descarta y se reabre, aunque del
    # lado de la app siga viéndose viva. Protege contra conexiones que
    # Postgres (o un firewall/NAT del VPS) cierra del otro lado por
    # inactividad sin avisar: `pool_pre_ping` (ya activo) recién detecta la
    # conexión muerta al intentar usarla; esto la renueva antes de que pase.
    # 30 min es conservador para el tráfico de hoy y no genera reconexiones
    # perceptibles.
    DB_POOL_RECYCLE: int = 1800

    # Si más adelante se suman réplicas de la API (tech_stack.md, punto 4 de
    # Escalabilidad -- hoy fuera de alcance): cada réplica abre su propio
    # pool, así que N réplicas piden hasta N * (DB_POOL_SIZE + DB_MAX_OVERFLOW)
    # conexiones. Con estos valores, a partir de ~6 réplicas ya se acerca al
    # `max_connections` por defecto de Postgres (100), y ahí hay que bajar el
    # pool por réplica, subir `max_connections`, o sumar un pooler (PgBouncer)
    # -- ninguna de las tres hace falta con una sola réplica.

    # --- Extracción por URL (backlog post-1.0, punto 1) ---
    # Segunda vía de ingesta para los medios cuyo RSS no trae `content:encoded`
    # (Clarín y Perfil: 0 de 438 items, verificado el 18/08/2026). Los valores
    # salen de la medición sobre 120 artículos reales -- ver specs/change_logs.md,
    # "Backlog punto 1". Es una política de red DISTINTA a la de los feeds a
    # propósito: un feed por medio y por ciclo tolera esperas que un artículo,
    # multiplicado por decenas, no.

    # Segundos que se espera la página de un artículo. Más corto que los 15 de
    # los feeds (`ingestion.REQUEST_TIMEOUT_SECONDS`) porque acá el costo se
    # multiplica: un ciclo baja 1 feed por medio pero puede pedir decenas de
    # artículos. Medido: 0,31 s promedio en Clarín y 0,38 s en Perfil, así que
    # 10 s ya es dos órdenes de magnitud de margen sobre el caso normal.
    EXTRACCION_TIMEOUT: float = 10.0

    # Reintentos por artículo, sin backoff exponencial. Los feeds usan 3
    # intentos con espera creciente hasta ~20 s; reusar eso por artículo
    # empujaría la ingesta contra el ciclo de 15 minutos del scheduler. Con 1
    # se absorbe el error de red puntual y se abandona rápido: una nota que no
    # se pudo bajar se pierde sola, y el feed la vuelve a ofrecer en el ciclo
    # siguiente mientras siga en su ventana.
    EXTRACCION_REINTENTOS: int = 1

    # Piso de caracteres por debajo del cual lo extraído NO se considera un
    # artículo. Es la defensa contra el modo de falla propio de esta vía: si un
    # medio rediseña su maquetado, `trafilatura` no falla -- devuelve un menú o
    # un aviso de cookies que *parece* contenido y contamina embeddings y
    # prompts en silencio. Medido sobre 120 artículos: el más corto tuvo 701
    # caracteres y el percentil 10 quedó en 1.615, así que 500 deja margen
    # amplio sobre el artículo legítimo más flaco y sigue muy por encima de
    # cualquier menú.
    EXTRACCION_MIN_CARACTERES: int = 500

    # Pausa entre requests de artículo al mismo medio. Ni Clarín ni Perfil
    # declaran `crawl-delay` en su robots.txt, así que esto es cortesía y no
    # obligación: son medios que no nos conocen y pedirles decenas de páginas
    # seguidas sin respirar no es forma de presentarse.
    EXTRACCION_PAUSA_SEGUNDOS: float = 1.0


# Instancia única de configuración, importada en el resto de la aplicación.
settings = Settings()