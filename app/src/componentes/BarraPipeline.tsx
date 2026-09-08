/**
 * En qué anda el ciclo automático.
 *
 * Lo que hace falta saber antes de decidir sintetizar algo a mano: si el
 * scheduler está corriendo justo ahora, cuándo fue la última vuelta, si quedó
 * alguna colgada, y qué hizo.
 *
 * **`pasos` es un mapa abierto, no un struct**, y por eso acá hay un
 * normalizador en vez de un `.map()` sobre campos conocidos: sus claves las
 * decide `_correr_paso` del lado del motor, vienen en español con acentos y
 * espacios (`síntesis`, `entrega al backend`, `purga de cuerpos`), y los valores
 * no son homogéneos. Medido sobre corridas reales, el segundo nivel trae
 * enteros, booleanos, strings, listas y objetos anidados.
 *
 * La versión anterior hacía `String(valor)` sobre cada uno, así que todo lo que
 * no fuera un escalar salía como `[object Object]`.
 */
import { useState } from "react";

import type { Corrida } from "../bindings/Corrida";
import type { RespuestaPipeline } from "../bindings/RespuestaPipeline";

type Dato = { etiqueta: string; valor: string; vacio: boolean };

function esObjeto(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Un escalar a texto, diciendo además si cuenta como "no pasó nada". */
function escalar(etiqueta: string, v: unknown): Dato | null {
  if (typeof v === "number") return { etiqueta, valor: String(v), vacio: v === 0 };
  if (typeof v === "boolean") return { etiqueta, valor: v ? "sí" : "no", vacio: !v };
  if (typeof v === "string") return { etiqueta, valor: v, vacio: false };
  return null;
}

/**
 * Aplana el contenido de un paso a pares etiqueta/valor.
 *
 * Baja **un solo nivel** de anidamiento —alcanza para todo lo que el motor
 * emite hoy, como `síntesis.por_modelo`— y lo que no encaje se resume en vez de
 * descartarse: es preferible mostrar "3 elementos" que perder el dato en
 * silencio, porque este mapa puede cambiar del lado del motor sin avisar.
 */
function aplanar(contenido: unknown): Dato[] {
  // `ingesta` es una lista con un objeto por medio: se suman los numéricos.
  if (Array.isArray(contenido)) {
    const suma = new Map<string, number>();
    let conObjetos = 0;
    for (const item of contenido) {
      if (!esObjeto(item)) continue;
      conObjetos += 1;
      for (const [k, v] of Object.entries(item)) {
        if (typeof v === "number") suma.set(k, (suma.get(k) ?? 0) + v);
        else if (Array.isArray(v)) suma.set(k, (suma.get(k) ?? 0) + v.length);
      }
    }
    const datos: Dato[] = [
      { etiqueta: "medios", valor: String(conObjetos || contenido.length), vacio: false },
    ];
    for (const [k, v] of suma) datos.push({ etiqueta: k, valor: String(v), vacio: v === 0 });
    return datos;
  }

  if (!esObjeto(contenido)) {
    const solo = escalar("valor", contenido);
    return solo === null ? [] : [solo];
  }

  const datos: Dato[] = [];
  for (const [clave, valor] of Object.entries(contenido)) {
    const plano = escalar(clave, valor);
    if (plano !== null) {
      datos.push(plano);
      continue;
    }
    if (Array.isArray(valor)) {
      datos.push({ etiqueta: clave, valor: String(valor.length), vacio: valor.length === 0 });
      continue;
    }
    if (esObjeto(valor)) {
      const dentro = Object.entries(valor);
      if (dentro.length === 0) {
        datos.push({ etiqueta: clave, valor: "0", vacio: true });
        continue;
      }
      for (const [sub, v] of dentro) {
        const anidado = escalar(`${clave}·${sub}`, v);
        datos.push(anidado ?? { etiqueta: `${clave}·${sub}`, valor: "…", vacio: false });
      }
    }
  }
  return datos;
}

function cuandoFue(iso: string): string {
  const minutos = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutos < 1) return "recién";
  if (minutos < 60) return `hace ${minutos} min`;
  const horas = Math.round(minutos / 60);
  if (horas < 24) return `hace ${horas} h`;
  return `hace ${Math.round(horas / 24)} d`;
}

function Pasos({ corrida }: { corrida: Corrida }) {
  const pasos = Object.entries(corrida.pasos).map(
    ([nombre, contenido]) => [nombre, aplanar(contenido)] as const,
  );
  if (pasos.length === 0) {
    return <p className="vacio">Esta corrida no registró pasos.</p>;
  }
  return (
    <div className="pipeline-detalle">
      {pasos.map(([nombre, datos]) => (
        <article key={nombre} className="pipeline-paso">
          <h4>{nombre}</h4>
          {datos.length === 0 ? (
            <span className="pipeline-nada">sin datos</span>
          ) : (
            <dl>
              {datos.map((d) => (
                // Lo que quedó en cero se atenúa en vez de esconderse: que un
                // contador exista y esté en cero dice algo distinto de que no
                // exista, y el ojo igual va primero a lo que no es cero.
                <div key={d.etiqueta} className={d.vacio ? "en-cero" : undefined}>
                  <dt>{d.etiqueta}</dt>
                  <dd>{d.valor}</dd>
                </div>
              ))}
            </dl>
          )}
        </article>
      ))}
    </div>
  );
}

export default function BarraPipeline({
  pipeline,
  error,
}: {
  pipeline: RespuestaPipeline | null;
  error: string | null;
}) {
  const [abierto, setAbierto] = useState(false);

  if (error) return <div className="pipeline aviso">{error}</div>;
  if (!pipeline) return <div className="pipeline pipeline-vacio">Preguntando por el ciclo…</div>;

  const ultima = pipeline.ultima;

  return (
    <div className="pipeline">
      <div className="pipeline-fila">
        <span className={`estado ${pipeline.corriendo ? "alerta" : "ok"}`}>
          {pipeline.corriendo ? "Ciclo en curso" : "En reposo"}
        </span>
        <span className="pipeline-dato">cada {pipeline.intervalo_minutos} min</span>
        {/* `ultima` es `null` en un motor que nunca corrió. Es un caso real y
            distinto de "no vino el dato", por eso el tipo lo obliga a mirar. */}
        {ultima === null ? (
          <span className="pipeline-dato">Nunca corrió</span>
        ) : (
          <>
            <span className="pipeline-dato">{cuandoFue(ultima.inicio)}</span>
            {ultima.duracion_segundos !== null && (
              <span className="pipeline-dato">{Math.round(ultima.duracion_segundos)}s</span>
            )}
            <button
              className="secundario chico pipeline-toggle"
              aria-expanded={abierto}
              onClick={() => setAbierto(!abierto)}
            >
              {abierto ? "Ocultar pasos" : `${Object.keys(ultima.pasos).length} pasos`}
            </button>
          </>
        )}
      </div>

      {abierto && ultima !== null && <Pasos corrida={ultima} />}

      {/* Se informa aparte en vez de mentir `corriendo: true` para siempre:
          es una corrida que quedó abierta de un proceso que murió. */}
      {pipeline.huerfana && (
        <div className="aviso">
          Quedó una corrida <b>huérfana</b>: se abrió y nunca cerró, así que el
          proceso que la ejecutaba murió a mitad de camino. El ciclo siguiente la
          reemplaza; si se repite, mirá <code>docker compose logs app</code>.
        </div>
      )}
    </div>
  );
}
