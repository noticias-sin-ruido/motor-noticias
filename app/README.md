# Cabina — la app de escritorio del operador

Interfaz de escritorio para el motor de Sin Ruido. **No lo empaqueta: lo maneja.**
Prende y apaga los contenedores que ya existen y le habla a `http://127.0.0.1:8000`.

El diseño completo —las once decisiones, con lo que se evaluó y se descartó— está
en el **punto 14** de [`specs/roadmap.md`](../specs/roadmap.md) y en
[`specs/change_logs.md`](../specs/change_logs.md). Esto es solo cómo se corre.

## Por qué maneja y no empaqueta

Empaquetar el motor arrancaba en **~2 GB antes de la primera línea de interfaz**:
el entorno de Python pesa 1,8 GB, con `torch` en 527 MB. Y había un costo peor
que el peso — habría que sacar **pgvector**, que es lo único del sistema sin plan
B escrito.

Manejando los contenedores, el motor queda tal como está: probado.

## Qué necesita

| | |
|---|---|
| Node.js | LTS |
| Rust | toolchain `stable-x86_64-pc-windows-msvc` |
| MSVC Build Tools | workload "Desktop development with C++" — Rust necesita su linker |
| WebView2 | viene con Windows 11 |
| Docker Desktop | para levantar el motor |

```powershell
winget install Microsoft.VisualStudio.2022.BuildTools --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
winget install Rustlang.Rustup
winget install OpenJS.NodeJS.LTS
```

El orden importa: Rust busca el linker de MSVC al instalarse. Después hay que
**abrir una terminal nueva** para que el `PATH` se actualice.

## Cómo se corre

```bash
cd app
npm install
npm run tauri dev
```

La primera compilación de Rust tarda varios minutos porque baja y compila el
árbol de Tauri. Las siguientes son incrementales.

## El token

La app pide el token del operador **una sola vez** y lo guarda en el
**Administrador de credenciales de Windows** (servicio `sin-ruido-motor`,
usuario `api-token`). Se puede ver y borrar a mano desde
`Panel de control → Administrador de credenciales → Credenciales de Windows`.

**No** se lee del `.env` del motor, a propósito: eso ataría la app a una ruta del
disco y metería ese archivo —que en este proyecto ya se filtró una vez— en el
camino de un segundo programa.

## Cómo está armada

**Todo el HTTP sale de Rust, no del webview.** Con `fetch` desde React el token
tendría que viajar al JavaScript para poder mandarlo en el header; haciéndolo
desde Rust, sale del Credential Manager y va directo al pedido. El front solo
hace `invoke()`. De paso no hay CORS que configurar.

```
app/
├── src/                 # React + TypeScript (strict)
│   ├── App.tsx
│   ├── motor.ts         # los estados del motor y sus mensajes
│   ├── bindings/        # GENERADO por ts-rs -- no se toca a mano
│   └── estilos.css
└── src-tauri/
    └── src/
        ├── lib.rs       # los comandos que el front invoca
        ├── secretos.rs  # el token, contra el Credential Manager
        ├── api.rs       # el cliente HTTP contra el motor
        ├── ajustes.rs   # dónde está el repo, entre arranques
        ├── docker.rs    # prender y apagar los contenedores
        ├── motor.rs     # en qué anda: la máquina de estados
        └── tipos.rs     # la forma de lo que el motor devuelve
```

## Los tipos de TypeScript no se escriben a mano

El JSON cruza **dos** fronteras —motor → Rust → ventana— y ninguna ve los tipos
de la otra, así que la misma forma se describe dos veces: una para parsearla
(el struct de Rust) y otra para consumirla (el type de TS).

Las dos copias no hacen el mismo trabajo. **El struct de Rust valida de verdad**
y puede fallar; el type de TS se borra al compilar y no valida nada. O sea que
hay una sola fuente —Rust, que define el payload— y una descripción para el
compilador. Por eso la copia de TS **la genera `ts-rs`**, en `src/bindings/`:

```bash
npm run bindings        # regenera desde los structs de Rust
npm run bindings:check  # regenera y falla si quedaron desactualizados
```

