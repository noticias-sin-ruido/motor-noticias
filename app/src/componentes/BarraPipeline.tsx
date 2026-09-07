/**
 * En qué anda el ciclo automático.
 *
 * Lo que hace falta saber antes de decidir sintetizar algo a mano: si el
 * scheduler está corriendo justo ahora, cuándo fue la última vuelta y si quedó
 * alguna colgada.
 */
import type { Corrida } from "../bindings/Corrida";
import type { RespuestaPipeline } from "../bindings/RespuestaPipeline";

function cuandoFue(iso: string): string {
  const minutos = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutos < 1) return "recién";
  if (minutos < 60) return `hace ${minutos} min`;
  const horas = Math.round(minutos / 60);
  if (horas < 24) return `hace ${horas} h`;
  return `hace ${Math.round(horas / 24)} d`;
}

function Resumen({ corrida }: { corrida: Corrida }) {
  const pasos = Object.entries(corrida.pasos);
  return (
    <>
      <span className="pipeline-dato">{cuandoFue(corrida.inicio)}</span>
      {corrida.duracion_segundos !== null && (
        <span className="pipeline-dato">{corrida.duracion_segundos}s</span>
      )}
      {pasos.length > 0 && (
        <span className="pipeline-pasos">
          {pasos.map(([nombre, valor]) => (
            <span key={nombre} className="pipeline-paso">
              {nombre} <b>{String(valor)}</b>
            </span>
          ))}
        </span>
      )}
    </>
  );
}

export default function BarraPipeline({
  pipeline,
  error,
}: {
  pipeline: RespuestaPipeline | null;
  error: string | null;
}) {
  if (error) return <div className="pipeline aviso">{error}</div>;
  if (!pipeline) return <div className="pipeline pipeline-vacio">Preguntando por el ciclo…</div>;

  return (
    <div className="pipeline">
      <div className="pipeline-fila">
        <span className={`estado ${pipeline.corriendo ? "alerta" : "ok"}`}>
          {pipeline.corriendo ? "Ciclo en curso" : "En reposo"}
        </span>
        <span className="pipeline-dato">cada {pipeline.intervalo_minutos} min</span>
        {/* `ultima` es `null` en un motor que nunca corrió. Es un caso real y
            distinto de "no vino el dato", por eso el tipo lo obliga a mirar. */}
        {pipeline.ultima === null ? (
          <span className="pipeline-dato">Nunca corrió</span>
        ) : (
          <Resumen corrida={pipeline.ultima} />
        )}
      </div>

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
