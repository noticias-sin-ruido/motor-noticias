/**
 * Las dos formas de salir, y la pregunta cuando no se eligió ninguna.
 *
 * **Salir dejando el motor corriendo no es un descuido: es un caso de uso.** El
 * pipeline produce cada 15 minutos y no necesita que la ventana esté abierta,
 * así que cerrar la cabina y cerrar el motor son decisiones distintas. La cruz
 * de la ventana no elige por vos — pregunta.
 *
 * El mismo componente atiende los tres pedidos: el de la cruz, que abre la
 * pregunta, y los dos de la bandeja, que ya vienen decididos y sólo necesitan
 * mostrar en qué anda mientras se ejecutan.
 */
import { useEffect, useRef, useState } from "react";

import * as motor from "../motor";
import type { PedidoDeSalida } from "../motor";

import Modal from "./Modal";

export default function DialogoSalida({
  pedido,
  alCancelar,
}: {
  pedido: PedidoDeSalida;
  alCancelar: () => void;
}) {
  const [deteniendo, setDeteniendo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Los pedidos que ya vienen decididos se ejecutan al montar, y una sola vez:
  // sin la guarda, el doble efecto de StrictMode dispararía dos veces el
  // `docker compose stop`.
  const yaArranco = useRef(false);

  async function detenerYSalir() {
    setDeteniendo(true);
    setError(null);
    try {
      await motor.detener();
      await motor.salir();
    } catch (e) {
      // **No se sale igual.** Si el motor no se pudo parar, cerrar la ventana
      // dejaría los contenedores corriendo justo cuando se pidió lo contrario,
      // y sin nadie mirando. Se muestra qué pasó y se deja elegir.
      setError(motor.mensajeDeRechazo(e));
      setDeteniendo(false);
    }
  }

  useEffect(() => {
    if (yaArranco.current) return;
    yaArranco.current = true;
    if (pedido === "detener_y_salir") void detenerYSalir();
    if (pedido === "salir_sin_detener") void motor.salir();
    // `detenerYSalir` se define en cada render y no hace falta en las
    // dependencias: la guarda de arriba ya garantiza una sola ejecución.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pedido]);

  if (pedido !== "preguntar" && error === null) {
    return (
      <Modal titulo="Cerrando" alCerrar={alCancelar} bloqueado>
        <p className="ayuda sin-tope">
          {pedido === "detener_y_salir"
            ? "Parando los contenedores. Puede tardar unos segundos; la ventana se cierra sola cuando termine."
            : "Cerrando la cabina. El motor queda corriendo."}
        </p>
      </Modal>
    );
  }

  return (
    <Modal
      titulo="Cerrar la cabina"
      bajada="El motor puede seguir produciendo sin la ventana abierta."
      bloqueado={deteniendo}
      alCerrar={alCancelar}
    >
      {error !== null && (
        <div className="aviso">
          No se pudo detener el motor, así que la cabina no se cerró: {error}
        </div>
      )}

      <div className="salidas">
        <button disabled={deteniendo} onClick={() => void detenerYSalir()}>
          {deteniendo ? "Deteniendo el motor…" : "Detener motor y salir"}
        </button>
        <p className="ayuda sin-tope">
          Para los contenedores. El pipeline deja de producir hasta que vuelvas
          a arrancarlo.
        </p>

        <button className="secundario" disabled={deteniendo} onClick={() => void motor.salir()}>
          Salir dejando el motor corriendo
        </button>
        <p className="ayuda sin-tope">
          Cierra sólo la ventana. El ciclo sigue cada 15 minutos y las síntesis
          se siguen entregando al back-end.
        </p>
      </div>

      <div className="acciones">
        <button className="secundario" disabled={deteniendo} onClick={alCancelar}>
          Cancelar
        </button>
      </div>
    </Modal>
  );
}
