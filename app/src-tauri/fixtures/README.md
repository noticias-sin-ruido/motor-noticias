# Fixtures del contrato con el motor

Respuestas reales del motor, congeladas para que los tests puedan deserializarlas
sin levantar nada. Son lo que hace que los structs de `tipos.rs` describan **lo
que el motor devuelve** y no lo que suponemos que devuelve.

La separación en dos carpetas es la parte importante:

- **`capturados/`** — salieron de una corrida real contra `127.0.0.1:8000` el
  **6 de septiembre de 2026**, con la base de desarrollo (485 clusters, 431
  síntesis). Son evidencia.
- **`derivados/`** — los armamos a mano leyendo el código, porque la base real
  no produce ese caso hoy o porque capturarlo costaba plata. Son un supuesto
  fundado, no evidencia, y por eso viven aparte en vez de mezclados con una
  nota al pie que nadie lee.

## Lo que la captura enseñó

Cuatro cosas que estaban mal en la cabeza antes de mirar la respuesta real:

1. **`comparativa_enfoques` es un objeto indexado por nombre de medio**
   (`{"TN": {...}, "La Nación": {...}}`), no una lista de objetos con un campo
   `medio`. Un `Vec<_>` habría fallado al deserializar.
2. **`pasos` de una corrida tiene claves en español, con acentos y espacios**:
   `síntesis`, `vectorización`, `purga de cuerpos`, `entrega al backend`. No es
   un struct: es un mapa, y sus claves las decide `_correr_paso`.
3. **Una corrida sin cerrar viene con `fin`, `duracion_segundos` y
   `utilizacion` en `null`** y `pasos` vacío. Está en `pipeline.json`, en
   `anteriores[0]`, capturado del caso real.
4. **`GET /modelos` no filtró nada de más**: los tres modelos vinieron sin
   `api_key_env` ni `base_url`, como manda `_vista_publica`. El fixture deja
   eso registrado, así que si algún día se filtran, se ve en el diff.

## Por qué falta el POST exitoso

`post_sintetizado.json` está en `derivados/` y no en `capturados/` por una razón
concreta: **sintetizar de verdad es una llamada paga al proveedor.** Los dos
desenlaces que cortan sí se capturaron de verdad, eligiendo clusters donde el
motor no puede llegar al proveedor ni queriendo:

| fixture | cluster | por qué es gratis |
|---|---|---|
| `post_sin_medios.json` | 16 | tiene **1 solo medio**, y el mínimo es 2 |
| `post_sin_material.json` | 8 | `noticias_al_sintetizar == len(noticias)` |

Las dos condiciones se comprobaron **en la base antes de mandar el POST**, no
después.

## Cómo se vuelven a capturar

Con el motor arriba y `API_TOKEN` en el `.env`, pegándole a `127.0.0.1:8000`
con el header `Authorization: Bearer`. Las rutas son las del README de la app.

**No los edites a mano para que un test pase.** Un fixture que se toca deja de
ser evidencia de nada; si el motor cambió, lo que hay que cambiar es el struct
—y entonces el test tiene que fallar primero.
