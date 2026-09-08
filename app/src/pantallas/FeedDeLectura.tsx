/**
 * Pantalla 2: leer lo que el motor produjo.
 *
 * Ritmo distinto al de la lista de trabajo, y por eso son dos pantallas y no
 * una: allá se mira, se decide y se dispara; acá se lee de corrido.
 *
 * **Paginación por cursor y no por offset.** El scheduler inserta síntesis
 * nuevas arriba cada 15 minutos, así que con offset la página 2 repetiría lo
 * que ya se vio. El cursor es opaco: sale del campo `siguiente` de la respuesta
 * anterior y se manda tal cual, sin interpretarlo de este lado.
 */
import { useCallback, useEffect, useState } from "react";

import * as datos from "../datos";
import type { ResumenSintesis } from "../bindings/ResumenSintesis";

import DetalleSintesis from "../componentes/DetalleSintesis";
import TarjetaAngulo from "../componentes/TarjetaAngulo";

const POR_PAGINA = 20;

export default function FeedDeLectura({
  clusterId,
  alQuitarFiltro,
}: {
  /** Si viene, el feed arranca filtrado por ese cluster. */
  clusterId: number | null;
  alQuitarFiltro: () => void;
}) {
  const [angulos, setAngulos] = useState<ResumenSintesis[]>([]);
  const [siguiente, setSiguiente] = useState<string | null>(null);
  const [primeraHecha, setPrimeraHecha] = useState(false);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [abierto, setAbierto] = useState<ResumenSintesis | null>(null);

  /**
   * Trae una página. Con `cursor` en `null` empieza de cero y reemplaza la
   * lista; con cursor, agrega al final.
   */
  const traer = useCallback(
    async (cursor: string | null) => {
      setCargando(true);
      setError(null);
      try {
        const r = await datos.listarSintesis({
          limite: POR_PAGINA,
          cursor,
          clusterId,
        });
        setAngulos((previas) => (cursor === null ? r.sintesis : [...previas, ...r.sintesis]));
        setSiguiente(r.siguiente);
        setPrimeraHecha(true);
      } catch (e) {
        const err = e as { tipo?: string };
        // **El 422 no rompe la lista: la resetea.** Un cursor inválido es una
        // entrada nuestra que quedó vieja —el motor lo trata como pedido
        // inválido, no como error suyo— y lo único sensato es empezar de cero.
        // Dejar el cursor roto en el estado convertiría un tropiezo en una
        // pantalla que ya no carga más, y quien mira no tendría cómo saberlo.
        if (err.tipo === "invalida" && cursor !== null) {
          setAviso("El cursor de paginado quedó viejo, así que la lista se recargó desde el principio.");
          setSiguiente(null);
          void traer(null);
          return;
        }
        setError(datos.mensajeDeRechazo(e));
      } finally {
        setCargando(false);
      }
    },
    [clusterId],
  );

  // Cambiar de filtro empieza de cero: el cursor anterior pertenece a otra
  // consulta y mezclarlos daría una lista que no existe.
  useEffect(() => {
    setAngulos([]);
    setSiguiente(null);
    setPrimeraHecha(false);
    setAviso(null);
    void traer(null);
  }, [traer]);

  const agotado = primeraHecha && siguiente === null;

  return (
    <section className="pantalla">
      <div className="pantalla-cab">
        <h2 className="pantalla-titulo">Feed de lectura</h2>
        {clusterId !== null && (
          <span className="filtro-activo">
            Sólo el cluster <b>{clusterId}</b>
            <button className="secundario chico" onClick={alQuitarFiltro}>
              Ver todos
            </button>
          </span>
        )}
        <button
          className="secundario chico"
          disabled={cargando}
          onClick={() => void traer(null)}
        >
          {cargando && angulos.length === 0 ? "Cargando…" : "Actualizar"}
        </button>
      </div>

      {aviso !== null && <div className="aviso aviso-suave">{aviso}</div>}
      {error !== null && <div className="aviso">{error}</div>}

      {angulos.length === 0 && !cargando && error === null ? (
        <p className="vacio">
          {clusterId === null
            ? "El motor todavía no produjo ninguna síntesis."
            : `El cluster ${clusterId} no tiene ángulos.`}
        </p>
      ) : (
        <ul className="lista-angulos">
          {angulos.map((s) => (
            <TarjetaAngulo key={s.id} sintesis={s} alAbrir={setAbierto} />
          ))}
        </ul>
      )}

      {angulos.length > 0 && (
        <div className="feed-pie">
          <span className="feed-cuenta">
            {angulos.length} {angulos.length === 1 ? "ángulo" : "ángulos"}
            {agotado ? " · no hay más" : ""}
          </span>
          {/* `siguiente: null` es el final de la lista, no un error. El botón se
              deshabilita en vez de desaparecer: que exista y esté apagado dice
              "llegaste al final", desaparecer no dice nada. */}
          <button
            className="secundario"
            disabled={cargando || agotado}
            onClick={() => void traer(siguiente)}
          >
            {cargando ? "Trayendo…" : agotado ? "No hay más" : "Cargar más"}
          </button>
        </div>
      )}

      {abierto !== null && (
        <DetalleSintesis resumen={abierto} alCerrar={() => setAbierto(null)} />
      )}
    </section>
  );
}
