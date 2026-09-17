# 📚 CLAUDE.md — Índice del proyecto Sin Ruido

Este archivo se carga automáticamente como contexto en cada sesión de Claude Code sobre este repo. Se mantiene corto a propósito — el contexto completo vive en `specs/`, léelo antes de hacer cambios estructurales:

- **[specs/mission.md](specs/mission.md)** — Qué es el proyecto, para quién, y qué NO hace.
- **[specs/conventions.md](specs/conventions.md)** — Cómo se escribe código acá: capas, tipos, testing, Alembic, seguridad.
- **[specs/roadmap.md](specs/roadmap.md)** — Las 5 fases del proyecto, estado actual y entregables.
- **[specs/change_logs.md](specs/change_logs.md)** — Decisiones de diseño tomadas por fase: qué se evaluó, qué se descartó y por qué.
- **[specs/tech_stack.md](specs/tech_stack.md)** — Stack tecnológico, estructura de directorios, y puntos de quiebre de arquitectura/escalabilidad a vigilar.
- **[specs/seguridad.md](specs/seguridad.md)** — El legajo: qué ya se rompió y qué lo
  reabriría, qué nunca sale en una respuesta, qué endpoints exigen token siempre. Es lo
  que se le pega al encargo de una revisión de seguridad.
- **[specs/webhook_contract.md](specs/webhook_contract.md)** — Contrato de entrega al back-end: payload, firma HMAC y semántica de reintentos. Es un documento compartido con otro equipo, no lo cambies sin avisar.

**Estado.** El motor está en producción con las cinco fases cerradas, la entrega al
back-end verificada punta a punta, y una **aplicación de escritorio** que lo opera.
Motor y app se numeran juntos: un solo número para los dos.

**Acá no va el número de versión ni qué puntos del backlog están cerrados.** Los dos
son copias derivadas —la versión vive en `src/config.py`, el estado del backlog en
`specs/roadmap.md` con sus fechas— y este archivo se carga en cada sesión, así que
una copia vieja acá desinforma desde el primer turno. Ya pasó dos veces. La prueba
es simple: si una línea hay que editarla al cortar una versión, no va en este
archivo.

Regla de oro: antes de tomar una decisión de diseño no trivial, debatila y dejala documentada en `specs/change_logs.md` — no la tomes en silencio dentro del código.
