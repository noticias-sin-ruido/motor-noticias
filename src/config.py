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
    GEMINI_API_KEY: Optional[str] = None
    ENVIRONMENT: str = "development"

    # Alertas de fallo de ingesta (ver specs/change_logs.md, Fase 2 --
    # "Manejo de errores por medio").
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    ALERT_EMAIL_TO: Optional[str] = None

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
    CATEGORIAS_NO_EVENTO: Dict[str, str] = {
        "horoscopo": r"horoscopo|zodiaco|zodiacal|astrolog",
        "recetas": r"receta",
        "juegos": r"loteria|quiniela",
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

    # --- Síntesis con Gemini (Fase 4) ---
    # Medido: 68.534 tokens de entrada para 21 clusters publicables, o sea del
    # orden de US$0,01 por corrida completa. Verificá los precios vigentes.
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"

    # Baja a propósito: la tarea es resumir sin inventar, no escribir bonito.
    GEMINI_TEMPERATURA: float = 0.2

    # Cuánto razonamiento previo hace el modelo: MINIMAL, LOW, MEDIUM o HIGH.
    # Importa porque **los tokens de razonamiento se facturan como salida**, y
    # la salida es ~80% del costo de esta fase.
    #
    # Medido contra gemini-3.5-flash-lite con un prompt corto: MINIMAL y LOW
    # gastan 0 tokens de razonamiento, MEDIUM 349 y HIGH 448. Se usa LOW porque
    # no cuesta nada en las tareas simples y deja margen para escalar solo
    # cuando el caso lo pide.
    #
    # NO usar `thinking_budget`: este modelo rechaza el 0 con un 400 genérico
    # (verificado). `thinking_level` es la palanca que sí acepta.
    GEMINI_THINKING_LEVEL: str = "LOW"

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