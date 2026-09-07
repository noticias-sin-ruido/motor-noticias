/**
 * Pantalla 1: mirar, decidir y disparar.
 *
 * Es el modo paso a paso que el motor no tenía cómo ofrecer: hasta la fase 3
 * el ciclo automático era lo único que sintetizaba, y elegir un hecho puntual
 * significaba pegarle a la API a mano.
 */
import { useCallback, useEffect, useState } from "react";

import * as datos from "../datos";
import type { FiltroDeEstado } from "../datos";
import type { Cluster } from "../bindings/Cluster";
import type { RespuestaPipeline } from "../bindings/RespuestaPipeline";

import BarraPipeline from "../componentes/BarraPipeline";
import DialogoSintetizar from "../componentes/DialogoSintetizar";
import FiltroEstado from "../componentes/FiltroEstado";
import TarjetaCluster from "../componentes/TarjetaCluster";

export default function ListaDeTrabajo({
  alVerAngulos,
}: {
  alVerAngulos: (cluster: Cluster) => void;
}) {
  const [clusters, setClusters] = useState<Cluster[] | null>(null);
  const [filtro, setFiltro] = useState<FiltroDeEstado>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [pipeline, setPipeline] = useState<RespuestaPipeline | null>(null);
  const [errorPipeline, setErrorPipeline] = useState<string | null>(null);

  const [sintetizando, setSintetizando] = useState<Cluster | null>(null);

  const cargar = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const r = await datos.listarClusters(filtro, 20);
      setClusters(r.clusters);
    } catch (e) {
      setError(datos.mensajeDeRechazo(e));
    } finally {
      setCargando(false);
    }
    // El pipeline va en su propio try: que el ciclo no conteste no es motivo
    // para dejar la lista en blanco, son dos preguntas independientes.
    try {
      setPipeline(await datos.estadoDelPipeline(5));
      setErrorPipeline(null);
    } catch (e) {
      setErrorPipeline(datos.mensajeDeRechazo(e));
    }
  }, [filtro]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  return (
    <section className="pantalla">
      <BarraPipeline pipeline={pipeline} error={errorPipeline} />

      <div className="pantalla-cab">
        <FiltroEstado valor={filtro} alElegir={setFiltro} deshabilitado={cargando} />
        <button className="secundario chico" disabled={cargando} onClick={() => void cargar()}>
          {cargando ? "Cargando…" : "Actualizar"}
        </button>
      </div>

      {error && <div className="aviso">{error}</div>}

      {clusters === null && !error ? (
        <p className="vacio">Preguntándole al motor…</p>
      ) : clusters !== null && clusters.length === 0 ? (
        <p className="vacio">
          No hay clusters{filtro === null ? "" : ` en estado «${filtro}»`}. Si el
          motor recién arrancó, esperá a que cierre su primer ciclo.
        </p>
      ) : (
        <ul className="lista-clusters">
          {(clusters ?? []).map((c) => (
            <TarjetaCluster
              key={c.id}
              cluster={c}
              alSintetizar={setSintetizando}
              alVerAngulos={alVerAngulos}
            />
          ))}
        </ul>
      )}

      {sintetizando && (
        <DialogoSintetizar
          cluster={sintetizando}
          alCerrar={() => setSintetizando(null)}
          // Al terminar se recarga: `cantidad_sintesis` acaba de cambiar, y la
          // tarjeta seguiría diciendo el número viejo.
          alTerminar={() => void cargar()}
        />
      )}
    </section>
  );
}
