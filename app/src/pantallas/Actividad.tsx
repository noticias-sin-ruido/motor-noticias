/**
 * Qué viene haciendo el motor: una fila por corrida, con lo que produjo.
 *
 * **Existe porque enterarse no puede depender del mail.** El 12/09/2026 se
 * descubrió que las nueve alertas del motor no le llegaban a nadie desde hacía
 * meses: la casilla estaba deshabilitada y el único rastro de cada fallo era un
 * `logger.error` en un log que sólo se ve con `docker logs` desde una terminal.
 * Punto 19 del backlog.
 *
 * **Este es el paso 1 de cuatro, y no toca el motor.** `GET /pipeline` ya
 * devuelve hasta 50 corridas con su detalle; lo único que faltaba era mostrarlas
 * en vez de quedarse con la última. Los eventos —el feed que falló, el back-end
 * que rechazó— llegan en el paso 3, a esta misma pantalla.
 */
import { useCallback, useEffect, useState } from "react";

import * as datos from "../datos";
import type { Corrida } from "../bindings/Corrida";

/**
 * Cuántas corridas se traen. El motor topea en 200.
 *
 * **El número sale del ciclo, no de una intuición**: con el intervalo en 15
 * minutos son 96 corridas por día, así que 200 son unos dos días. Con menos, la
 * pantalla no puede contestar «¿desde cuándo viene pasando esto?», que es para
 * lo que existe.
 */
const CUANTAS = 200;

/**
 * Qué se muestra de cada corrida, **en orden del pipeline y no del
 * diccionario**.
 *
 * El orden importa para leer: las claves de `pasos` vienen como el motor las
 * escribió, así que sin esto una fila decía «purga · ingesta · síntesis» y otra
 * «ingesta · síntesis · purga». Con la secuencia fija, la misma columna quiere
 * decir siempre lo mismo y la vista se recorre de arriba abajo.
 *
 * **No está todo lo que el motor reporta, y es deliberado.** `vectorizadas`
 * casi siempre iguala a las noticias nuevas, así que mostrar las dos era decir
 * el mismo número dos veces. Lo que queda es lo que varía y decide algo.
 */
const PRODUCIDO: Array<{ paso: string; campo: string; etiqueta: string }> = [
  { paso: "ingesta", campo: "nuevas", etiqueta: "noticias" },
  { paso: "agrupamiento", campo: "clusters_creados", etiqueta: "clusters" },
  { paso: "síntesis", campo: "sintetizados", etiqueta: "síntesis" },
  { paso: "entrega al backend", campo: "entregadas", etiqueta: "entregadas" },
  { paso: "purga de cuerpos", campo: "purgadas", etiqueta: "purgadas" },
];

/**
 * Lo que salió mal. **Va aparte y no mezclado con lo producido**, que era el
 * problema de la primera versión: un `5 fallaron` quedaba en el medio de una
 * tira de texto, entre dos números buenos, y se perdía.
 */
const FALLAS: Array<{ paso: string; campo: string; etiqueta: string }> = [
  { paso: "ingesta", campo: "feeds_fallados", etiqueta: "feeds caídos" },
  { paso: "ingesta", campo: "extraccion_fallida", etiqueta: "sin cuerpo" },
  { paso: "síntesis", campo: "fallidos", etiqueta: "síntesis fallaron" },
  { paso: "síntesis", campo: "vencidos_sin_publicar", etiqueta: "vencidas" },
  { paso: "entrega al backend", campo: "agotadas", etiqueta: "agotadas" },
];

/**
 * Suma un campo de un paso. **Cualquier forma inesperada devuelve 0 en vez de
 * romper**: `pasos` es `Record<string, unknown>` porque el motor decide sus
 * claves en tiempo de ejecución, y un panel que existe para ver qué se rompió no
 * puede ser lo que se rompe.
 */
function contar(
  pasos: Record<string, unknown>,
  paso: string,
  campo: string,
): number {
  const valor = pasos[paso];
  // `ingesta` es una lista con una entrada por medio; el resto son objetos.
  if (Array.isArray(valor)) {
    let total = 0;
    for (const m of valor) {
      if (typeof m !== "object" || m === null) continue;
      const n = (m as Record<string, unknown>)[campo];
      if (typeof n === "number") total += n;
    }
    return total;
  }
  if (typeof valor === "object" && valor !== null) {
    const n = (valor as Record<string, unknown>)[campo];
    return typeof n === "number" ? n : 0;
  }
  return 0;
}

function resumir(
  pasos: Record<string, unknown>,
  cuales: typeof PRODUCIDO,
): string[] {
  return cuales
    .map(({ paso, campo, etiqueta }) => ({
      n: contar(pasos, paso, campo),
      etiqueta,
    }))
    .filter(({ n }) => n > 0)
    .map(({ n, etiqueta }) => `${n} ${etiqueta}`);
}

