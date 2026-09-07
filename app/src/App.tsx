import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import * as motor from "./motor";
import type { Estado } from "./motor";
import type { Cluster } from "./bindings/Cluster";

import BarraMotor from "./componentes/BarraMotor";
import ListaDeTrabajo from "./pantallas/ListaDeTrabajo";
import Andamio from "./Andamio";

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

/** Las vistas del cuerpo. El feed de lectura llega en la fase 5. */
type Vista = "trabajo" | "andamio";

export default function App() {
  const [hayToken, setHayToken] = useState<boolean | null>(null);
  const [hayRepo, setHayRepo] = useState<boolean | null>(null);
  const [estado, setEstado] = useState<Estado>("parado");
  const [vista, setVista] = useState<Vista>("trabajo");

  const revisar = useCallback(async () => {
    setHayToken(await invoke<boolean>("token_existe"));
    setHayRepo((await motor.repoLeer()) !== null);
  }, []);

  useEffect(() => {
    void revisar();
  }, [revisar]);

  if (hayToken === null || hayRepo === null) {
    return (
      <main className="envoltorio">
        <div className="tarjeta">Cargando…</div>
      </main>
    );
  }

  // El token y la carpeta se piden **antes** de dibujar el shell: sin ellos no
  // hay nada que la barra pueda decir, ni pantalla que pueda pedir datos.
  if (!hayToken) {
    return (
      <main className="envoltorio">
        <p className="eyebrow">Sin Ruido · cabina</p>
        <h1>Primero, el token</h1>
        <PedirToken alGuardar={() => setHayToken(true)} />
      </main>
    );
  }

  if (!hayRepo) {
    return (
      <main className="envoltorio">
        <p className="eyebrow">Sin Ruido · cabina</p>
        <h1>Dónde está el motor</h1>
        <PedirRepo alGuardar={() => setHayRepo(true)} />
      </main>
    );
  }

  return (
    <div className="marco">
      <BarraMotor
        estado={estado}
        alCambiar={setEstado}
        alOlvidarToken={() => setHayToken(false)}
      />

      <nav className="pestanas">
        <button
          className={`pestana${vista === "trabajo" ? " elegida" : ""}`}
          onClick={() => setVista("trabajo")}
        >
          Lista de trabajo
        </button>
        <button
          className={`pestana${vista === "andamio" ? " elegida" : ""}`}
          onClick={() => setVista("andamio")}
        >
          Andamio
        </button>
      </nav>

      <main className="cuerpo">
        {/* **Ninguna pantalla le pega a la API hasta `listo`.** Antes de eso el
            motor puede no estar escuchando todavía, o estar migrando la base, y
            lo que volvería sería un error de red disfrazado de problema de
            datos. */}
        {estado !== "listo" ? (
          <div className="tarjeta">
            <p className="sin-tope">
              El motor no está operativo, así que todavía no hay nada que
              pedirle. Arrancalo desde la barra de arriba.
            </p>
            <p className="ayuda">
              Estado actual: <b>{motor.describir(estado).texto}</b>.
            </p>
          </div>
        ) : vista === "trabajo" ? (
          <ListaDeTrabajo
            alVerAngulos={(c: Cluster) => {
              // La pantalla 2 es la fase 5. Hasta entonces el botón existe pero
              // no lleva a ningún lado, y decirlo es mejor que un clic mudo.
              window.alert(
                `El feed de lectura llega en la fase 5.\n\nCluster ${c.id}: ${c.titulo_evento}`,
              );
            }}
          />
        ) : (
          <Andamio />
        )}
      </main>
    </div>
  );
}
