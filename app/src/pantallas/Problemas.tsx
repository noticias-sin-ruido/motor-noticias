import { useCallback, useEffect, useState } from "react";

import * as datos from "../datos";
import type { EventoRegistrado } from "../bindings/EventoRegistrado";

/**
 * Lo que salió mal, agrupado por clave.
 *
 * **Es la mitad que motivó el punto 19.** Las corridas de abajo cuentan qué
 * produjo el motor; esto cuenta qué se rompió — y hasta ahora eso vivía sólo en
 * el log de Docker, que rota y se lee desde una terminal.
 *
 * **Es pestaña propia y no una lista adentro de Actividad**, y el motivo es de
 * función y no de prolijidad: separada, la pestaña puede llevar el contador.
 * Con eso te enterás de que algo se rompió **sin ir a buscarlo**, desde
 * cualquier pantalla — que es exactamente el problema que este punto vino a
 * resolver. Una lista adentro de otra pestaña no puede hacer eso.
 *
 * El tamaño está acotado por diseño: es **una fila por clave**, no por
 * ocurrencia. Medido el 12/09/2026, el máximo teórico con doce medios —o sea
 * todo roto a la vez— son 49 filas, y un motor sano tiene cero.
 */
/**
 * Cómo se muestra cada categoría.
 *
 * **Las claves las escribe el motor y pierden los acentos** (`sintesis`,
 * `extraccion`): son el prefijo del identificador con el que agrupa sus avisos,
 * no texto para leer. Acá se traducen a algo escrito bien.
 *
 * **No son momentos del pipeline, son subsistemas**: `ingesta` y `síntesis` sí
 * son pasos del ciclo, pero `robots`, `alertas` y `scheduler` no.
 *
 * Lo que no esté en el mapa cae al capitalizado de abajo, así que un aviso
 * nuevo del motor se muestra razonable sin tocar esto.
 */
const NOMBRE_DE_CATEGORIA: Record<string, string> = {
  ingesta: "Ingesta",
  extraccion: "Extracción",
  robots: "robots.txt",
  sintesis: "Síntesis",
  webhook: "Entrega al back-end",
  pipeline: "Pipeline",
  scheduler: "Scheduler",
  alertas: "Alertas",
};

function nombreDeCategoria(clave: string): string {
  return (
    NOMBRE_DE_CATEGORIA[clave] ?? clave.charAt(0).toUpperCase() + clave.slice(1)
  );
}

export default function Problemas() {
  const [eventos, setEventos] = useState<EventoRegistrado[] | null>(null);
  const [categorias, setCategorias] = useState<string[]>([]);
  const [filtro, setFiltro] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);

  const traer = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const r = await datos.listarEventos(filtro);
      setEventos(r.eventos);
      setCategorias(r.categorias);
    } catch (e) {
      setError(datos.mensajeDeRechazo(e));
    } finally {
      setCargando(false);
    }
  }, [filtro]);

  useEffect(() => {
    void traer();
  }, [traer]);

  return (
    <div className="ajustes">
      <section className="tarjeta">
        <div className="actividad-cabecera">
          <div>
            <h2>Qué se rompió</h2>
            <p className="ayuda sin-tope">
              Una fila por problema, con cuántas veces ocurrió.
            </p>
          </div>
          {/* El filtro sale de las categorías que existen de verdad, no de una
            lista fija: cuando el motor sume un aviso nuevo, aparece solo. */}
          {categorias.length > 1 && (
            <select
              value={filtro ?? ""}
              onChange={(e) => setFiltro(e.target.value || null)}
              aria-label="Filtrar por categoría"
            >
              <option value="">Todas las categorías</option>
              {categorias.map((c) => (
                <option key={c} value={c}>
                  {nombreDeCategoria(c)}
                </option>
              ))}
            </select>
          )}
        </div>

        {error && <div className="aviso">{error}</div>}

        {eventos !== null && eventos.length === 0 && !cargando && (
          <p className="ayuda">
            {filtro
              ? `Nada registrado en «${nombreDeCategoria(filtro)}».`
              : "Nada roto todavía. Si el motor viene corriendo hace rato, es una buena noticia."}
          </p>
        )}

        <ul className="lista-eventos">
          {(eventos ?? []).map((e) => (
            <li
              key={e.id}
              className={e.terminal ? "evento terminal" : "evento"}
            >
              <div className="evento-cabeza">
                <b>{e.asunto}</b>
                {/* El contador es lo que distingue un tropiezo de algo roto, y
                  se pierde si el evento sólo se registrara cuando el mail sale. */}
                {e.veces > 1 && (
                  <span className="evento-veces">{e.veces}×</span>
                )}
                <span className="evento-cuando">
                  {e.ultima_vez.replace("T", " ").slice(0, 16)}
                </span>
              </div>
              <p className="evento-mensaje">{e.mensaje}</p>
              <p className="evento-clave">
                <code>{e.clave}</code>
                {e.veces > 1 && ` · desde ${e.primera_vez.slice(0, 10)}`}
              </p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
