/**
 * Lo que el motor sabe, envuelto en funciones con nombre.
 *
 * **Acá, y en ningún otro archivo, se escriben los nombres de los argumentos de
 * los comandos.** Tauri los convierte a camelCase antes de buscarlos
 * (`ArgumentCase::Camel` es el default de `tauri-macros`), así que escribir
 * `cluster_id` compila, pasa `tsc` y **falla en ejecución sin decir por qué**:
 * el argumento no matchea, llega como `None` y el filtro simplemente no se
 * aplica. Medido el 07/09/2026: la misma consulta devolvió 1 fila con
 * `clusterId` y 100 con `cluster_id`.
 *
 * Concentrarlos acá deja **una sola línea donde equivocarse** por comando, en
 * vez de una por cada lugar de la interfaz que lo llame.
 */
import { invoke } from "@tauri-apps/api/core";

import type { ErrorDeApi } from "./bindings/ErrorDeApi";
import type { RespuestaClusters } from "./bindings/RespuestaClusters";
import type { RespuestaDetalle } from "./bindings/RespuestaDetalle";
import type { RespuestaActivarModelo } from "./bindings/RespuestaActivarModelo";
import type { RespuestaAltaModelo } from "./bindings/RespuestaAltaModelo";
import type { RespuestaEntrega } from "./bindings/RespuestaEntrega";
import type { RespuestaModelos } from "./bindings/RespuestaModelos";
import type { RespuestaPipeline } from "./bindings/RespuestaPipeline";
import type { RespuestaSintesis } from "./bindings/RespuestaSintesis";
import type { Salud } from "./bindings/Salud";
import type { Sintetizado } from "./bindings/Sintetizado";

export type { ErrorDeApi };

/** Los estados que el motor le pone a un cluster. `null` es "todos". */
export type FiltroDeEstado = "abierto" | "procesado" | "descartado" | null;

/**
 * Los argumentos opcionales se **omiten**, no se mandan en `undefined`.
 *
 * Del lado de Rust son `Option<T>`, y serde los toma como `None` cuando la
 * clave no está. Mandar `undefined` probablemente funcione igual —el
 * serializador la descarta— pero eso es una suposición sobre el puente, y este
 * es exactamente el lugar donde las suposiciones sobre el puente no se ven
 * fallar: el argumento llega como `None`, el filtro no se aplica, y la pantalla
 * muestra de más sin que nada se queje.
 */
function conOpcionales(base: Record<string, unknown>, opcionales: Record<string, unknown>) {
  const args = { ...base };
  for (const [clave, valor] of Object.entries(opcionales)) {
    if (valor !== null && valor !== undefined) args[clave] = valor;
  }
  return args;
}

/**
 * El `GET /` del motor. **Es la única ruta abierta**, así que contesta aunque no
 * haya token — y eso es justamente para qué se la usa acá: `exige_token` es la
 * forma de saber si hace falta pedirlo, antes de pedirlo.
 *
 * `entrega_configurada` dice si hay a dónde entregar, sin decir a dónde.
 */
export function salud(): Promise<Salud> {
  return invoke<Salud>("motor_salud");
}

export function listarClusters(
  estado: FiltroDeEstado,
  limite = 20,
): Promise<RespuestaClusters> {
  return invoke<RespuestaClusters>(
    "listar_clusters",
    conOpcionales({ limite }, { estado }),
  );
}

/**
 * Una página de síntesis. `cursor` sale del campo `siguiente` de la respuesta
 * anterior y se manda tal cual: es base64 y el único que sabe leerlo es el
 * motor.
 */
export function listarSintesis(opciones: {
  limite?: number;
  cursor?: string | null;
  clusterId?: number | null;
  entregado?: boolean | null;
}): Promise<RespuestaSintesis> {
  return invoke<RespuestaSintesis>(
    "listar_sintesis",
    conOpcionales(
      { limite: opciones.limite ?? 20 },
      {
        cursor: opciones.cursor,
        clusterId: opciones.clusterId,
        entregado: opciones.entregado,
      },
    ),
  );
}

/**
 * Prende o apaga un modelo. **Prender uno apaga a los demas**, y lo decide el
 * motor: la app no manda apagar nada.
 *
 * Tarda, porque el motor sondea al proveedor antes de prender. Es a proposito:
 * asi un error de credencial sale cuando se aprieta el boton y no quince minutos
 * despues, adentro del paso mas caro del pipeline.
 */
