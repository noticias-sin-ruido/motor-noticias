# El contrato entre el motor y la cabina

Qué le pide la app al motor, y qué endpoints deliberadamente no usa.

Existe porque **el motor y la app se versionan juntos pero se rompen por
separado**: renombrar un campo de una respuesta compila perfecto del lado de
Python, pasa todos sus tests, y hace que la ventana muestre una tarjeta vacía o
un error de deserialización. Ningún compilador cruza esa frontera.

Lo hace cumplir `tests/test_contrato_api.py`, del lado del motor. Este archivo
no es documentación al margen: **el test lo lee**, y una ruta nueva rompe la
suite hasta que figure acá.

## La regla: subconjunto, no igualdad

El test afirma que la respuesta **contiene al menos** los campos de la lista.
Agregar campos no rompe la app —los structs de Rust no tienen
`deny_unknown_fields`, y eso fue deliberado— pero **renombrar o borrar sí**.

El sentido es que el motor pueda crecer sin pedir permiso, y no pueda achicarse
sin avisar.

## Lo que la app consume

| método | ruta | tipo en `tipos.rs` |
|---|---|---|
| `GET` | `/` | `Salud` |
| `GET` | `/clusters` | `RespuestaClusters` |
| `GET` | `/sintesis` | `RespuestaSintesis` |
| `GET` | `/sintesis/{sintesis_id}` | `RespuestaDetalle` |
| `GET` | `/pipeline` | `RespuestaPipeline` |
| `GET` | `/modelos` | `RespuestaModelos` |
| `POST` | `/clusters/{cluster_id}/synthesize` | `RespuestaSintetizar` |
| `GET` | `/entrega` | `RespuestaEntrega` |
| `PATCH` | `/entrega` | `RespuestaEntrega` |
| `PATCH` | `/modelos/{modelo_id}` | `RespuestaActivarModelo` |
| `POST` | `/modelos` | `RespuestaAltaModelo` |

### Los campos exigidos

La lista canónica vive en `CONTRATO` dentro de `tests/test_contrato_api.py`, y
un segundo test la compara contra los bindings que `ts-rs` genera desde los
structs de Rust. Así el contrato no puede derivar de lo que la app realmente
exige: si alguien agrega un campo obligatorio en `tipos.rs` y no lo trae acá, la
suite del motor falla.

Cuatro notas sobre casos que no son obvios:

- **`GET /` informa dos booleanos que la cabina no puede deducir**:
  `exige_token`, porque `API_TOKEN` es opcional del lado del motor y sin este
  campo la app pedía un token aunque la API estuviera abierta; y
  `entrega_configurada`, para no marcar como "sin entregar" lo que no tiene a
  dónde ir. **Son booleanos y nunca la URL**: esta ruta contesta sin credencial,
  y un webhook suele llevar un identificador que no es público. Hay un test que
  lo fija (`test_la_salud_no_filtra_la_url_del_destino`).

- **`GET /sintesis/{id}` viene aplanado.** `DetalleSintesis` usa
  `#[serde(flatten)]` sobre `ResumenSintesis`, así que los campos del resumen y
  los del detalle llegan al mismo nivel, no anidados.
- **`POST .../synthesize` tiene dos formas** y el `200` no distingue cuál.
  Se eligen por presencia: la que cortó trae `motivo`, la que trabajó trae
  `creados`. `cluster_id` está en las dos y **no sirve** para discriminar.
- **`GET /modelos` es la excepción a la estrictez**: `ModeloPublico` pide sólo
  los campos que la ventana usa, porque `_vista_publica` devuelve la tabla menos
  dos columnas y exigirlos todos ataría la app a que nadie agregue una columna
  nunca.

### Cuatro rutas que el contrato vigila pero no invoca

`GET`/`PATCH /entrega` y `PATCH`/`POST /modelos` llevan `solo_forma` en el
diccionario. El guardián de deriva **sí** las cubre —renombrar un campo de esas
respuestas rompe la suite— pero `TestLosCamposLlegan` no las llama, y cada una
tiene su motivo:

- las de `/entrega` **exigen token siempre**, y la fixture de tests deja la API
  abierta a propósito, así que ahí contestarían `503`;
- las de `/modelos` **sondean al proveedor** antes de prender o guardar, y esta
  suite no sale a la red ni mockea proveedores: eso es trabajo de
  `tests/test_modelos.py`.

### Lo que el contrato NO cubre, y no puede

`GET /modelos` **no debe** devolver `api_key_env` ni `base_url`. Eso es una
prohibición, no un requisito, y un contrato de subconjunto no puede expresarla:
`tests/test_modelos.py` la vigila del lado del motor, que es donde corresponde.

### La excepción al token opcional

`GET /entrega` y `PATCH /entrega` **exigen token siempre**, aunque el motor no
tenga `API_TOKEN` definido y el resto de la API esté abierta. Es la única
excepción a la regla de `auth.py`, y la cabina la va a ver como un `503` con un
mensaje que dice qué configurar — no como un `401`.

El motivo: esos endpoints cambian a dónde salen las síntesis, y salen
**firmadas**. Quien reciba una entrega desviada obtiene contenido que parece
legítimo porque lo es.

## Lo que la app no consume

Están acá para que agregar un endpoint obligue a decidir si la cabina lo
necesita, en vez de que pase inadvertido.

Que esto hacía falta se comprobó al escribirlo: el inventario manual de rutas
se hizo con un `grep` de `get|post|put|delete` y **se comió las dos `PATCH`**.
El test, que lee `openapi.json`, las encontró en la primera corrida.

| método | ruta | por qué no |
|---|---|---|
| `GET` | `/medios` | la consola de medios quedó fuera de la v1 |
| `GET` | `/search` | la búsqueda semántica no tiene lugar en las dos pantallas |
| `POST` | `/cluster` | lo dispara el scheduler; a mano no tiene sentido |
| `POST` | `/vectorize` | ídem |
| `POST` | `/ingest` | ídem, y golpea los feeds con la identidad del operador |
| `POST` | `/synthesize` | la app sintetiza **por cluster**, no en barrido |
| `POST` | `/deliver` | la entrega al back-end la maneja el ciclo |
| `POST` | `/purge` | **irreversible**: borra cuerpos de noticias. Fuera de la v1 a propósito |
| `POST` | `/medios` | alta de medios: consola completa, fuera de la v1 |
| `PATCH` | `/medios/{medio_id}` | baja y modificación de medios: consola completa |

## Cómo se rompe a propósito

Para comprobar que el test sirve, renombrar un campo en la respuesta del motor
—por ejemplo `titulo_angulo` en `listar_sintesis`— y confirmar que la suite
falla nombrando el endpoint y el campo. Si no falla, el contrato no está
vigilando nada.
