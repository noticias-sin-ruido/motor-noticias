# ✅ Validación manual del pipeline contra Postgres real

Guía para levantar el entorno completo (Postgres + pgvector) y correr la ingesta contra los feeds RSS reales, con consultas para verificar los resultados en profundidad. Escrita originalmente al cerrar Fase 2 (Ingesta) — las queries de chequeo siguen siendo válidas hoy, pero la lista de medios y algunos números del ejemplo son de esa corrida puntual, no una foto del estado actual (ver `specs/roadmap.md` para eso). Ver `CLAUDE.md` para las decisiones de diseño detrás de cada paso.

---

## 1. Levantar Postgres + pgvector

**Requiere Docker Desktop corriendo.**

```powershell
cd "C:\Users\Usuario\Desktop\Propio\Sin Ruido\motor-noticias"
docker compose up -d
docker compose ps   # debe decir "healthy"
```

## 2. Configurar `.env`

```powershell
Copy-Item .env.example .env
```

Los valores por defecto de `DATABASE_URL` ya coinciden con las credenciales del `docker-compose.yml` (`usuario`/`password`/`sin_ruido` en `localhost:5432`). `MODELO_API_KEY` y las variables `SMTP_*` pueden quedar como están — no bloquean esta validación.

## 3. Entorno virtual y dependencias

```powershell
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
```

## 4. Aplicar el esquema con Alembic

```powershell
alembic upgrade head
```

Crea las 4 tablas, sus índices y la columna `Vector(384)` de `Noticia.embedding` (algo que SQLite en los tests nunca prueba). **Es obligatorio**: `init_db()` ya no crea tablas — solo habilita la extensión `vector` y falla con un mensaje claro si este paso falta.

## 5. Levantar la API

```powershell
uvicorn src.main:app --reload
```

No debería tirar ningún error al arrancar.

## 6. Seed de medios

En otra terminal:

```powershell
.venv\Scripts\activate
python scripts/seed_medios.py
```

Crea los 4 medios: La Nación, Clarín, TN, El Cronista.

## 7. Correr la ingesta real

```powershell
curl http://localhost:8000/
curl http://localhost:8000/clusters
curl -X POST http://localhost:8000/ingest
```

`POST /ingest` pega contra los feeds reales de los 4 medios — descarga, limpieza HTML, filtro en vivo, dedup, e inserción real en Postgres. La respuesta trae un resumen por medio: `nuevas`, `duplicadas`, `en_vivo`, `sin_contenido`, `error`.

> **`sin_contenido` alto o igual al total de items de un medio no es un error** — significa que ese medio, en ese ciclo puntual, tenía su ventana de feed dominada por contenido sin `content:encoded` (ej. horóscopos, cables de agencia sin cuerpo completo). Es esperable que varíe entre corridas; volvé a correr `POST /ingest` más tarde si un medio da 0 noticias nuevas en una corrida puntual antes de asumir que algo está roto. Si pasa de forma sostenida en varias corridas, ahí sí conviene revisar el feed de ese medio en particular.

---

## 8. Consultas de verificación en la base de datos

```powershell
docker exec -it sin_ruido_db psql -U usuario -d sin_ruido
```

Dentro de `psql` (o pegando cada línea con `docker exec -it sin_ruido_db psql -U usuario -d sin_ruido -c "..."` desde PowerShell):

**Noticias por medio** (lo primero para chequear cobertura):
```sql
SELECT m.nombre, count(n.id) AS noticias
FROM medio m
LEFT JOIN noticia n ON n.medio_id = m.id
GROUP BY m.nombre
ORDER BY noticias DESC;
```

**Últimas 10 noticias ingeridas, con su medio:**
```sql
SELECT n.id, m.nombre AS medio, n.titulo, n.fecha_publicacion
FROM noticia n
JOIN medio m ON m.id = n.medio_id
ORDER BY n.id DESC
LIMIT 10;
```

