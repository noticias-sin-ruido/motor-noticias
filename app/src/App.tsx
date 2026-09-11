import { useCallback, useEffect, useRef, useState } from "react";

import * as datos from "./datos";
import * as motor from "./motor";
import type { Estado } from "./motor";
import type { Cluster } from "./bindings/Cluster";

import BarraMotor from "./componentes/BarraMotor";
import DialogoSalida from "./componentes/DialogoSalida";
import ListaDeTrabajo from "./pantallas/ListaDeTrabajo";
import FeedDeLectura from "./pantallas/FeedDeLectura";
import Ajustes from "./pantallas/Ajustes";
import Medios from "./pantallas/Medios";
import Modelos from "./pantallas/Modelos";

function PedirToken({ alGuardar }: { alGuardar: () => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);

  async function guardar(evento: React.FormEvent) {
    evento.preventDefault();
    setGuardando(true);
    setError(null);
    try {
      await motor.tokenGuardar(token);
      alGuardar();
    } catch (e) {
      // `mensajeDeRechazo` y no `String(e)`: si el comando pasa a devolver un
      // error con forma —como acaba de pasarle a `ruta_configurada`— `String`
      // muestra "[object Object]" y nadie se entera hasta verlo en pantalla.
      setError(motor.mensajeDeRechazo(e));
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
      setError(motor.mensajeDeRechazo(e));
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

/** Las vistas del cuerpo. */
type Vista = "trabajo" | "feed" | "medios" | "modelos" | "ajustes";

export default function App() {
  const [hayToken, setHayToken] = useState<boolean | null>(null);
  const [hayRepo, setHayRepo] = useState<boolean | null>(null);
  // **`null` es "todavía no se sabe", y no es lo mismo que `false`.** Sólo el
  // motor puede decir si pide token, así que hasta que conteste no se le puede
  // pedir a nadie que lo pegue ni darlo por innecesario.
  const [exigeToken, setExigeToken] = useState<boolean | null>(null);
  // Si el motor tiene a donde entregar. Lo consume el feed, que sin esto marca
  // como pendiente una entrega que nunca va a ocurrir.
  const [entregaConfigurada, setEntregaConfigurada] = useState(false);
  const [estado, setEstado] = useState<Estado>("parado");
  const [vista, setVista] = useState<Vista>("trabajo");
  // Con qué cluster entrar al feed. Vive acá y no adentro del feed porque
  // lo decide la otra pantalla: es el único dato que cruza entre las dos.
  const [clusterDelFeed, setClusterDelFeed] = useState<number | null>(null);
  // Lo que la bandeja o la cruz pidieron. `null` es "nadie pidió salir".
  const [salida, setSalida] = useState<motor.PedidoDeSalida | null>(null);
  const desuscribirSalida = useRef<(() => void) | null>(null);

  /**
   * Le pregunta al motor si pide token. **`GET /` es la única ruta abierta**,
   * así que contesta sin credencial — que es exactamente para lo que se la usa
   * acá: averiguar si hace falta el token *antes* de pedirlo.
   *
   * Si el motor no contesta queda en `null` y no se pide nada: no se sabe.
   */
  const sondearSalud = useCallback(async () => {
    try {
      const salud = await datos.salud();
      setExigeToken(salud.exige_token);
      setEntregaConfigurada(salud.entrega_configurada);
    } catch {
      setExigeToken(null);
      // Con el motor caido no se sabe, y **`false` es lo que conviene**: hace
      // que el feed no prometa una entrega que nadie puede confirmar.
      setEntregaConfigurada(false);
    }
  }, []);

  const revisar = useCallback(async () => {
    setHayToken(await motor.tokenExiste());
    setHayRepo((await motor.repoLeer()) !== null);
    await sondearSalud();
  }, [sondearSalud]);

  useEffect(() => {
    void revisar();
  }, [revisar]);

  // **El segundo sondeo, y hace falta.** El primero corre al abrir la ventana,
  // cuando el motor puede estar apagado: ahí `exigeToken` queda en `null` y no
  // se pide token. Cuando el motor termina de arrancar recién ahí se sabe, y
  // este efecto es lo que convierte ese "no se sabe" en una respuesta.
  useEffect(() => {
    if (estado === "listo") void sondearSalud();
  }, [estado, sondearSalud]);

  // **La suscripción vive acá y no adentro del diálogo**, porque el diálogo no
  // existe hasta que llega el pedido: si escuchara él, no habría nadie oyendo
  // cuando alguien aprieta la cruz.
  useEffect(() => {
    void motor.alPedirSalida(setSalida).then((off) => {
      desuscribirSalida.current = off;
    });
    return () => desuscribirSalida.current?.();
  }, []);

  // **Un solo `return`, y es la corrección de un bug que ya mordió.**
  //
  // Antes cada una de estas pantallas tempranas hacía su propio `return`, y el
  // `DialogoSalida` vivía sólo en el último. Resultado: en «Cargando», en la
  // carpeta y en el token, Rust hacía `prevent_close`, emitía el pedido, React
  // lo guardaba... y no había nadie que lo dibujara. **La ventana no se podía
  // cerrar**, y como la bandeja tampoco ejecuta nada por su cuenta —le avisa a
  // la ventana y la ventana hace el trabajo—, su menú tampoco servía. Sólo se
  // salía por el Administrador de tareas.
  //
  // El comentario de abajo ya decía que el diálogo tenía que llegar «hasta
  // antes de que haya token». El código lo desmentía. Armar el cuerpo en una
  // variable en vez de retornar temprano hace que **no se pueda volver a
  // escribir mal**: no hay ningún camino que saltee el diálogo.
  let cuerpo: React.ReactNode;

  if (hayToken === null || hayRepo === null) {
    cuerpo = (
      <main className="envoltorio">
        <div className="tarjeta">Cargando…</div>
      </main>
    );
  } else if (!hayRepo) {
    // **La carpeta va primero, y el orden es la decisión.** Antes se pedía el
    // token antes que nada, y con eso la app no podía enterarse nunca de que no
    // le hacía falta: para saberlo hay que preguntarle al motor, para
    // preguntarle tiene que estar corriendo, y para arrancarlo hace falta esta
    // carpeta.
    cuerpo = (
      <main className="envoltorio">
        <p className="eyebrow">Sin Ruido · cabina</p>
        <h1>Dónde está el motor</h1>
        <PedirRepo alGuardar={() => setHayRepo(true)} />
      </main>
    );
  } else if (exigeToken === true && !hayToken) {
    // Sólo si el motor dijo que lo exige. Con `null` —el motor todavía no
    // contestó— se sigue de largo: la ventana se abre, se arranca el motor
    // desde la barra, y el efecto de arriba vuelve a preguntar.
    cuerpo = (
      <main className="envoltorio">
        <p className="eyebrow">Sin Ruido · cabina</p>
        <h1>El motor pide token</h1>
        <PedirToken
          alGuardar={() => {
            setHayToken(true);
          }}
        />
      </main>
    );
  } else {
    cuerpo = (
      <div className="marco">
      {/* La barra y las pestañas se pegan **juntas**, envueltas en un solo
          contenedor `sticky`. Pegarlas por separado obligaría a que la segunda
          conozca el alto exacto de la primera, y ese número se desactualiza en
          silencio en cuanto cambia el logo o el alto de un botón. */}
      <div className="cabecera">
        <BarraMotor estado={estado} alCambiar={setEstado} />

        <nav className="pestanas">
          <button
            className={`pestana${vista === "trabajo" ? " elegida" : ""}`}
            onClick={() => setVista("trabajo")}
          >
            Lista de trabajo
          </button>
          <button
            className={`pestana${vista === "feed" ? " elegida" : ""}`}
            onClick={() => setVista("feed")}
          >
            Feed de lectura
          </button>
          <button
            className={`pestana${vista === "medios" ? " elegida" : ""}`}
            onClick={() => setVista("medios")}
          >
            Medios
          </button>
          <button
            className={`pestana${vista === "modelos" ? " elegida" : ""}`}
            onClick={() => setVista("modelos")}
          >
            Modelos
          </button>
          <button
            className={`pestana${vista === "ajustes" ? " elegida" : ""}`}
            onClick={() => setVista("ajustes")}
          >
            Ajustes
          </button>
        </nav>
      </div>

      <main className="cuerpo">
        {/* **Ajustes es la excepción, y es el punto de que exista.** Las otras
            pantallas no tienen nada que mostrar sin motor; ésta es donde se
            arregla que el motor no arranque —la carpeta que se movió, el token
            que cambió—, así que bloquearla hasta que el motor esté listo la
            haría inútil justo cuando hace falta. */}
        {vista === "ajustes" ? (
          <Ajustes
            hayToken={hayToken}
            exigeToken={exigeToken}
            motorListo={estado === "listo"}
            alCambiarToken={() => {
              setHayToken(false);
              void sondearSalud();
            }}
            alCambiarEntrega={() => void sondearSalud()}
          />
        ) : estado !== "listo" ? (
          /* **Ninguna otra pantalla le pega a la API hasta `listo`.** Antes de
             eso el motor puede no estar escuchando todavía, o estar migrando la
             base, y lo que volvería sería un error de red disfrazado de
             problema de datos. */
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
              setClusterDelFeed(c.id);
              setVista("feed");
            }}
          />
        ) : vista === "feed" ? (
          <FeedDeLectura
            clusterId={clusterDelFeed}
            entregaConfigurada={entregaConfigurada}
            alQuitarFiltro={() => setClusterDelFeed(null)}
          />
        ) : vista === "medios" ? (
          <Medios />
        ) : (
          <Modelos />
        )}
      </main>
      </div>
    );
  }

  return (
    <>
      {cuerpo}

      {/* **Fuera del cuerpo, y ahora de verdad.** Puede llegar con el motor
          apagado, con la ventana en cualquier pestaña, y —esto es lo que antes
          no se cumplía— antes de que haya token o carpeta. Ahí es donde más
          falta hace: es la única forma de cerrar la ventana, porque Rust hace
          `prevent_close` y la bandeja delega en la ventana en vez de ejecutar
          por su cuenta. */}
      {salida !== null && (
        <DialogoSalida pedido={salida} alCancelar={() => setSalida(null)} />
      )}
    </>
  );
}
