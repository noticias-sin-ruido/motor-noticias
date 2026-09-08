/**
 * Un ángulo, resumido, tal como llega de `GET /sintesis`.
 *
 * La unidad del feed no es el hecho sino el **ángulo**: un cluster produce
 * varias síntesis porque separar el material en recortes —el hecho, sus
 * consecuencias, las reacciones— es trabajo del modelo. Por eso la tarjeta
 * titula con `titulo_angulo` y no con el evento.
 *
 * Es un resumen a propósito: el contenido —resumen neutro, puntos clave y la
 * comparativa por medio— se pide aparte con `detalle_de_sintesis`. Traer la
 * comparativa completa de veinte ítems para elegir uno sería pagar la lectura
 * entera para tomar una decisión.
 */
import type { ResumenSintesis } from "../bindings/ResumenSintesis";

function cuando(iso: string): string {
  const minutos = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutos < 1) return "recién";
  if (minutos < 60) return `hace ${minutos} min`;
  const horas = Math.round(minutos / 60);
  if (horas < 24) return `hace ${horas} h`;
  const dias = Math.round(horas / 24);
  return dias === 1 ? "ayer" : `hace ${dias} días`;
}

export default function TarjetaAngulo({
  sintesis,
  alAbrir,
}: {
  sintesis: ResumenSintesis;
  alAbrir: (sintesis: ResumenSintesis) => void;
}) {
  return (
    <li className="tarjeta-angulo">
      <div className="ta-cab">
        {/* `title` con el texto completo: el recorte a tres renglones alcanza
            para la enorme mayoría, pero no para todos, y lo que se recorta
            tiene que seguir siendo alcanzable. */}
        <h3 className="ta-titulo" title={sintesis.titulo_angulo}>
          {sintesis.titulo_angulo}
        </h3>
        {/* Entregado o no va en texto y no sólo en color: es la diferencia
            entre "el back-end ya lo tiene" y "todavía no salió". */}
        <span className={`chip ${sintesis.enviado_backend ? "chip-entregado" : "chip-pendiente"}`}>
          {sintesis.enviado_backend ? "entregado" : "sin entregar"}
        </span>
      </div>

      <div className="ta-cuerpo">
      {sintesis.topicos.length > 0 && (
        <ul className="ta-topicos">
          {sintesis.topicos.map((t) => (
            <li key={t}>{t}</li>
          ))}
          {sintesis.subtopicos.map((t) => (
            <li key={t} className="ta-subtopico">
              {t}
            </li>
          ))}
        </ul>
      )}

      <div className="ta-datos">
        <span>{cuando(sintesis.fecha_generacion)}</span>
        <span>
          {sintesis.cantidad_notas} {sintesis.cantidad_notas === 1 ? "nota" : "notas"} ·{" "}
          {sintesis.medios.join(", ")}
        </span>
        {/* `modelo_usado` es nullable en el modelo del motor: una síntesis vieja
            puede no tenerlo. Se dice, en vez de mostrar un hueco. */}
        <span className="ta-modelo">
          {sintesis.modelo_usado ?? "modelo no registrado"}
        </span>
      </div>
      </div>

      <div className="ta-acciones">
        <button className="chico" onClick={() => alAbrir(sintesis)}>
          Leer el ángulo
        </button>
        <span className="ta-id">#{sintesis.id} · cluster {sintesis.cluster_id}</span>
      </div>
    </li>
  );
}
