/**
 * Sintetizar un cluster a mano.
 *
 * **Es el único lugar de la app que puede gastar plata**, y está escrito con
 * esa premisa: qué se va a hacer se dice antes de hacerlo, `forzar` cuesta un
 * gesto aparte, y los tres desenlaces del `200` se muestran distinto en vez de
 * colapsar en "listo".
 */
import { useEffect, useRef, useState } from "react";

import * as datos from "../datos";
import type { Cluster } from "../bindings/Cluster";
import type { ModeloPublico } from "../bindings/ModeloPublico";
import type { Motivo } from "../bindings/Motivo";
import type { Sintetizado } from "../bindings/Sintetizado";

/**
 * Qué significa cada motivo por el que el motor **no** llamó al proveedor.
 *
 * `Motivo` es una categoría cerrada generada desde el enum de Rust, y este
 * `switch` **no tiene `default` a propósito**: si el motor agrega una variante
 * y acá no se contempla, `tsc` corta con TS2366 por la salida faltante. Es la
 * única dirección en la que TypeScript avisa, y sirve justamente porque este
 * es el texto que decide si alguien vuelve a apretar un botón que se paga.
 */
function explicarCorte(motivo: Motivo): string {
  switch (motivo) {
    case "sin_material_nuevo":
      return "Ya estaba sintetizado y no entraron notas nuevas desde entonces. No se llamó al proveedor, así que no costó nada. Para rehacerlo igual, marcá «forzar».";
    case "sin_medios_suficientes":
      return "No lo cubrieron suficientes medios distintos. El motor no publica un hecho con una sola voz, así que ni siquiera lo intentó. No costó nada.";
  }
}

/** Lo que puede recibir foco adentro del diálogo. */
const ENFOCABLES =
  'button:not([disabled]), select:not([disabled]), input:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

