# 📚 CLAUDE.md — Índice del proyecto Sin Ruido

Este archivo se carga automáticamente como contexto en cada sesión de Claude Code sobre este repo. Se mantiene corto a propósito — el contexto completo vive en `specs/`, léelo antes de hacer cambios estructurales:

- **[specs/mission.md](specs/mission.md)** — Qué es el proyecto, para quién, y qué NO hace.
- **[specs/conventions.md](specs/conventions.md)** — Cómo se escribe código acá: capas, tipos, testing, Alembic, seguridad.
- **[specs/roadmap.md](specs/roadmap.md)** — Las 5 fases del proyecto, estado actual y entregables.
- **[specs/change_logs.md](specs/change_logs.md)** — Decisiones de diseño tomadas por fase: qué se evaluó, qué se descartó y por qué.
- **[specs/tech_stack.md](specs/tech_stack.md)** — Stack tecnológico, estructura de directorios, y puntos de quiebre de arquitectura/escalabilidad a vigilar.
- **[specs/webhook_contract.md](specs/webhook_contract.md)** — Contrato de entrega al back-end: payload, firma HMAC y semántica de reintentos. Es un documento compartido con otro equipo, no lo cambies sin avisar.

**Estado: versión 1.1.0** — las 5 fases completas y la entrega al back-end probada punta a punta. La 1.1.0 cerró los puntos 1, 2, 10 y 12 del backlog (ingesta por URL, motor de IA desacoplado, token de operador y logging). En esta rama se cerró además **el punto 3** (el alta de medios la hace el operador, con sondeo del feed antes de aceptar y baja reversible), una auditoría de seguridad en tres tandas sobre los catorce endpoints, **el punto 8, parcial** (purga de cuerpos de las noticias huérfanas, `POST /purge`, corrida contra la base real con backup previo) y **el punto 6-bis, parcial**: multimodelo con cadena de fallback reactiva y elección de modelo por cluster (`POST /synthesize?modelo_id=`, `POST /clusters/{id}/synthesize`). Sin número de versión todavía. Lo que sigue está en el backlog priorizado de `specs/roadmap.md`.

Regla de oro: antes de tomar una decisión de diseño no trivial, debatila y dejala documentada en `specs/change_logs.md` — no la tomes en silencio dentro del código.
