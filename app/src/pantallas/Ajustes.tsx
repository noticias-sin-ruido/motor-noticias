/**
 * Ajustes: lo que se toca una vez y casi nunca más.
 *
 * **Funciona con el motor apagado, y eso es el punto.** Las otras dos pantallas
 * no tienen nada que mostrar sin motor; ésta es justamente donde se arregla que
 * el motor no arranque —la carpeta que se movió, el token que cambió—, así que
 * si dependiera de que el motor esté vivo no serviría para nada cuando hace
 * falta. Lo único que se degrada es el panel de salud, que dice que no contesta
 * en vez de desaparecer.
 */
import { useCallback, useEffect, useState } from "react";

import * as datos from "../datos";
import * as motor from "../motor";
import Modal from "../componentes/Modal";
import type { Entrega } from "../bindings/Entrega";
import type { Salud } from "../bindings/Salud";

function SaludDelMotor() {
  const [salud, setSalud] = useState<Salud | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);

  const traer = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      setSalud(await datos.salud());
    } catch (e) {
      setSalud(null);
      setError(datos.mensajeDeRechazo(e));
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void traer();
  }, [traer]);

  return (
    <section className="tarjeta">
      <h2>El motor</h2>

      {error && <div className="aviso">{error}</div>}

      {salud && (
        <dl className="fila">
          <dt>Base de datos</dt>
          <dd>{salud.database === "ok" ? "Responde" : "No responde"}</dd>

          <dt>Entorno</dt>
          <dd>{salud.environment}</dd>

          {/* Sirve para detectar un contenedor con el reloj corrido, que es la
              clase de problema que se ve en los datos mucho después. */}
          <dt>Hora del motor</dt>
          <dd>{salud.hora_local}</dd>

          <dt>Pide token</dt>
          <dd>{salud.exige_token ? "Sí" : "No, la API está abierta"}</dd>

          {/* La URL NO se muestra acá: `GET /` sólo manda el booleano. Editarla
              es el bloque siguiente. */}
          <dt>Entrega al back-end</dt>
          <dd>
            {salud.entrega_configurada
              ? "Configurada"
              : "Sin configurar — las síntesis quedan guardadas"}
          </dd>
        </dl>
      )}

      <div className="acciones">
        <button className="chico" onClick={() => void traer()} disabled={cargando}>
          {cargando ? "Consultando…" : "Volver a consultar"}
        </button>
      </div>
    </section>
  );
}

