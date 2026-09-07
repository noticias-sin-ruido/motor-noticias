/**
 * Un hecho agrupado, con lo que hace falta para decidir qué hacer con él.
 *
 * La decisión que la tarjeta tiene que sostener es "¿sintetizo esto?", y para
 * eso lo que importa es: cuántos medios lo cubrieron —abajo de dos el motor no
 * publica— y si ya tiene ángulos hechos.
 *
 * **Es una ficha de alto fijo**, y esa es la restricción que ordena todo lo de
 * abajo. En una grilla, tarjetas que miden lo que su contenido dejan los bordes
 * y los botones a alturas distintas en cada columna, y la lista deja de leerse
 * como una lista. Por eso el título reserva sus tres renglones aunque tenga
 * uno, los datos ocupan un alto constante, y las notas desplegadas **no
 * agrandan la tarjeta**: entran en el mismo hueco, con scroll propio.
 */
import { useState } from "react";

import type { Cluster } from "../bindings/Cluster";

/**
 * El mínimo de medios que el motor exige para sintetizar. Está acá **como
 * aviso, no como regla**: la regla vive en el motor (`MIN_MEDIOS_CLUSTER`) y es
 * la que decide. Duplicarla acá sirve para que la tarjeta lo diga antes de
 * gastar el viaje, pero si las dos se separan, manda el motor — por eso el
 * botón no se deshabilita.
 */
const MEDIOS_MINIMOS = 2;

export default function TarjetaCluster({
  cluster,
  alSintetizar,
  alVerAngulos,
}: {
  cluster: Cluster;
  alSintetizar: (cluster: Cluster) => void;
  alVerAngulos: (cluster: Cluster) => void;
}) {
  const [abierta, setAbierta] = useState(false);
  const pocosMedios = cluster.medios.length < MEDIOS_MINIMOS;

  return (
    <li className="tarjeta-cluster">
      <div className="tc-cab">
        <h3 className="tc-titulo" title={cluster.titulo_evento}>
          {cluster.titulo_evento}
        </h3>
        <span className={`chip chip-${cluster.estado}`}>{cluster.estado}</span>
      </div>

      {/* El hueco de alto fijo. Los datos y las notas se turnan adentro, así
          que abrir las notas nunca cambia la altura de la tarjeta ni empuja a
          las vecinas de su fila. */}
      <div className="tc-cuerpo">
        {abierta ? (
          <ul className="tc-notas">
            {cluster.noticias.map((n) => (
              <li key={n.id}>
                <span className="tc-medio">{n.medio}</span>
                <a href={n.url} target="_blank" rel="noreferrer noopener">
                  {n.titulo}
                </a>
              </li>
            ))}
          </ul>
        ) : (
          <dl className="tc-datos">
            <div>
              <dt>Notas</dt>
              <dd>{cluster.cantidad_noticias}</dd>
            </div>
            <div>
              <dt>Medios</dt>
              {/* El aviso va en el texto y no solo en el color: "pocos" se lee
                  igual sin distinguir tonos. */}
              <dd className={pocosMedios ? "tc-ojo" : undefined}>
                {cluster.medios.length}
                {pocosMedios && " — pocos"}
              </dd>
            </div>
            <div>
              <dt>Ángulos</dt>
              <dd className={cluster.cantidad_sintesis > 0 ? "tc-hecho" : "tc-ojo"}>
                {cluster.cantidad_sintesis === 0 ? "ninguno" : cluster.cantidad_sintesis}
              </dd>
            </div>
            <p className="tc-medios-lista">{cluster.medios.join(", ")}</p>
          </dl>
        )}
      </div>

      <div className="tc-acciones">
        <button className="chico" onClick={() => alSintetizar(cluster)}>
          Sintetizar…
        </button>
        {cluster.cantidad_sintesis > 0 && (
          <button className="secundario chico" onClick={() => alVerAngulos(cluster)}>
            Ángulos
          </button>
        )}
        <button
          className="secundario chico"
          aria-expanded={abierta}
          onClick={() => setAbierta(!abierta)}
        >
          {abierta ? "Ocultar notas" : `${cluster.cantidad_noticias} notas`}
        </button>
      </div>
    </li>
  );
}