**Confirmar que el contenido es texto plano (sin HTML colado)** — si esto devuelve filas, la limpieza con BeautifulSoup falló en algún caso:
```sql
SELECT id, titulo
FROM noticia
WHERE contenido_limpio LIKE '%<%>%'
LIMIT 20;
```

**Longitud del contenido por medio** (detecta si algún medio está trayendo solo snippets cortos en vez de artículo completo — señal de que perdió `content:encoded`):
```sql
SELECT m.nombre AS medio,
       count(*) AS noticias,
       round(avg(length(n.contenido_limpio))) AS longitud_promedio,
       min(length(n.contenido_limpio)) AS longitud_minima
FROM noticia n
JOIN medio m ON m.id = n.medio_id
GROUP BY m.nombre
ORDER BY longitud_promedio ASC;
```

**Duplicados por `guid` o `url`** (debe devolver 0 filas siempre — si no, el constraint `unique` falló o se bypaseó):
```sql
SELECT guid, count(*) FROM noticia GROUP BY guid HAVING count(*) > 1;
SELECT url, count(*) FROM noticia GROUP BY url HAVING count(*) > 1;
```

**Colaron notas "en vivo"** (debe devolver 0 filas — valida el heurístico `es_en_vivo`):
```sql
SELECT id, titulo FROM noticia
WHERE titulo ILIKE '%en vivo%' OR titulo ILIKE '%minuto a minuto%' OR titulo ILIKE '%en directo%';
```

**Coló contenido de otro país vía Infobae** — ya no debería aplicar (Infobae fue reemplazado por El Cronista), pero sirve como chequeo general de que no se re-agregó un medio con mezcla de países:
```sql
SELECT n.id, n.titulo, n.url
FROM noticia n
JOIN medio m ON m.id = n.medio_id
WHERE n.url ~ '/(mexico|colombia|peru|chile|venezuela|espana)/';
```

**Extensión `vector` habilitada correctamente:**
```sql
SELECT * FROM pg_extension WHERE extname = 'vector';
```

**Columna `embedding` existe con el tipo correcto** (debe ser `vector(384)`; hoy va a estar `NULL` en todas las filas porque la vectorización es Fase 3):
```sql
SELECT column_name, data_type, udt_name
FROM information_schema.columns
WHERE table_name = 'noticia' AND column_name = 'embedding';
```

**Ver `activo` de cada medio** (por si alguno quedó desactivado sin querer y por eso no aparece en la ingesta):
```sql
SELECT nombre, feed_rss, activo FROM medio;
```

---

## 9. Volver a correr la ingesta para probar la deduplicación

```powershell
curl -X POST http://localhost:8000/ingest
```

Corré `POST /ingest` una segunda vez sin cambios: los `nuevas` deberían bajar a 0 (o casi) y `duplicadas` debería subir con las mismas noticias que ya estaban — así se valida el dedup por `guid` contra datos reales, no solo contra el mock de los tests.

---

## 10. Apagar el entorno

Los datos quedan persistidos en el volumen `sin_ruido_pgdata` para la próxima vez:

```powershell
docker compose down
```

Para arrancar de cero (borra todos los datos):
```powershell
docker compose down -v
```

---

## Checklist de cierre de Fase 2

- [ ] `docker compose up -d` levanta sano (`healthy`)
- [ ] `uvicorn` arranca sin errores (extensión `vector` + tablas creadas en Postgres real)
- [ ] `scripts/seed_medios.py` crea los medios
- [ ] `POST /ingest` trae noticias nuevas de al menos algunos medios (no hace falta que los 4 den resultado en la misma corrida — ver nota sobre `sin_contenido`)
- [ ] Sin duplicados por `guid`/`url`
- [ ] Sin notas "en vivo" coladas
- [ ] Sin HTML crudo en `contenido_limpio`
- [ ] Segunda corrida de `POST /ingest` confirma dedup (baja `nuevas`, sube `duplicadas`)