import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

// Los tipos que cruzan desde Rust no se escriben acá: los genera `ts-rs` a
// partir de los enums de Rust, que son los que definen el payload de verdad.
// Ver `app/src/bindings/` y el README.
import type { Estado } from "./bindings/Estado";
import type { ErrorDocker } from "./bindings/ErrorDocker";

export type { Estado, ErrorDocker };

/** Qué se le muestra a quien mira, y con qué color. */
export function describir(estado: Estado): { texto: string; clase: string } {
  switch (estado) {
    case "parado":
      return { texto: "detenido", clase: "alerta" };
    case "reconstruyendo":
      // Puede tardar minutos la primera vez después de tocar requirements.txt,
      // y por eso es un estado propio y no "arrancando": una ventana que dice
      // "arrancando" durante cinco minutos parece colgada.
      return { texto: "reconstruyendo la imagen…", clase: "alerta" };
    case "arrancando":
      return { texto: "arrancando…", clase: "alerta" };
    case "migrando":
      return { texto: "migrando la base…", clase: "alerta" };
    case "listo":
      return { texto: "operativo", clase: "ok" };
    case "error":
      return { texto: "con problemas", clase: "error" };
  }
}

/**
 * El mensaje que se muestra. **Cada uno dice qué hacer**, que es lo que le
 * faltaba al stderr de Docker cuando iba crudo a la pantalla.
 */
export function mensajeDeError(error: ErrorDocker): string {
  switch (error.tipo) {
    case "no_instalado":
      return "No se encontró Docker. Hace falta Docker Desktop para levantar el motor.";
    case "demonio_caido":
      return "Docker Desktop no está corriendo. Abrilo, esperá a que diga «Engine running» y volvé a intentar.";
    case "fallo":
      return error.detalle;
  }
}

/**
 * Traduce lo que rechaza un `invoke`. Un comando de Tauri puede fallar antes de
 * llegar a nuestro código —el propio puente devuelve un string— así que lo que
 * vuelve no siempre tiene la forma del enum.
 */
export function mensajeDeRechazo(e: unknown): string {
  if (typeof e === "object" && e !== null && "tipo" in e) {
    return mensajeDeError(e as ErrorDocker);
  }
  return String(e);
}

/** El estado actual, sondeado una vez. */
export function estadoActual(): Promise<Estado> {
  return invoke<Estado>("motor_estado");
}

/**
 * Se suscribe a las transiciones que emite Rust durante el arranque.
 *
 * Rust avisa **en cada cambio** y no solo al final: entre `reconstruyendo` y
 * `listo` pueden pasar minutos, y sin estos avisos la ventana no tendría nada
 * que mostrar mientras tanto.
 */
export function alCambiarEstado(fn: (estado: Estado) => void): Promise<() => void> {
  return listen<Estado>("motor-estado", (evento) => fn(evento.payload));
}

export function arrancar(): Promise<Estado> {
  return invoke<Estado>("motor_arrancar");
}

export function detener(): Promise<Estado> {
  return invoke<Estado>("motor_detener");
}

export function repoLeer(): Promise<string | null> {
  return invoke<string | null>("repo_leer");
}

export function repoGuardar(ruta: string): Promise<void> {
  return invoke("repo_guardar", { ruta });
}