function CarpetaDelMotor() {
  const [guardada, setGuardada] = useState<string | null>(null);
  const [editando, setEditando] = useState(false);
  const [ruta, setRuta] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [listo, setListo] = useState(false);

  const traer = useCallback(async () => {
    setGuardada(await motor.repoLeer());
  }, []);

  useEffect(() => {
    void traer();
  }, [traer]);

  async function guardar(evento: React.FormEvent) {
    evento.preventDefault();
    setError(null);
    setListo(false);
    try {
      await motor.repoGuardar(ruta.trim());
      await traer();
      setEditando(false);
      setListo(true);
    } catch (e) {
      setError(motor.mensajeDeRechazo(e));
    }
  }

  return (
    <section className="tarjeta">
      <h2>La carpeta del motor</h2>

      {!editando ? (
        <>
          <dl className="fila">
            <dt>Carpeta</dt>
            <dd>
              <code>{guardada ?? "sin configurar"}</code>
            </dd>
          </dl>
          <p className="ayuda">
            Es la carpeta que contiene el <code>docker-compose.yml</code>. Se
            comprueba <b>cada vez que se arranca o se para el motor</b>, no sólo
            al guardarla: si movés la carpeta a otro disco, la app te lo dice
            acá en vez de fallar con un error de Docker.
          </p>
          {listo && <div className="aviso">Carpeta actualizada.</div>}
          <div className="acciones">
            <button
              className="chico"
              onClick={() => {
                setRuta(guardada ?? "");
                setListo(false);
                setEditando(true);
              }}
            >
              Cambiar carpeta
            </button>
          </div>
        </>
      ) : (
        <form onSubmit={guardar}>
          <label htmlFor="ajustes-repo">Carpeta del repo del motor</label>
          <input
            id="ajustes-repo"
            type="text"
            value={ruta}
            onChange={(e) => setRuta(e.target.value)}
            placeholder="La carpeta que contiene docker-compose.yml"
            autoFocus
          />
          {error && <div className="aviso">{error}</div>}
          <div className="acciones">
            <button className="chico" type="submit" disabled={!ruta.trim()}>
              Guardar
            </button>
            <button
              className="chico"
              type="button"
              onClick={() => {
                setEditando(false);
                setError(null);
              }}
            >
              Cancelar
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function DestinoDeEntrega({ alCambiar }: { alCambiar: () => void }) {
  const [entrega, setEntrega] = useState<Entrega | null>(null);
  const [editando, setEditando] = useState(false);
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  /** La URL esperando confirmación. `null` es "no hay nada que confirmar". */
  const [porConfirmar, setPorConfirmar] = useState<string | null>(null);

  const traer = useCallback(async () => {
    setError(null);
    try {
      setEntrega((await datos.entregaVer()).entrega);
    } catch (e) {
      setEntrega(null);
      setError(datos.mensajeDeRechazo(e));
    }
  }, []);

  useEffect(() => {
    void traer();
  }, [traer]);

  async function guardar(nuevo: string | null) {
    setGuardando(true);
    setError(null);
    try {
      setEntrega((await datos.entregaCambiar(nuevo)).entrega);
      setEditando(false);
      // La salud de `GET /` trae `entrega_configurada`, y el feed la usa para
      // decidir si el chip "sin entregar" significa algo. Si no se refresca,
      // la otra pantalla sigue mostrando lo de antes.
      alCambiar();
    } catch (e) {
      setError(datos.mensajeDeRechazo(e));
    } finally {
      setGuardando(false);
    }
  }

  return (
    <section className="tarjeta">
      <h2>A dónde se entregan las síntesis</h2>

      {error && <div className="aviso">{error}</div>}

      {entrega && !editando && (
        <>
          <dl className="fila">
            <dt>Destino</dt>
            <dd>
              <code>{entrega.url ?? "sin configurar"}</code>
            </dd>
            {entrega.configurado && !entrega.valido && (
              <>
                <dt>Problema</dt>
                <dd>{entrega.problema}</dd>
              </>
            )}
            <dt>Secreto de firma</dt>
            <dd>
              {entrega.secreto_configurado
                ? "Configurado en el motor"
                : "Falta — sin él la entrega no corre"}
            </dd>
            {entrega.actualizado_en && (
              <>
                <dt>Último cambio</dt>
                <dd>{entrega.actualizado_en}</dd>
              </>
            )}
          </dl>

          <p className="ayuda">
            Sin destino, el motor <b>no deja de trabajar</b>: las síntesis se
            acumulan guardadas y salen cuando se configure uno. Cambiar la URL{" "}
            <b>no reenvía lo viejo</b> — el destino nuevo recibe desde la próxima
            síntesis, porque mandar cientos de golpe a un back-end que quizá
            recién se levanta tiene que ser una acción con su propio nombre.
          </p>

          <div className="acciones">
            <button
              className="chico"
              onClick={() => {
                setUrl(entrega.url ?? "");
                setEditando(true);
              }}
            >
              Cambiar destino
            </button>
            {entrega.configurado && (
              <button
                className="chico"
                onClick={() => void guardar(null)}
                disabled={guardando}
              >
                Dejar de entregar
              </button>
            )}
          </div>
        </>
      )}

      {/* **El aviso va ANTES de guardar, y esa es la decisión.**
          Después de guardar no serviría: la entrega es un proceso de fondo que
          corre cada quince minutos, así que un destino mal apareado no se nota
          hasta que alguien lee un log o revisa por qué el back-end está vacío.
          Acá la responsabilidad queda del lado de quien la puede resolver, con
          el motivo escrito y no como un "¿estás seguro?". */}
      {porConfirmar !== null && entrega && (
        <Modal
          titulo="El secreto de firma tiene que coincidir"
          alCerrar={() => setPorConfirmar(null)}
        >
          <p className="sin-tope">
            El motor <b>firma cada síntesis</b> con <code>WEBHOOK_SECRET</code>,
            y el back-end la valida con ese mismo valor. Es lo que prueba que lo
            que llega salió de acá y no de otro lado.
          </p>

          {entrega.secreto_configurado ? (
            <p className="ayuda">
              Hay un secreto configurado en el motor, pero <b>la app no puede
              saber si es el que valida este destino</b>. Si no coinciden, cada
              síntesis vuelve rechazada — y un rechazo <b>no se reintenta</b>:
              cuenta el intento igual, y a los cinco barridos la síntesis se
              abandona y ya no sale sola. Es el mismo contador que dejó 144
              síntesis abandonadas el 8 de septiembre, ahí por un destino
              inalcanzable.
            </p>
          ) : (
            <p className="aviso">
              <b>Falta el secreto en el motor.</b> Con un destino configurado y
              sin secreto, la entrega no corre y sólo lo dice el log: el back-end
              se queda vacío sin que nada avise en pantalla.
            </p>
          )}

          <p className="ayuda">
            No se configura desde acá: va en el <code>.env</code> del motor y hay
            que <b>reiniciar el contenedor</b>. Es a propósito — la base se
            respalda y se lee desde la API, y una credencial ahí adentro se
            filtra sola.
          </p>

          <div className="acciones">
            <button
              className="chico"
              onClick={() => {
                const destino = porConfirmar;
                setPorConfirmar(null);
                void guardar(destino);
              }}
              disabled={guardando}
            >
              Entiendo, guardar el destino
            </button>
            <button className="chico" onClick={() => setPorConfirmar(null)}>
              Cancelar
            </button>
          </div>
        </Modal>
      )}

      {editando && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setPorConfirmar(url.trim());
          }}
        >
          <label htmlFor="entrega-url">URL del back-end</label>
          <input
            id="entrega-url"
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://tu-backend/webhooks/sintesis"
            autoFocus
          />
          <p className="ayuda">
            Puede ser un back-end de tu red o de la misma máquina. Lo que el
            motor rechaza son las direcciones link-local, que es donde viven los
            metadatos de las nubes.
          </p>
          <div className="acciones">
            <button className="chico" type="submit" disabled={!url.trim() || guardando}>
              {guardando ? "Guardando…" : "Guardar"}
            </button>
            <button
              className="chico"
              type="button"
              onClick={() => {
                setEditando(false);
                setError(null);
              }}
            >
              Cancelar
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function TokenDelOperador({
  hayToken,
  exigeToken,
  alCambiar,
}: {
  hayToken: boolean;
  /** `null` mientras el motor no haya contestado. */
  exigeToken: boolean | null;
  alCambiar: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [borrando, setBorrando] = useState(false);

  async function olvidar() {
    setBorrando(true);
    setError(null);
    try {
      // **Esto es lo que antes no pasaba.** "Olvidar token" sólo ponía en
      // `false` un estado de React: la app volvía a pedirlo y la credencial
      // seguía guardada en el Administrador de credenciales de Windows.
      await motor.tokenBorrar();
      alCambiar();
    } catch (e) {
      setError(motor.mensajeDeRechazo(e));
    } finally {
      setBorrando(false);
    }
  }

  return (
    <section className="tarjeta">
      <h2>El token del operador</h2>

      <dl className="fila">
        <dt>Guardado en esta máquina</dt>
        <dd>{hayToken ? "Sí" : "No"}</dd>
        <dt>El motor lo exige</dt>
        <dd>
          {exigeToken === null
            ? "No se sabe: el motor no contestó"
            : exigeToken
              ? "Sí"
              : "No, la API está abierta"}
        </dd>
      </dl>

      {hayToken && exigeToken === false && (
        <p className="ayuda">
          Hay un token guardado y este motor no lo pide. No molesta: si algún día
          la API se cierra, ya está puesto.
        </p>
      )}

      <p className="ayuda">
        Vive en el <b>Administrador de credenciales de Windows</b>, no en un
        archivo de la app, y nunca vuelve a la ventana: los pedidos al motor los
        firma la parte de Rust.
      </p>

      {error && <div className="aviso">{error}</div>}

      {hayToken && (
        <div className="acciones">
          <button className="chico" onClick={() => void olvidar()} disabled={borrando}>
            {borrando ? "Borrando…" : "Olvidar token"}
          </button>
        </div>
      )}
    </section>
  );
}

export default function Ajustes({
  hayToken,
  exigeToken,
  motorListo,
  alCambiarToken,
  alCambiarEntrega,
}: {
  hayToken: boolean;
  exigeToken: boolean | null;
  /** Con el motor abajo no se puede consultar el destino: exige token y API viva. */
  motorListo: boolean;
  alCambiarToken: () => void;
  alCambiarEntrega: () => void;
}) {
  return (
    <div className="ajustes">
      <SaludDelMotor />
      <CarpetaDelMotor />
      {/* **Sólo con el motor listo**, a diferencia del resto de la pantalla.
          `GET /entrega` exige token siempre y necesita la API viva: pedirlo con
          el motor apagado no da un error informativo, da ruido encima del
          problema que la persona vino a resolver acá. */}
      {motorListo && <DestinoDeEntrega alCambiar={alCambiarEntrega} />}
      <TokenDelOperador
        hayToken={hayToken}
        exigeToken={exigeToken}
        alCambiar={alCambiarToken}
      />
    </div>
  );
}
