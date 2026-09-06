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
│   └── estilos.css
└── src-tauri/
    └── src/
        ├── lib.rs       # los comandos que el front invoca
        ├── secretos.rs  # el token, contra el Credential Manager
        └── api.rs       # el cliente HTTP contra el motor
```

Los errores cruzan a la interfaz como **categorías cerradas**
(`sin_token`, `motor_caido`, `no_autorizado`, …) y no como texto suelto: "falta
el token" y "el motor está apagado" piden acciones distintas de quien mira, así
que la UI tiene que poder separarlas sin parsear un mensaje. Es el mismo criterio
que el motor aplicó a su campo `agotados` después de que un mensaje de error
filtrara el nombre de una variable de entorno.

## Estado

**Fase 1 — el esqueleto.** La app abre, pide el token si falta, y muestra el
`GET /` real del motor. Prueba la cadena entera: Tauri corre, React carga, el
token sale del Credential Manager, el HTTP llega a `localhost:8000` y la
respuesta se pinta.

Lo que **todavía no hace**: levantar los contenedores por su cuenta (fase 2),
las dos pantallas (fases 4 y 5), el ícono en la bandeja (fase 6) y el instalador
(fase 9).
