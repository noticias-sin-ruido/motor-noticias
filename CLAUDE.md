# 📚 CLAUDE.md — Índice del proyecto Sin Ruido

Este archivo se carga automáticamente como contexto en cada sesión de Claude Code sobre este repo. Se mantiene corto a propósito — el contexto completo vive en `specs/`, léelo antes de hacer cambios estructurales:

- **[specs/mission.md](specs/mission.md)** — Rol, visión del proyecto, reglas de desarrollo y buenas prácticas.
- **[specs/roadmap.md](specs/roadmap.md)** — Las 5 fases del proyecto, estado actual y entregables.
- **[specs/change_logs.md](specs/change_logs.md)** — Decisiones de diseño tomadas por fase: qué se evaluó, qué se descartó y por qué.
- **[specs/tech_stack.md](specs/tech_stack.md)** — Stack tecnológico, estructura de directorios, y puntos de quiebre de arquitectura/escalabilidad a vigilar.
- **[specs/webhook_contract.md](specs/webhook_contract.md)** — Contrato de entrega al back-end: payload, firma HMAC y semántica de reintentos. Es un documento compartido con otro equipo, no lo cambies sin avisar.

**Fase actual:** Fase 4 (Síntesis Neutra con IA) ✅ completa. **Siguiente:** Fase 5 (Deployment y Escalabilidad). Detalle en `specs/roadmap.md`.

Regla de oro: antes de tomar una decisión de diseño no trivial, debatila y dejala documentada en `specs/change_logs.md` — no la tomes en silencio dentro del código.
