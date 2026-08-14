# 🤫 Sin Ruido — Backend Engine

Motor backend que agrega noticias de múltiples fuentes (RSS), agrupa por similitud semántica lo que cubre un mismo hecho, y genera una síntesis neutral con IA comparando cómo la contó cada medio — con tópicos, subtópicos y copy listo para redes sociales.

**Estado:** Fase 5 completa — motor en versión beta. Detalle de las fases y próximos pasos en [specs/roadmap.md](specs/roadmap.md).

## 📖 Documentación

| Archivo | Contenido |
|---|---|
| [specs/mission.md](specs/mission.md) | Rol, visión del proyecto, reglas de desarrollo |
| [specs/roadmap.md](specs/roadmap.md) | Las 5 fases, estado y backlog priorizado |
| [specs/change_logs.md](specs/change_logs.md) | Decisiones de diseño por fase (qué se evaluó, qué se descartó, por qué) |
| [specs/tech_stack.md](specs/tech_stack.md) | Stack tecnológico, estructura del proyecto, puntos de quiebre de escalabilidad |
| [specs/webhook_contract.md](specs/webhook_contract.md) | Contrato de entrega al back-end (payload, firma, reintentos) |
| [specs/validacion_manual.md](specs/validacion_manual.md) | Guía de validación manual contra Postgres real + queries de chequeo |

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
python scripts/seed_medios.py
uvicorn src.main:app --reload
```

Verificación rápida sin pytest: `python scripts/verify_setup.py`. Guía completa paso a paso, con queries de verificación: [specs/validacion_manual.md](specs/validacion_manual.md).

## 🧪 Tests

```bash
pytest
```