Escribirla a mano ya había fallado: un comentario en `src/motor.ts` prometía que
"si se agrega una variante en Rust y no acá, TypeScript lo hace notar en el
`switch`". Era falso — TS solo caza la dirección opuesta. Generados, la promesa
se cumple: **medido**, agregar `Estado::Pausado` solo en Rust deja el `switch`
de `describir()` sin salida y `tsc` corta con `TS2366`.

Los archivos generados **se commitean**. Ignorarlos obligaría a correr `cargo`
antes que `tsc`, y además un cambio de contrato se ve mejor en el diff que en
la ausencia de un archivo.

⚠️ La ruta de destino va repetida en cada `#[ts(export_to = ...)]` y no en un
`TS_RS_EXPORT_DIR`. Se probó la variable y se descartó: **cargo resuelve
`.cargo/config.toml` desde el directorio actual, no desde el manifiesto**, así
que `cargo test` lanzado desde `app/` la ignoraba y escribía los tipos en
`src-tauri/bindings/` sin avisar. El guardián pasaba en verde con los bindings
desactualizados.

**Los errores que vienen del motor o de Docker** cruzan a la interfaz como
**categorías cerradas** (`sin_token`, `motor_caido`, `no_autorizado`,
`demonio_caido`, …) y no como texto suelto: "falta el token" y "el motor está
apagado" piden acciones distintas de quien mira, así que la UI tiene que poder
separarlas sin parsear un mensaje. Es el mismo criterio que el motor aplicó a su
campo `agotados` después de que un mensaje de error filtrara el nombre de una
variable de entorno.

**Los comandos del token y de la ruta del repo quedan afuera a propósito**, y
devuelven un texto: `token_existe`, `token_guardar`, `token_borrar`, `repo_leer`
y `repo_guardar`. La regla nació porque distintas causas piden distintas
acciones; en estos cinco la acción es una sola en cada caso —el almacén de
credenciales de Windows falló, o esa carpeta no es el repo— y una categoría no
agregaría nada que el mensaje no diga ya. Lo que sí se cuidó es que esos
mensajes no arrastren nada sensible: **el token nunca se interpola en ellos**,
solo el error del sistema.

## La CSP está apagada, y es una decisión con fecha

`tauri.conf.json` tiene `"csp": null` — el default del scaffold. Hoy no hay
riesgo concreto: la ventana no renderiza HTML de nadie, no hay un solo
`dangerouslySetInnerHTML` en `src/`, y React escapa por defecto.

**Deja de ser inocuo en la fase 4.** Ahí entran a la pantalla titulares, URLs y
citas textuales de TN, Perfil y La Nación, más resúmenes generados por un
modelo: contenido de terceros que ninguno de nosotros escribió. La CSP es la
segunda capa para ese caso, debajo del escapado de React.

Se prende **junto con esas pantallas** y no antes, por una razón práctica: una
CSP mal puesta rompe el HMR de Vite en modo dev sin decir por qué, así que hay
que probarla abriendo la app — y conviene probarla contra el contenido real que
la justifica, no contra una ventana vacía. El valor propuesto para entonces es
`default-src 'self'`, ajustando lo que el modo dev pida.

## Docker Desktop tiene que estar corriendo

La app **detecta** que no lo está y lo dice —"Docker Desktop no está corriendo"—
en vez de escupir el `failed to connect to the docker API at npipe://…` que
devuelve Docker. Sin esa distinción, quien mira no puede saber si falló el
arranque o si nunca hubo con quién hablar: los dos casos llegaban como el mismo
error.

**Prenderlo desde la app se evaluó y se descartó.** El ejecutable no está en una
ruta fija —en esta máquina la instalación es por usuario, no en
`Archivos de programa`—, arrancarlo tarda entre 30 y 60 segundos y puede abrir
diálogos propios. Detectar cuesta un `docker info` de milisegundos y no puede
salir mal; prender es una cadena de cosas que sí. Queda como candidato si la
molestia se repite.

## Estado

**Fase 2 — el control del motor.** Además de lo de la fase 1, la app pregunta una
vez dónde está el repo —comprobando que tenga `docker-compose.yml` antes de
aceptarlo—, levanta y para los contenedores, y muestra en qué anda mientras
tanto: `reconstruyendo → arrancando → migrando → listo`.

