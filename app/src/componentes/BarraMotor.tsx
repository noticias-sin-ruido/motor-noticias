/**
 * El estado del motor, siempre a la vista.
 *
 * Es una barra fija y no una pantalla aparte **porque de esto depende todo lo
 * demás**: ninguna pantalla puede pedir datos si el motor no está `listo`, así
 * que tener que irse a otra vista para saber si sigue vivo es justo la
 * pregunta que uno se hace sin querer moverse. Reemplaza a la tarjeta grande
 * de la fase 1, que era la pantalla entera cuando no había ninguna otra.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import * as motor from "../motor";
import type { Estado } from "../motor";

export default function BarraMotor({
  estado,
  alCambiar,
}: {
  estado: Estado;
  alCambiar: (estado: Estado) => void;
}) {
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const desuscribir = useRef<(() => void) | null>(null);

  // Rust avisa **cada transición** del arranque, no sólo el final: entre
  // `reconstruyendo` y `listo` pueden pasar minutos, y sin estos avisos la
  // barra no tendría nada que mostrar mientras tanto.
  useEffect(() => {
    void motor.alCambiarEstado(alCambiar).then((off) => {
      desuscribir.current = off;
    });
    return () => desuscribir.current?.();
  }, [alCambiar]);

  const sondear = useCallback(async () => {
    try {
      alCambiar(await motor.estadoActual());
      setError(null);
    } catch (e) {
      setError(motor.mensajeDeRechazo(e));
    }
  }, [alCambiar]);

  useEffect(() => {
    void sondear();
  }, [sondear]);

  async function accion(fn: () => Promise<Estado>) {
    setOcupado(true);
    setError(null);
    try {
      alCambiar(await fn());
    } catch (e) {
      setError(motor.mensajeDeRechazo(e));
      alCambiar("error");
    } finally {
      setOcupado(false);
    }
  }

  const { texto, clase } = motor.describir(estado);

  return (
    <header className="barra-motor">
      <div className="barra-motor-fila">
        {/* El logo no es un `<img>` sino una máscara pintada por CSS, así toma
            el color del tema en vez de traer el suyo. El archivo es un trazo
            monocromo, o sea que no se pierde nada en el camino.

            Lleva el nombre en texto porque una máscara CSS **no tiene
            alternativa textual**: sin esto, quien usa lector de pantalla no
            oiría el nombre del programa en ningún lado. */}
        <span className="barra-motor-logo" role="img" aria-label="Sin Ruido" />
        <span className={`estado ${clase}`}>{texto}</span>
        <div className="barra-motor-acciones">
          {estado === "listo" ? (
            <button
              className="secundario chico"
              disabled={ocupado}
              onClick={() => void accion(motor.detener)}
            >
              Detener motor
            </button>
          ) : (
            <button
              className="chico"
              disabled={ocupado}
              onClick={() => void accion(motor.arrancar)}
            >
              {ocupado ? "Trabajando…" : "Arrancar motor"}
            </button>
          )}
          {/* El glifo va `aria-hidden` y el nombre accesible lo pone el
              `aria-label`: un botón de solo ícono sin nombre es, para quien usa
              lector de pantalla, un botón sin etiqueta. El `title` solo, que
              era lo que había, no alcanza — no todos los lectores lo anuncian. */}
          <button
            className="secundario chico"
            disabled={ocupado}
            onClick={() => void sondear()}
            aria-label="Volver a consultar el estado del motor"
            title="Volver a consultar el estado del motor"
          >
            <span aria-hidden="true">↻</span>
          </button>
        </div>
      </div>
      {error && <div className="aviso">{error}</div>}
    </header>
  );
}
