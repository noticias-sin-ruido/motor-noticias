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
import Actividad from "./pantallas/Actividad";
import Problemas from "./pantallas/Problemas";
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
        Se guarda en el <b>Administrador de credenciales de Windows</b>, no en
        un archivo de la app. Se pide una sola vez.
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
/**
 * Si un evento es posterior a la marca de «ya lo vi».
 *
 * **Compara instantes y no texto, y eso lo decidió un incidente de veinte
 * minutos.** La primera versión comparaba las cadenas ISO directamente: andaba,
 * porque el motor mandaba UTC sin sufijo (`2026-09-12T20:16:08`) y lo que
 * `toISOString()` agrega de más va después de los segundos. Andaba **de
 * casualidad**.
 *
 * Al arreglar otra cosa --que el motor devolviera las fechas en UTC-3 con el
 * offset, como manda `src/tiempo.py`-- pasó a mandar `2026-09-12T17:16:08-03:00`
 * y la comparación de texto se rompió sin avisar: «17» siempre es menor que
 * «21», así que ningún evento contaba como nuevo y la burbuja quedaba apagada
 * para siempre.
 *
 * `Date` resuelve las dos formas al mismo instante **siempre que la cadena
 * traiga la zona**, y ahí está la parte que conecta los dos arreglos: un ISO
 * sin offset lo interpreta como hora **local**, no como UTC. Comprobado:
 * `2026-09-12T20:16:08` --que el motor mandaba pensándolo en UTC-- lo leía como
 * las 20:16 de Buenos Aires, tres horas corrido.
 *
 * O sea que devolver `-03:00` no era cosmética para que se lea lindo: **es lo
 * que hace que la fecha signifique una sola cosa** para cualquiera que la
 * consuma. Es la regla de `src/tiempo.py`, y este es el caso que la justifica.
 */
function esPosterior(momento: string, marca: string): boolean {
  return new Date(momento).getTime() > new Date(marca).getTime();
}

type Vista =
  | "trabajo"
  | "feed"
  | "medios"
  | "modelos"
  | "actividad"
  | "problemas"
  | "ajustes";

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
  /** Cuántos problemas registrados. Alimenta el contador de la pestaña. */
  const [problemas, setProblemas] = useState(0);
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
      setProblemas(0);
      setExigeToken(null);
      // Con el motor caido no se sabe, y **`false` es lo que conviene**: hace
      // que el feed no prometa una entrega que nadie puede confirmar.
      setEntregaConfigurada(false);
    }
  }, []);

  /**
   * Cuántos problemas hay, para el contador de la pestaña.
   *
   * **Va aparte del sondeo de salud y no adentro**, aunque los dos se disparen
   * juntos: `GET /` es ruta abierta y `GET /eventos` exige token siempre. Si
   * fueran el mismo pedido, un motor sin credencial haría fallar el sondeo que
   * justamente sirve para averiguar si hace falta credencial.
   *
   * Que falle no rompe nada: el contador se apaga y la pestaña sigue ahí.
   */
  const contarProblemas = useCallback(async () => {
    try {
      const [r, vistoHasta] = await Promise.all([
        datos.listarEventos(null),
        motor.problemasVistosLeer(),
      ]);
      // **Se cuenta lo que pasó DESDE la última revisión, no todo.** Sin esto
      // la burbuja sería acumulativa y no se apagaría nunca: los eventos viven
      // 90 días, y no son cosas que se arreglen sino que se revisen.
      //
      // Y como se compara contra `ultima_vez`, **un evento que vuelve a ocurrir
      // vuelve a contar**. Eso es la propiedad que se busca y no un efecto
      // secundario: un feed que falló, se revisó, y falla de nuevo mañana tiene
      // que avisar otra vez. Un «descartar» lo escondería.
      const nuevos = vistoHasta
        ? r.eventos.filter((e) => esPosterior(e.ultima_vez, vistoHasta))
        : r.eventos;
      setProblemas(nuevos.length);
    } catch {
      setProblemas(0);
    }
  }, []);

  const revisar = useCallback(async () => {
    setHayToken(await motor.tokenExiste());
    setHayRepo((await motor.repoLeer()) !== null);
    await sondearSalud();
    await contarProblemas();
  }, [sondearSalud, contarProblemas]);

  useEffect(() => {
    void revisar();
  }, [revisar]);

  // **El segundo sondeo, y hace falta.** El primero corre al abrir la ventana,
  // cuando el motor puede estar apagado: ahí `exigeToken` queda en `null` y no
  // se pide token. Cuando el motor termina de arrancar recién ahí se sabe, y
  // este efecto es lo que convierte ese "no se sabe" en una respuesta.
  useEffect(() => {
    if (estado === "listo") {
      void sondearSalud();
      void contarProblemas();
    }
  }, [estado, sondearSalud, contarProblemas]);

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
              className={`pestana${vista === "actividad" ? " elegida" : ""}`}
              onClick={() => setVista("actividad")}
            >
              Actividad
            </button>
            <button
              className={`pestana${vista === "problemas" ? " elegida" : ""}`}
              onClick={() => {
                setVista("problemas");
                // **Abrir la pestaña es la revisión.** Si estás mirando la
                // lista, la información ya está frente tuyo; un botón extra
                // para decir «sí, miré» sería ceremonia.
                //
                // Se marca y recién después se recuenta, así la burbuja se
                // apaga en el mismo clic. Si el marcado falla, el recuento
                // igual corre: peor que quedarse con la burbuja es quedarse
                // sin la lista.
                void motor
                  .problemasVistosMarcar(new Date().toISOString())
                  .catch(() => undefined)
                  .then(() => contarProblemas());
              }}
            >
              Problemas
              {/* **El contador es la razón de que esto sea una pestaña.** Sin él,
                enterarse de que algo se rompió exige ir a mirar — que es el
                problema que este punto vino a resolver. */}
              {problemas > 0 && (
                <span className="pestana-cuenta">{problemas}</span>
              )}
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
          ) : vista === "actividad" ? (
            <Actividad />
          ) : vista === "problemas" ? (
            <Problemas />
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
