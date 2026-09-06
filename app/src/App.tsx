import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

/** Lo que devuelve `GET /` del motor. */
type Salud = {
  status: string;
  database: string;
  environment: string;
  hora_local: string;
};

/**
 * El error llega como categoría cerrada desde Rust, no como texto suelto.
 * `SinToken` y `MotorCaido` piden acciones distintas de quien mira, así que la
 * interfaz tiene que poder distinguirlas sin leer un mensaje.
 */
type ErrorDeApi =
  | { tipo: "sin_token" }
  | { tipo: "motor_caido" }
  | { tipo: "no_autorizado" }
  | { tipo: "respuesta"; detalle: number }
  | { tipo: "red"; detalle: string };

function mensajeDe(error: ErrorDeApi): string {
  switch (error.tipo) {
    case "sin_token":
      return "Falta configurar el token del motor.";
    case "motor_caido":
      return "El motor no responde. Todavía no puedo levantarlo solo — eso llega en la fase siguiente.";
    case "no_autorizado":
      return "El motor rechazó el token. Puede haber cambiado en el .env.";
    case "respuesta":
      return `El motor respondió ${error.detalle}.`;
    case "red":
      return `Error de red: ${error.detalle}`;
  }
}

/** Pide el token una vez y lo manda al Credential Manager. */
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

/** El estado del motor, que es lo único que muestra esta fase. */
function EstadoDelMotor({ alOlvidarToken }: { alOlvidarToken: () => void }) {
  const [salud, setSalud] = useState<Salud | null>(null);
  const [error, setError] = useState<ErrorDeApi | null>(null);
  const [cargando, setCargando] = useState(true);

  const consultar = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      setSalud(await invoke<Salud>("motor_salud"));
    } catch (e) {
      setSalud(null);
      setError(e as ErrorDeApi);
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void consultar();
  }, [consultar]);

  async function olvidar() {
    await invoke("token_borrar");
    alOlvidarToken();
  }

  // `status` viene "ok" o "degradado"; el 503 con "degradado" significa que la
  // API está viva y la base no. Son estados distintos y se pintan distinto.
  const clase =
    error !== null ? "error" : salud?.status === "ok" ? "ok" : "alerta";
  const rotulo =
    error !== null
      ? "sin contacto"
      : salud?.status === "ok"
        ? "operativo"
        : "degradado";

  return (
    <>
      <div className="tarjeta">
        <dl className="fila">
          <dt>Motor</dt>
          <dd>
            <span className={`estado ${clase}`}>
              {cargando ? "consultando…" : rotulo}
            </span>
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
        {error && <div className="aviso">{mensajeDe(error)}</div>}
      </div>
      <div className="acciones">
        <button onClick={() => void consultar()} disabled={cargando}>
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

  const revisar = useCallback(async () => {
    setHayToken(await invoke<boolean>("token_existe"));
  }, []);

  useEffect(() => {
    void revisar();
  }, [revisar]);

  return (
    <main className="envoltorio">
      <p className="eyebrow">Sin Ruido · cabina</p>
      <h1>Estado del motor</h1>
      {hayToken === null ? (
        <div className="tarjeta">Cargando…</div>
      ) : hayToken ? (
        <EstadoDelMotor alOlvidarToken={() => setHayToken(false)} />
      ) : (
        <PedirToken alGuardar={() => setHayToken(true)} />
      )}
    </main>
  );
}
