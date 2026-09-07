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

Los errores cruzan a la interfaz como **categorías cerradas**
(`sin_token`, `motor_caido`, `no_autorizado`, …) y no como texto suelto: "falta
el token" y "el motor está apagado" piden acciones distintas de quien mira, así
que la UI tiene que poder separarlas sin parsear un mensaje. Es el mismo criterio
que el motor aplicó a su campo `agotados` después de que un mensaje de error
filtrara el nombre de una variable de entorno.

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

Los estados intermedios existen porque el servicio `app` **no tiene
healthcheck**: que el contenedor esté arriba no quiere decir que la API conteste,
y menos que haya terminado el `alembic upgrade head`. Eso se sondea contra
`GET /`, reusando el **503** que el motor ya devuelve cuando la base no responde
como la señal de "migrando".

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

Lo que **todavía no hace**: las dos pantallas (fases 4 y 5), el ícono en la
bandeja (fase 6) y el instalador (fase 9).