export default function DialogoSintetizar({
  cluster,
  alCerrar,
  alTerminar,
}: {
  cluster: Cluster;
  alCerrar: () => void;
  alTerminar: () => void;
}) {
  const [modelos, setModelos] = useState<ModeloPublico[] | null>(null);
  const [enUso, setEnUso] = useState<string>("");
  const [elegido, setElegido] = useState<number | null>(null);
  const [forzar, setForzar] = useState(false);
  const [trabajando, setTrabajando] = useState(false);
  const [resultado, setResultado] = useState<Sintetizado | null>(null);
  const [error, setError] = useState<string | null>(null);

  const caja = useRef<HTMLDivElement>(null);
  // `trabajando` y `alCerrar` se leen desde refs y no desde las dependencias:
  // si entraran al `useEffect` de abajo, cada render volvería a montar el
  // manejador y a robar el foco de donde el usuario lo hubiera dejado.
  const trabajandoRef = useRef(false);
  const cerrarRef = useRef(alCerrar);
  trabajandoRef.current = trabajando;
  cerrarRef.current = alCerrar;

  /**
   * Las cuatro cosas que un diálogo modal le debe al teclado, y que no tenía:
   * foco inicial adentro, Escape para salir, el tabulador atrapado en la caja,
   * y devolver el foco a donde estaba al cerrar. Sin esto se podía abrir el
   * diálogo y quedar encerrado sin mouse.
   */
  useEffect(() => {
    const veniaDe = document.activeElement as HTMLElement | null;
    caja.current?.querySelector<HTMLElement>(ENFOCABLES)?.focus();

    function alTeclado(e: KeyboardEvent) {
      if (e.key === "Escape") {
        // Mientras hay un pedido en vuelo no se cierra: el resultado del POST
        // llega igual y perderlo dejaría sin saber si costó plata o no.
        if (!trabajandoRef.current) cerrarRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const lista = Array.from(caja.current?.querySelectorAll<HTMLElement>(ENFOCABLES) ?? []);
      if (lista.length === 0) return;
      const primero = lista[0];
      const ultimo = lista[lista.length - 1];
      if (primero === undefined || ultimo === undefined) return;
      if (e.shiftKey && document.activeElement === primero) {
        e.preventDefault();
        ultimo.focus();
      } else if (!e.shiftKey && document.activeElement === ultimo) {
        e.preventDefault();
        primero.focus();
      }
    }

    document.addEventListener("keydown", alTeclado);
    return () => {
      document.removeEventListener("keydown", alTeclado);
      veniaDe?.focus();
    };
  }, []);

  useEffect(() => {
    let vigente = true;
    datos
      .listarModelos()
      .then((r) => {
        if (!vigente) return;
        setModelos(r.modelos);
        setEnUso(r.en_uso);
      })
      .catch((e: unknown) => {
        if (vigente) setError(datos.mensajeDeRechazo(e));
      });
    return () => {
      vigente = false;
    };
  }, []);

  async function confirmar() {
    setTrabajando(true);
    setError(null);
    try {
      setResultado(await datos.sintetizarCluster(cluster.id, elegido, forzar));
      alTerminar();
    } catch (e) {
      setError(datos.mensajeDeRechazo(e));
    } finally {
      setTrabajando(false);
    }
  }

  const yaTiene = cluster.cantidad_sintesis > 0;

  return (
    <div className="modal-fondo" role="dialog" aria-modal="true" aria-label="Sintetizar cluster">
      <div className="modal" ref={caja}>
        <h2 className="modal-titulo">Sintetizar</h2>
        <p className="modal-hecho">{cluster.titulo_evento}</p>

        {resultado === null ? (
          <>
            <label htmlFor="modelo">Con qué modelo</label>
            <select
              id="modelo"
              value={elegido === null ? "" : String(elegido)}
              disabled={modelos === null || trabajando}
              onChange={(e) => setElegido(e.target.value === "" ? null : Number(e.target.value))}
            >
              <option value="">
                El que el motor elija{enUso ? ` — hoy: ${enUso}` : ""}
              </option>
              {(modelos ?? [])
                .filter((m) => m.activo)
                .map((m) => (
                  <option key={m.id} value={m.id} disabled={!m.credencial_configurada}>
                    {m.nombre} ({m.modelo}){m.credencial_configurada ? "" : " — sin credencial"}
                  </option>
                ))}
            </select>
            <p className="ayuda">
              Sin elegir uno, el motor usa su cadena de fallback: arranca por el
              de mayor prioridad y baja si falla.
            </p>

            {yaTiene && (
              <label className={`casilla${forzar ? " casilla-encendida" : ""}`}>
                <input
                  type="checkbox"
                  checked={forzar}
                  disabled={trabajando}
                  onChange={(e) => setForzar(e.target.checked)}
                />
                <span>
                  <b>Forzar.</b> Este cluster ya tiene{" "}
                  {cluster.cantidad_sintesis === 1
                    ? "un ángulo"
                    : `${cluster.cantidad_sintesis} ángulos`}
                  . Sin esto, el motor corta gratis si no hay material nuevo;
                  con esto <b>llama al proveedor igual, y eso se paga</b>.
                </span>
              </label>
            )}

            {error && <div className="aviso">{error}</div>}

            <div className="acciones">
              <button disabled={trabajando} onClick={() => void confirmar()}>
                {trabajando
                  ? "Sintetizando… (puede tardar)"
                  : forzar
                    ? "Sintetizar de nuevo (se paga)"
                    : "Sintetizar"}
              </button>
              <button className="secundario" disabled={trabajando} onClick={alCerrar}>
                Cancelar
              </button>
            </div>
            {trabajando && (
              <p className="ayuda">
                El motor le está hablando al proveedor. Puede tardar hasta tres
                minutos; no cierres la ventana.
              </p>
            )}
          </>
        ) : resultado.tipo === "hecha" ? (
          <>
            <div className="desenlace desenlace-hecha">
              <span className="estado ok">sintetizado</span>
              <p>
                <b>{resultado.creados}</b> ángulos nuevos,{" "}
                <b>{resultado.actualizados}</b> actualizados y{" "}
                <b>{resultado.descartados}</b> descartados.
              </p>
              <p className="ayuda">Esta corrida sí llamó al proveedor.</p>
            </div>
            <div className="acciones">
              <button onClick={alCerrar}>Cerrar</button>
            </div>
          </>
        ) : (
          <>
            <div className="desenlace desenlace-cortada">
              <span className="estado alerta">no se sintetizó</span>
              <p>{explicarCorte(resultado.motivo)}</p>
              <p className="ayuda">
                Motivo del motor: <code>{resultado.motivo}</code>
              </p>
            </div>
            <div className="acciones">
              {resultado.motivo === "sin_material_nuevo" && !forzar && (
                <button
                  onClick={() => {
                    setForzar(true);
                    setResultado(null);
                  }}
                >
                  Rehacerlo igual (se paga)
                </button>
              )}
              <button className="secundario" onClick={alCerrar}>
                Cerrar
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