Los estados intermedios salen de sondear `GET /`, reusando el **503** que el
motor ya devuelve cuando la base no responde como la señal de "migrando".

⚠️ Una versión anterior de este párrafo decía que el servicio `app` "no tiene
healthcheck", y es **falso**: el `Dockerfile` define uno (`curl -f` contra
`GET /`, y `-f` falla con el 503, así que ya codifica exactamente la condición
que nos interesa). Se sondea igual por otro motivo: el healthcheck de Docker
solo se lee con `docker inspect`, corre cada 30 s, y lo que la ventana necesita
es contar el progreso al ritmo de quien está mirando. Pero el motivo escrito
antes no era ése.

`up -d --build`, y `stop` — **nunca `down`**, que borra los contenedores y con la
bandera equivocada se lleva puesto el volumen de Postgres.

**Fase 3 — el cliente tipado.** Los siete endpoints tienen structs, y la ventana
ya no ve JSON crudo: pide `listar_sintesis` y recibe o los datos o una categoría
de error. `404` y `422` dejaron de colapsar en "el motor respondió un número",
que era todo lo que la pantalla podía decir de un id inexistente y de un cursor
roto por igual.

Las formas **no se dedujeron leyendo el código**: salieron de respuestas reales
congeladas en `src-tauri/fixtures/`, y corrigieron cuatro suposiciones — la más
cara, que `comparativa_enfoques` es un objeto indexado por medio y no una lista.
Ver `src-tauri/fixtures/README.md`.

Hay cuatro pruebas contra el motor de verdad, marcadas `#[ignore]` porque
necesitan los contenedores arriba y el token guardado:

```bash
cd src-tauri && cargo test -- --ignored
```

Son las únicas que ejercitan la cadena entera —Credential Manager, armado de la
query, red, deserialización—; las demás deserializan fixtures. **Ninguna manda
un POST**, para que correrlas nunca pueda terminar en una llamada paga.

**Fase 4 — el puente, cruzado (07/09/2026).** Los seis comandos que la fase 3
dejó sin invocar desde la ventana ya se ejercitaron con un andamio descartable
(`src/Andamio.tsx`): diez pruebas, cero fallidas.

Hacía falta porque entre Rust y el webview hay una capa que **ningún compilador
ve**: `cargo test` llama las funciones directo, y `tsc` tipa el retorno de
`invoke<T>()` con lo que uno le declare — no sabe qué comandos existen ni qué
argumentos piden. `invoke("comando_inexistente", { fruta: 3 })` compila perfecto.

⚠️ **Los argumentos van en camelCase**, y esto está **medido**, no supuesto: la
misma consulta con las dos grafías devolvió 1 fila con `clusterId` y 100 con
`cluster_id`, que fue ignorado.

```ts
invoke("sintetizar_cluster", { clusterId: 8, modeloId: null, forzar: false })
//                             ^^^^^^^^^ NO `cluster_id`
```

Escribirlo en snake_case compila, pasa `tsc` y falla en ejecución.
`ArgumentCase::Camel` es el default en `tauri-macros 2.6.3` (`wrapper.rs:51`,
la conversión en `:506`).

**El andamio es descartable y se borra con la pantalla de verdad.** Corre solo al
montar y es todo de lectura; el único POST está detrás de un botón aparte,
apuntando a un cluster que ya tiene síntesis para que `forzar: false` corte en
`sin_material_nuevo` sin llegar al proveedor.

**El `Set<cluster_id>` se midió y se resolvió.** Saber qué cluster ya está
sintetizado costaba **5 pedidos y 201 ms** paginando `/sintesis` antes de dibujar
una fila, creciendo con el histórico. Ahora `GET /clusters` trae
`cantidad_sintesis`: **14 ms en un pedido**, y constante. Ver el punto 14 de
`specs/roadmap.md`.

Lo que **todavía no hace**: las dos pantallas (fases 4 y 5), el ícono en la
bandeja (fase 6) y el instalador (fase 9).
