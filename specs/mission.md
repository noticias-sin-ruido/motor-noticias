# 🎯 Mission — Sin Ruido

Qué es este proyecto y qué no. Cómo se escribe código acá vive en
[conventions.md](conventions.md); cómo trabajamos vive en las skills del método y
no se repite en ningún archivo del repo.

## Qué es Sin Ruido

**Sin Ruido** es un **motor backend para ingesta, vectorización y síntesis neutra de noticias**.

### Objetivo

Agregar noticias de múltiples fuentes (RSS feeds), vectorizarlas, agruparlas por similitud semántica, y generar síntesis neutrales con comparativa de enfoques editoriales.

### Público objetivo

Usuarios que desean entender eventos noticiosos sin sesgos editoriales, viendo cómo cada medio reporta el mismo hecho.

### Qué NO hace este motor

Compara enfoques editoriales sobre **un mismo hecho** y entrega síntesis. **Si no hay hecho, no es su trabajo.** Los horóscopos, las recetas y la quiniela se clasifican y quedan fuera del agrupamiento (`services/categorias.py`): no se pierden, pero qué se hace con ellos lo resuelve el back-end del producto. Meter acá un circuito para contenido sin hecho mezclaría dos productos distintos en el mismo motor.

### Principio rector

Este proyecto prioriza **claridad sobre optimalidad prematura**. No sobre-ingenierices: preferí tres líneas parecidas antes que una abstracción prematura, y **no resuelvas problemas de escala que todavía no existen** (ver `tech_stack.md`, sección "Arquitectura y Escalabilidad", para los que sí están identificados y a propósito pospuestos).

`specs/` es la fuente de verdad del proyecto, no el código por sí solo: se mantiene al día en el mismo cambio que toca el código, no después.
