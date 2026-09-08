/**
 * Un ángulo entero: el resumen neutro, los puntos clave, cómo lo cubrió cada
 * medio y las notas que lo respaldan.
 *
 * **La comparativa es lo que hace al proyecto.** El resumen neutro cuenta el
 * hecho; la comparativa muestra qué destacó y qué omitió cada medio sobre ese
 * mismo hecho, con una cita textual que lo respalda. Por eso se pinta una fila
 * por medio y no un párrafo corrido: la gracia es poder leerlas enfrentadas.
 */
import { useEffect, useState } from "react";

import * as datos from "../datos";
import type { DetalleSintesis as Detalle } from "../bindings/DetalleSintesis";
import type { ResumenSintesis } from "../bindings/ResumenSintesis";

import Modal from "./Modal";

export default function DetalleSintesis({
  resumen,
  alCerrar,
}: {
  resumen: ResumenSintesis;
  alCerrar: () => void;
}) {
  const [detalle, setDetalle] = useState<Detalle | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let vigente = true;
    datos
      .detalleDeSintesis(resumen.id)
      .then((r) => {
        if (vigente) setDetalle(r.sintesis);
      })
      .catch((e: unknown) => {
        if (vigente) setError(datos.mensajeDeRechazo(e));
      });
    return () => {
      vigente = false;
    };
  }, [resumen.id]);

  // El título del evento sólo llega en el detalle, así que hasta que responda
  // se muestra el del ángulo, que ya se tenía del resumen.
  const bajada = detalle?.titulo_evento ?? undefined;

  return (
    <Modal titulo={resumen.titulo_angulo} bajada={bajada} ancho="ancho" alCerrar={alCerrar}>
      {error !== null ? (
        <div className="aviso">{error}</div>
      ) : detalle === null ? (
        <p className="vacio">Trayendo el ángulo…</p>
      ) : (
        <div className="det">
          <section>
            <h3 className="det-rotulo">Resumen neutro</h3>
            <p className="det-resumen">{detalle.resumen_neutro}</p>
          </section>

          {detalle.puntos_clave.length > 0 && (
            <section>
              <h3 className="det-rotulo">Puntos clave</h3>
              <ul className="det-puntos">
                {detalle.puntos_clave.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h3 className="det-rotulo">Cómo lo cubrió cada medio</h3>
            {/* Es un objeto indexado por nombre de medio, no una lista: lo
                corrigió la captura de fixtures de la fase 3, donde un `Vec`
                habría fallado al deserializar. */}
            {Object.keys(detalle.comparativa_enfoques).length === 0 ? (
              <p className="vacio">Esta síntesis no trajo comparativa.</p>
            ) : (
              <div className="det-comparativa">
                {Object.entries(detalle.comparativa_enfoques).map(([medio, enfoque]) => (
                  <article key={medio} className="det-medio">
                    <h4>{medio}</h4>
                    <dl>
                      <dt>Destacó</dt>
                      <dd>{enfoque.destaco}</dd>
                      <dt>Omitió</dt>
                      <dd>{enfoque.omitio}</dd>
                    </dl>
                    <blockquote>{enfoque.cita}</blockquote>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section>
            <h3 className="det-rotulo">
              Las {detalle.fuentes.length} notas que lo respaldan
            </h3>
            <ul className="det-fuentes">
              {detalle.fuentes.map((f) => (
                <li key={f.id}>
                  {/* `medio` sí es opcional acá: el motor lo emite como
                      `n.medio.nombre if n.medio else None`. */}
                  <span className="det-fuente-medio">{f.medio ?? "medio desconocido"}</span>
                  <a href={f.url} target="_blank" rel="noreferrer noopener">
                    {f.titulo}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}

      <div className="acciones">
        <button onClick={alCerrar}>Cerrar</button>
      </div>
    </Modal>
  );
}
