from typing import Optional

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


# Instancia única de configuración, importada en el resto de la aplicación.
settings = Settings()