# 🤫 Sin Ruido — Backend Engine

Motor backend para ingesta, vectorización y síntesis neutra de noticias: agrega noticias de múltiples fuentes (RSS), las agrupa por similitud semántica, y genera una síntesis neutral comparando cómo cada medio enfoca el mismo hecho.

**Estado:** Fase 2 (Ingesta de Noticias) ✅ completa. Ver [specs/roadmap.md](specs/roadmap.md) para el detalle de las 5 fases.

## 📖 Documentación

| Archivo | Contenido |
|---|---|
| [specs/mission.md](specs/mission.md) | Rol, visión del proyecto, reglas de desarrollo |
| [specs/roadmap.md](specs/roadmap.md) | Las 5 fases, estado y entregables |
| [specs/change_logs.md](specs/change_logs.md) | Decisiones de diseño por fase (qué se evaluó, qué se descartó, por qué) |
| [specs/tech_stack.md](specs/tech_stack.md) | Stack tecnológico, estructura del proyecto, puntos de quiebre de escalabilidad |
| [QUICK_START.md](QUICK_START.md) | Cómo correr las pruebas de Fase 1 en minutos |
| [VALIDACION_FASE2.md](VALIDACION_FASE2.md) | Guía de validación de la ingesta contra Postgres real + queries de chequeo |
| [TESTING.md](TESTING.md) | Guía detallada de tests |

## 🚀 Inicio rápido (entorno local)

```bash
git clone https://github.com/noticias-sin-ruido/motor-noticias.git
cd motor-noticias

python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt -r requirements-dev.txt

docker compose up -d            # Postgres + pgvector
copy .env.example .env          # completar credenciales si hace falta

alembic upgrade head            # crea el esquema (obligatorio)
python seed_medios.py
uvicorn src.main:app --reload
```

Guía completa, paso a paso, con queries de verificación: [VALIDACION_FASE2.md](VALIDACION_FASE2.md).

## 🧪 Tests

```bash
pytest
```
