import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import * as motor from "./motor";
import Andamio from "./Andamio";
import type { Estado } from "./motor";
import type { ErrorDeApi } from "./bindings/ErrorDeApi";
import type { Salud } from "./bindings/Salud";

/**
 * El error llega como categoría cerrada desde Rust, no como texto suelto:
 * `sin_token` y `motor_caido` piden acciones distintas de quien mira, así que
 * la interfaz tiene que poder distinguirlas sin parsear un mensaje. El tipo lo
 * genera `ts-rs` desde el enum de Rust.
 */
function mensajeDe(error: ErrorDeApi): string {
  switch (error.tipo) {
    case "sin_token":
      return "Falta configurar el token del motor.";
    case "motor_caido":
      return "El motor no responde.";
    case "no_autorizado":
      return "El motor rechazó el token. Puede haber cambiado en el .env.";
    case "no_encontrado":
      return "El motor no encontró eso. Puede haberse borrado; probá refrescar.";
    case "invalida":
      return `El motor rechazó el pedido: ${error.detalle}`;
    case "respuesta":
      return `El motor respondió ${error.detalle}.`;
    case "red":
      return `Error de red: ${error.detalle}`;
  }
}

function PedirToken({ alGuardar }: { alGuardar: () => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);

  async function guardar(evento: React.FormEvent) {
    evento.preventDefault();
    setGuardando(true);
    setError(null);
    try {
      await invoke("token_guardar", { token });
      alGuardar();
    } catch (e) {
      setError(String(e));
      setGuardando(false);
    }
  }

  return (
    <form className="tarjeta" onSubmit={guardar}>
      <label htmlFor="token">Token del operador</label>
      <input
        id="token"
        type="password"
        value={token}
        onChange={(e) => setToken(e.target.value)}
        placeholder="El valor de API_TOKEN en el .env del motor"
        autoFocus
      />
      <p className="ayuda">
        Se guarda en el <b>Administrador de credenciales de Windows</b>, no en un
        archivo de la app. Se pide una sola vez.
      </p>
      {error && <div className="aviso">{error}</div>}
      <div className="acciones">
        <button type="submit" disabled={!token.trim() || guardando}>
          {guardando ? "Guardando…" : "Guardar"}
        </button>
      </div>
    </form>
  );
}

