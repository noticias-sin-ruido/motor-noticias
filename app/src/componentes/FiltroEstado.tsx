/**
 * Por qué estado filtrar los clusters.
 *
 * Los valores son los que el motor guarda como texto libre en la columna, así
 * que esta lista es **una conveniencia de la interfaz y no una garantía**: si
 * el motor algún día escribe otro estado, la opción no va a estar acá pero el
 * cluster va a llegar igual con "todos". Por eso el tipo del binding también
 * deja `estado` como `string`.
 */
import type { FiltroDeEstado } from "../datos";

const OPCIONES: { valor: FiltroDeEstado; texto: string }[] = [
  { valor: null, texto: "Todos" },
  { valor: "abierto", texto: "Abiertos" },
  { valor: "procesado", texto: "Procesados" },
  { valor: "descartado", texto: "Descartados" },
];

export default function FiltroEstado({
  valor,
  alElegir,
  deshabilitado,
}: {
  valor: FiltroDeEstado;
  alElegir: (valor: FiltroDeEstado) => void;
  deshabilitado: boolean;
}) {
  return (
    <div className="filtro" role="group" aria-label="Filtrar por estado">
      {OPCIONES.map((opcion) => (
        <button
          key={opcion.texto}
          className={`filtro-opcion${valor === opcion.valor ? " elegida" : ""}`}
          aria-pressed={valor === opcion.valor}
          disabled={deshabilitado}
          onClick={() => alElegir(opcion.valor)}
        >
          {opcion.texto}
        </button>
      ))}
    </div>
  );
}