function FilaDeCorrida({ corrida }: { corrida: Corrida }) {
  const pasos = (corrida.pasos ?? {}) as Record<string, unknown>;
  const hizo = resumir(pasos, PRODUCIDO);
  const fallo = resumir(pasos, FALLAS);
  const hora = corrida.inicio.slice(11, 16);

  return (
    <li
      className={[
        "corrida",
        fallo.length > 0 ? "con-falla" : hizo.length > 0 ? "hizo-algo" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <span className="corrida-hora">{hora}</span>
      <span className="corrida-hizo">
        {hizo.length > 0 ? hizo.join(" · ") : <em>sin novedades</em>}
        {fallo.length > 0 && (
          <b className="corrida-fallo"> {fallo.join(" · ")}</b>
        )}
      </span>
      <span className="corrida-duracion">
        {corrida.fin === null ? (
          <span className="sin-probar">sin cerrar</span>
        ) : corrida.duracion_segundos !== null ? (
          `${Math.round(corrida.duracion_segundos)} s`
        ) : null}
      </span>
    </li>
  );
}

/** `2026-09-12` → `viernes 12 de septiembre`, o «hoy» / «ayer». */
function nombreDelDia(iso: string): string {
  const dia = new Date(`${iso}T12:00:00`);
  const hoy = new Date();
  const aDia = (d: Date) => Math.floor(d.getTime() / 86400000);
  const diferencia = aDia(hoy) - aDia(dia);
  if (diferencia === 0) return "Hoy";
  if (diferencia === 1) return "Ayer";
  return dia.toLocaleDateString("es-AR", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

export default function Actividad() {
  const [corridas, setCorridas] = useState<Corrida[] | null>(null);
  const [intervalo, setIntervalo] = useState<number | null>(null);
  const [corriendo, setCorriendo] = useState(false);
  const [huerfana, setHuerfana] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);

  const traer = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const r = await datos.estadoDelPipeline(CUANTAS);
      // `ultima` viene aparte de `anteriores`: se juntan acá porque para quien
      // mira son la misma lista.
      setCorridas(r.ultima ? [r.ultima, ...r.anteriores] : r.anteriores);
      setIntervalo(r.intervalo_minutos);
      setCorriendo(r.corriendo);
      setHuerfana(r.huerfana);
    } catch (e) {
      setError(datos.mensajeDeRechazo(e));
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void traer();
  }, [traer]);

  // **Agrupadas por día.** Sin esto cada fila repetía la fecha completa
  // veinticinco veces, y lo único que cambiaba entre una y otra eran cuatro
  // dígitos perdidos al final.
  const porDia = new Map<string, Corrida[]>();
  for (const c of corridas ?? []) {
    const dia = c.inicio.slice(0, 10);
    porDia.set(dia, [...(porDia.get(dia) ?? []), c]);
  }

  return (
    <div className="ajustes">
      <section className="tarjeta">
        <div className="actividad-cabecera">
          <div>
            <h2>Qué viene haciendo el motor</h2>
            <p className="ayuda sin-tope">
              Una corrida del ciclo por fila
              {intervalo !== null && `, cada ${intervalo} minutos`}.
              {corriendo && " Ahora mismo hay una en curso."}
            </p>
          </div>
          {/* **Arriba y no al final.** Lo de arriba es lo último que pasó, que
              es lo que uno viene a mirar; el botón al pie quedaba junto a lo más
              viejo, o sea lejos de la razón para apretarlo. */}
          <button
            type="button"
            className="chico secundario"
            onClick={() => void traer()}
            disabled={cargando}
          >
            {cargando ? "Actualizando…" : "Actualizar"}
          </button>
        </div>

        {/* Una corrida abierta de un proceso que murió. El motor la informa
            aparte en vez de mentir «corriendo» para siempre, y acá se dice
            porque explica un pipeline que parece trabado y no lo está. */}
        {huerfana && (
          <div className="aviso">
            Quedó una corrida abierta de un proceso que se cortó. No está
            corriendo: la próxima arranca normal.
          </div>
        )}

        {error && <div className="aviso">{error}</div>}

        {corridas !== null && corridas.length === 0 && (
          <p className="ayuda">
            El motor todavía no completó ninguna corrida. Si recién arrancó,
            esperá a que cierre su primer ciclo.
          </p>
        )}

        {[...porDia.entries()].map(([dia, delDia]) => (
          <div key={dia} className="actividad-dia">
            <h3>{nombreDelDia(dia)}</h3>
            <ul className="lista-corridas">
              {delDia.map((c) => (
                <FilaDeCorrida key={c.id} corrida={c} />
              ))}
            </ul>
          </div>
        ))}
      </section>
    </div>
  );
}