function PedirRepo({ alGuardar }: { alGuardar: () => void }) {
  const [ruta, setRuta] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function guardar(evento: React.FormEvent) {
    evento.preventDefault();
    setError(null);
    try {
      await motor.repoGuardar(ruta.trim());
      alGuardar();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <form className="tarjeta" onSubmit={guardar}>
      <label htmlFor="repo">Carpeta del repo del motor</label>
      <input
        id="repo"
        type="text"
        value={ruta}
        onChange={(e) => setRuta(e.target.value)}
        placeholder="La carpeta que contiene docker-compose.yml"
        autoFocus
      />
      <p className="ayuda">
        Se comprueba que tenga un <code>docker-compose.yml</code> antes de
        guardarla, así el error sale ahora —cuando podés corregirlo— y no dos
        pantallas después, cuando el arranque falle sin decir por qué.
      </p>
      {error && <div className="aviso">{error}</div>}
      <div className="acciones">
        <button type="submit" disabled={!ruta.trim()}>
          Guardar
        </button>
      </div>
    </form>
  );
}

function Cabina({ alOlvidarToken }: { alOlvidarToken: () => void }) {
  const [estado, setEstado] = useState<Estado>("parado");
  const [salud, setSalud] = useState<Salud | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const desuscribir = useRef<(() => void) | null>(null);

  // Rust avisa cada transición del arranque; sin esto la ventana no tendría
  // nada que mostrar entre "reconstruyendo" y "listo", que pueden ser minutos.
  useEffect(() => {
    void motor.alCambiarEstado(setEstado).then((off) => {
      desuscribir.current = off;
    });
    return () => desuscribir.current?.();
  }, []);

  const mirarSalud = useCallback(async () => {
    try {
      setSalud(await invoke<Salud>("motor_salud"));
      setError(null);
    } catch (e) {
      setSalud(null);
      setError(mensajeDe(e as ErrorDeApi));
    }
  }, []);

  const sondear = useCallback(async () => {
    const actual = await motor.estadoActual();
    setEstado(actual);
    if (actual === "listo") await mirarSalud();
  }, [mirarSalud]);

  useEffect(() => {
    void sondear();
  }, [sondear]);

  async function arrancar() {
    setOcupado(true);
    setError(null);
    try {
      const final = await motor.arrancar();
      setEstado(final);
      if (final === "listo") await mirarSalud();
    } catch (e) {
      setError(motor.mensajeDeRechazo(e));
      setEstado("error");
    } finally {
      setOcupado(false);
    }
  }

  async function detener() {
    setOcupado(true);
    setError(null);
    try {
      setEstado(await motor.detener());
      setSalud(null);
    } catch (e) {
      setError(motor.mensajeDeRechazo(e));
    } finally {
      setOcupado(false);
    }
  }

  async function olvidar() {
    await invoke("token_borrar");
    alOlvidarToken();
  }

  const { texto, clase } = motor.describir(estado);

  return (
    <>
      <div className="tarjeta">
        <dl className="fila">
          <dt>Motor</dt>
          <dd>
            <span className={`estado ${clase}`}>{texto}</span>
          </dd>
          {salud && (
            <>
              <dt>Base</dt>
              <dd>{salud.database}</dd>
              <dt>Entorno</dt>
              <dd>{salud.environment}</dd>
              <dt>Hora del motor</dt>
              <dd>{salud.hora_local}</dd>
            </>
          )}
        </dl>
        {error && <div className="aviso">{error}</div>}
      </div>

      <div className="acciones">
        <button
          onClick={() => void arrancar()}
          disabled={ocupado || estado === "listo"}
        >
          {ocupado ? "Trabajando…" : "Arrancar motor"}
        </button>
        <button
          className="secundario"
          onClick={() => void detener()}
          disabled={ocupado || estado === "parado"}
        >
          Detener motor
        </button>
        <button
          className="secundario"
          onClick={() => void sondear()}
          disabled={ocupado}
        >
          Actualizar
        </button>
        <button className="secundario" onClick={() => void olvidar()}>
          Olvidar token
        </button>
      </div>
    </>
  );
}

export default function App() {
  const [hayToken, setHayToken] = useState<boolean | null>(null);
  const [hayRepo, setHayRepo] = useState<boolean | null>(null);
  // Fase 4: el andamio que cruza el puente `invoke()`. Es temporal y se va
  // junto con la pantalla de verdad.
  const [andamio, setAndamio] = useState(false);

  const revisar = useCallback(async () => {
    setHayToken(await invoke<boolean>("token_existe"));
    setHayRepo((await motor.repoLeer()) !== null);
  }, []);

  useEffect(() => {
    void revisar();
  }, [revisar]);

  const cargando = hayToken === null || hayRepo === null;

  return (
    <main className="envoltorio">
      <p className="eyebrow">Sin Ruido · cabina</p>
      <h1>{andamio ? "El puente invoke()" : "Estado del motor"}</h1>
      {cargando ? (
        <div className="tarjeta">Cargando…</div>
      ) : !hayToken ? (
        <PedirToken alGuardar={() => setHayToken(true)} />
      ) : !hayRepo ? (
        <PedirRepo alGuardar={() => setHayRepo(true)} />
      ) : andamio ? (
        <Andamio />
      ) : (
        <Cabina alOlvidarToken={() => setHayToken(false)} />
      )}
      {!cargando && hayToken && hayRepo && (
        <div className="acciones">
          <button className="secundario chico" onClick={() => setAndamio(!andamio)}>
            {andamio ? "volver a la cabina" : "andamio de la fase 4"}
          </button>
        </div>
      )}
    </main>
  );
}
