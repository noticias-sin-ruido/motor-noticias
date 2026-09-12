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

/** De a cuántos se traen, y el techo que impone el motor (`le=100`). */
const PASO = 20;
const TOPE = 100;

export default function ListaDeTrabajo({
  alVerAngulos,
}: {
  alVerAngulos: (cluster: Cluster) => void;
}) {
  const [clusters, setClusters] = useState<Cluster[] | null>(null);
  const [filtro, setFiltro] = useState<FiltroDeEstado>(null);
  // **Se pide de nuevo con un límite más grande, no se pagina con cursor.**
  // `GET /clusters` no tiene cursor —a diferencia de `/sintesis`— y el tope del
  // motor es 100.
  //
  // Medido el 12/09/2026: **782 clusters, de los cuales 49 abiertos**. Con el
  // filtro en «abierto» —que es el caso accionable, lo que falta sintetizar— el
  // tope sobra. **Sin filtro se toca enseguida**, y por eso el pie distingue
  // «no hay más» de «máximo alcanzado»: decir lo primero cuando pasa lo segundo
  // es afirmar que no hay 682 clusters que sí están.
  //
  // El cursor entra cuando el caso accionable deje de entrar: **si los abiertos
  // pasan de ~80**. Hasta entonces es trabajo de motor + contrato + bindings
  // para un techo que la pantalla no toca donde importa.
  const [limite, setLimite] = useState(PASO);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [pipeline, setPipeline] = useState<RespuestaPipeline | null>(null);
  const [errorPipeline, setErrorPipeline] = useState<string | null>(null);

  const [sintetizando, setSintetizando] = useState<Cluster | null>(null);

  // **Dos finales distintos, y confundirlos es mentir.** Que volvieran menos de
  // los pedidos significa que no hay más; llegar al tope significa que la vista
  // no puede traer más, y con 782 clusters en la base sí los hay. La primera
  // versión decía "No hay más" en los dos casos -- lo encontró la prueba manual
  // al llegar a 100 con 782 existiendo.
  const sinMas = clusters !== null && clusters.length < limite;
  const enElTope = limite >= TOPE;
  const agotado = sinMas || enElTope;

  const cargar = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const r = await datos.listarClusters(filtro, limite);
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
  }, [filtro, limite]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  // Cambiar de filtro vuelve al paso inicial: los 43 abiertos y los 595
  // procesados no se recorren igual, y arrastrar un límite grande de una
  // pestaña a otra hace lenta la que no lo necesita.
  useEffect(() => {
    setLimite(PASO);
  }, [filtro]);

  return (
    <section className="pantalla">
      <BarraPipeline pipeline={pipeline} error={errorPipeline} />

      <div className="pantalla-cab">
        <FiltroEstado
          valor={filtro}
          alElegir={setFiltro}
          deshabilitado={cargando}
        />
        <button
          className="secundario chico"
          disabled={cargando}
          onClick={() => void cargar()}
        >
          {cargando ? "Cargando…" : "Actualizar"}
        </button>
      </div>

      {error && <div className="aviso">{error}</div>}

      {clusters === null && !error ? (
        <p className="vacio">Preguntándole al motor…</p>
      ) : clusters !== null && clusters.length === 0 ? (
        <p className="vacio">
          No hay clusters{filtro === null ? "" : ` en estado «${filtro}»`}. Si
          el motor recién arrancó, esperá a que cierre su primer ciclo.
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

      {clusters !== null && clusters.length > 0 && (
        <div className="paginado">
          <span className="ayuda">
            {clusters.length} {clusters.length === 1 ? "cluster" : "clusters"}
            {sinMas
              ? " · no hay más"
              : enElTope
                ? ` · el máximo que muestra esta vista; filtrá por estado para ver otros`
                : ""}
          </span>
          {/* Se deshabilita en vez de desaparecer, igual que en el feed: un
              botón apagado dice "llegaste al final", desaparecer no dice nada.
              Y `agotado` se deduce de que volvieron menos de los pedidos —sin
              cursor no hay otra señal— o de haber llegado al tope del motor. */}
          <button
            className="secundario"
            disabled={cargando || agotado}
            onClick={() => setLimite((l) => Math.min(l + PASO, TOPE))}
          >
            {cargando
              ? "Trayendo…"
              : sinMas
                ? "No hay más"
                : enElTope
                  ? "Máximo alcanzado"
                  : "Cargar más"}
          </button>
        </div>
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
