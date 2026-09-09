/**
 * Los modelos de IA con los que el motor sintetiza (backlog punto 5).
 *
 * **Hay como mucho uno activo, y lo hace cumplir el motor**: prender uno apaga a
 * los demás. La app no manda apagar nada — si lo hiciera, tendría que replicar
 * esa regla y las dos copias se separarían el día que cambie.
 *
 * **Sin ninguno activo la síntesis no corre.** Apagar el último es válido y es
 * la marcha atrás si un modelo nuevo resulta peor, pero deja el motor sin
 * producir, así que se dice con todas las letras en vez de mostrarlo como un
 * estado más.
 */
import { useCallback, useEffect, useState } from "react";

import * as datos from "../datos";
import type { ModeloPublico } from "../bindings/ModeloPublico";

/**
 * Los adaptadores que el motor sabe hablar hoy.
 *
 * **`anthropic` no está**, y no es un olvido: el enum del motor lo reserva pero
 * lo rechaza al construirlo, porque nunca se pudo probar. Anthropic se usa igual
 * con `openai_compatible` y su `base_url`, que es lo que dice hacer el propio
 * comentario del motor. Ofrecerlo acá sería ofrecer un error garantizado.
 */
const ADAPTADORES = [
  { valor: "openai_compatible", texto: "Compatible con OpenAI (OpenAI, Groq, OpenRouter, Ollama…)" },
  { valor: "gemini", texto: "Gemini" },
];

function AvisoDeCredencial() {
  return (
    <p className="ayuda">
      La credencial del proveedor <b>no se configura desde acá</b>: vive en el{" "}
      <code>.env</code> del motor y hay que <b>reiniciar el contenedor</b> para
      que la tome. Es a propósito — la base se respalda y se lee desde la API, y
      una credencial ahí adentro se filtra sola.
      <br />
      Por el mismo motivo esta pantalla puede decirte <i>que falta</i> pero no{" "}
      <i>cuál variable</i> es: el motor dejó de publicar ese nombre después de
      que un mensaje de error lo filtrara.
    </p>
  );
}

function Alta({ alAlta }: { alAlta: () => void }) {
  const [nombre, setNombre] = useState("");
  const [adaptador, setAdaptador] = useState(ADAPTADORES[0].valor);
  const [modelo, setModelo] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [activar, setActivar] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function guardar(evento: React.FormEvent) {
    evento.preventDefault();
    setEnviando(true);
    setError(null);
    try {
      await datos.altaModelo({
        nombre: nombre.trim(),
        adaptador,
        modelo: modelo.trim(),
        baseUrl: baseUrl.trim() || null,
        activar,
      });
      alAlta();
    } catch (e) {
      setError(datos.mensajeDeRechazo(e));
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form className="tarjeta" onSubmit={guardar}>
      <h2>Agregar un modelo</h2>

      <label htmlFor="m-nombre">Nombre</label>
      <input
        id="m-nombre"
        value={nombre}
        onChange={(e) => setNombre(e.target.value)}
        placeholder="Cómo lo vas a reconocer entre varios"
      />

      <label htmlFor="m-adaptador">Protocolo</label>
      <select
        id="m-adaptador"
        value={adaptador}
        onChange={(e) => setAdaptador(e.target.value)}
      >
        {ADAPTADORES.map((a) => (
          <option key={a.valor} value={a.valor}>
            {a.texto}
          </option>
        ))}
      </select>

      <label htmlFor="m-modelo">Identificador del modelo</label>
      <input
        id="m-modelo"
        value={modelo}
        onChange={(e) => setModelo(e.target.value)}
        placeholder="gpt-4o, llama-3.1-70b, gemini-3.5-flash…"
      />

      {adaptador === "openai_compatible" && (
        <>
          <label htmlFor="m-base">URL del proveedor</label>
          <input
            id="m-base"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.groq.com/openai/v1"
          />
        </>
      )}

      <label className="casilla">
        <input
          type="checkbox"
          checked={activar}
          onChange={(e) => setActivar(e.target.checked)}
        />
        Empezar a sintetizar con éste (apaga el que esté activo)
      </label>

      <p className="ayuda">
        Antes de guardarlo, el motor <b>le manda un pedido de prueba</b> y
        verifica que devuelva la estructura que necesita. Por eso tarda, y por
        eso un modelo que no sirve se rechaza <i>ahora</i> en vez de romper la
        síntesis cada quince minutos.
      </p>

      {error && <div className="aviso">{error}</div>}

      <div className="acciones">
        <button
          type="submit"
          className="chico"
          disabled={!nombre.trim() || !modelo.trim() || enviando}
        >
          {enviando ? "Probando contra el proveedor…" : "Agregar"}
        </button>
      </div>
    </form>
  );
}

export default function Modelos() {
  const [modelos, setModelos] = useState<ModeloPublico[]>([]);
  const [enUso, setEnUso] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);
  const [tocando, setTocando] = useState<number | null>(null);

  const traer = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const r = await datos.listarModelos();
      setModelos(r.modelos);
      setEnUso(r.en_uso);
    } catch (e) {
      setError(datos.mensajeDeRechazo(e));
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void traer();
  }, [traer]);

  async function cambiar(modelo: ModeloPublico) {
    setTocando(modelo.id);
    setError(null);
    try {
      const r = await datos.activarModelo(modelo.id, !modelo.activo);
      setEnUso(r.en_uso);
      // Se relee la lista entera y no se toca el estado local: prender uno
      // apaga a los demás **del lado del motor**, así que actualizar sólo la
      // fila tocada dejaría la pantalla mostrando dos activos.
      await traer();
    } catch (e) {
      setError(datos.mensajeDeRechazo(e));
    } finally {
      setTocando(null);
    }
  }

  return (
    <div className="ajustes">
      <section className="tarjeta">
        <h2>Con qué sintetiza el motor</h2>

        <dl className="fila">
          <dt>En uso</dt>
          <dd>{enUso || "—"}</dd>
        </dl>

        {error && <div className="aviso">{error}</div>}

        {modelos.length === 0 && !cargando && (
          <p className="ayuda">Todavía no hay ningún modelo configurado.</p>
        )}

        <ul className="lista-modelos">
          {modelos.map((m) => (
            <li key={m.id} className={m.activo ? "modelo activo" : "modelo"}>
              <div>
                <b>{m.nombre}</b>
                <span className="modelo-detalle">
                  {m.modelo} · {m.adaptador} · prioridad {m.prioridad}
                </span>
                {!m.credencial_configurada && (
                  <span className="chip chip-pendiente">falta la credencial</span>
                )}
              </div>
              <button
                className="chico"
                onClick={() => void cambiar(m)}
                disabled={tocando !== null}
              >
                {tocando === m.id
                  ? m.activo
                    ? "Apagando…"
                    : "Probando contra el proveedor…"
                  : m.activo
                    ? "Apagar"
                    : "Usar éste"}
              </button>
            </li>
          ))}
        </ul>

        <AvisoDeCredencial />

        <div className="acciones">
          <button className="chico" onClick={() => void traer()} disabled={cargando}>
            {cargando ? "Cargando…" : "Refrescar"}
          </button>
        </div>
      </section>

      <Alta alAlta={() => void traer()} />
    </div>
  );
}
