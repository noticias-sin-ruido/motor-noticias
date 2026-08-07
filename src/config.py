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

    # Alertas de fallo de ingesta (ver CLAUDE.md, Fase 2 -- "Manejo de errores por medio").
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    ALERT_EMAIL_TO: Optional[str] = None


# Instancia única de configuración, importada en el resto de la aplicación.
settings = Settings()