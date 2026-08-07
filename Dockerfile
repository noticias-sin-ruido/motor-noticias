# 1. Imagen base ligera de Python 3.12
FROM python:3.12-slim

# Evita que Python escriba archivos .pyc y fuerza el stdout sin buffer para ver logs en tiempo real
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Establecer el directorio de trabajo dentro del contenedor
WORKDIR /app

# 2. Instalar dependencias del sistema necesarias para compilar algunas librerías
#    - build-essential: compilación general (numpy, scikit-learn, etc.)
#    - libxml2-dev / libxslt1-dev: requeridos por lxml (dependencia de newspaper4k)
#      si no hay wheel precompilada disponible para la arquitectura del build (ej. arm64)
#    - curl: usado por el HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 3. Copiar únicamente el archivo de requisitos primero (aprovecha la caché de capas de Docker)
COPY requirements.txt .

# 4. Instalar las dependencias de Python y el modelo de SpaCy para NLP en español
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    python -m spacy download es_core_news_md

# 5. Copiar el resto del código fuente del proyecto
#    (.dockerignore evita que se copien .env, .git, __pycache__, etc.)
COPY . .

# 6. Crear un usuario sin privilegios y correr la app con él (buena práctica de seguridad)
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Expone el puerto por defecto de FastAPI
EXPOSE 8000

# Verifica periódicamente que la API responda
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# 7. Comando para iniciar la aplicación backend con Uvicorn
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