export function activarModelo(
  modeloId: number,
  activo: boolean,
): Promise<RespuestaActivarModelo> {
  return invoke<RespuestaActivarModelo>("activar_modelo", { modeloId, activo });
}

/**
 * Da de alta un modelo. El motor lo sondea contra el proveedor antes de
 * guardarlo, asi que un rechazo aca dice que esa configuracion no sirve **y por
 * que**, en vez de aceptarla y fallar despues en cada sintesis.
 *
 * No se manda la credencial ni el nombre de su variable: eso vive en el `.env`
 * del motor y el alta no lo acepta.
 */
export function altaModelo(datos: {
  nombre: string;
  adaptador: string;
  modelo: string;
  baseUrl: string | null;
  activar: boolean;
}): Promise<RespuestaAltaModelo> {
  return invoke<RespuestaAltaModelo>("alta_modelo", {
    nombre: datos.nombre,
    adaptador: datos.adaptador,
    modelo: datos.modelo,
    baseUrl: datos.baseUrl,
    activar: datos.activar,
  });
}

/** El destino de entrega. Exige token siempre, aun con la API abierta. */
export function entregaVer(): Promise<RespuestaEntrega> {
  return invoke<RespuestaEntrega>("entrega_ver");
}

/**
 * Cambia el destino. `null` lo desconfigura.
 *
 * **No reenvia nada**: el destino nuevo recibe desde la proxima sintesis. El
 * historico se manda aparte, con una accion que tiene ese nombre.
 */
export function entregaCambiar(url: string | null): Promise<RespuestaEntrega> {
  return invoke<RespuestaEntrega>("entrega_cambiar", { url });
}

export function detalleDeSintesis(id: number): Promise<RespuestaDetalle> {
  return invoke<RespuestaDetalle>("detalle_de_sintesis", { id });
}

export function estadoDelPipeline(historial = 5): Promise<RespuestaPipeline> {
  return invoke<RespuestaPipeline>("estado_del_pipeline", { historial });
}

export function listarModelos(): Promise<RespuestaModelos> {
  return invoke<RespuestaModelos>("listar_modelos", {});
}

/**
 * Sintetiza un cluster puntual. **Puede costar plata.**
 *
 * `forzar` va explícito y sin default por la misma razón que del lado de Rust:
 * es lo único que separa "volver a sintetizar" de gastar una llamada paga sin
 * que nadie lo haya decidido. Un default acá la volvería invisible.
 */
export function sintetizarCluster(
  clusterId: number,
  modeloId: number | null,
  forzar: boolean,
): Promise<Sintetizado> {
  return invoke<Sintetizado>(
    "sintetizar_cluster",
    conOpcionales({ clusterId, forzar }, { modeloId }),
  );
}

/**
 * El texto que se le muestra a quien mira. **Cada categoría dice qué hacer**,
 * que es para lo que el motor las devuelve cerradas en vez de mandar un string.
 */
export function mensajeDeApi(error: ErrorDeApi): string {
  switch (error.tipo) {
    case "sin_token":
      return "Falta configurar el token del motor.";
    case "motor_caido":
      return "El motor no responde. Fijate el semáforo de arriba.";
    case "no_autorizado":
      return "El motor rechazó el token. Puede haber cambiado en el .env.";
    // El motor vive pero no puede atender **esto**, y ya explicó por qué. Su
    // texto va tal cual: viene saneado y dice qué configurar, que es más de lo
    // que podríamos agregarle desde acá. Hoy el caso real es `/entrega` contra
    // un motor sin `API_TOKEN`.
    case "no_disponible":
      return error.detalle;
    case "no_encontrado":
      return "El motor no encontró eso. Puede haberse borrado; probá refrescar.";
    case "invalida":
      return `El motor rechazó el pedido: ${error.detalle}`;
    case "respuesta":
      return `El motor respondió ${error.detalle}.`;
    case "red":
      return `Error de red: ${error.detalle}`;
  }
}

/**
 * Un `invoke` puede fallar **antes** de llegar a nuestro código —el propio
 * puente rechaza y devuelve un string—, así que lo que vuelve no siempre tiene
 * la forma del enum. Suponer que sí es cómo una pantalla termina mostrando
 * "undefined".
 */
export function mensajeDeRechazo(e: unknown): string {
  if (typeof e === "object" && e !== null && "tipo" in e) {
    return mensajeDeApi(e as ErrorDeApi);
  }
  return String(e);
}
