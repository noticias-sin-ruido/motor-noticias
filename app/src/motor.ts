import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

// Los tipos que cruzan desde Rust no se escriben acá: los genera `ts-rs` a
// partir de los enums de Rust, que son los que definen el payload de verdad.
// Ver `app/src/bindings/` y el README.
import type { Estado } from "./bindings/Estado";
import type { ErrorDocker } from "./bindings/ErrorDocker";

export type { Estado, ErrorDocker };

/**
 * Qué se le muestra a quien mira, y con qué color.
 *
 * **El ámbar está reservado a lo que está en curso** —reconstruyendo,
 * arrancando, migrando—, o sea a estados que se resuelven solos si uno espera.
 * `parado` no es uno de esos: no avanza a ningún lado hasta que alguien
 * apriete un botón, y en ámbar se confundía con los tres transitorios. Por eso
 * va en rojo, igual que `error`.
 *
 * Que dos estados compartan color no los vuelve indistinguibles: cada uno
 * dice su texto al lado del punto, que es lo que evita informar sólo por
 * color.
 */
export function describir(estado: Estado): { texto: string; clase: string } {
  switch (estado) {
    case "parado":
      return { texto: "Detenido", clase: "error" };
    case "reconstruyendo":
      // Puede tardar minutos la primera vez después de tocar requirements.txt,
      // y por eso es un estado propio y no "arrancando": una ventana que dice
      // "arrancando" durante cinco minutos parece colgada.
      return { texto: "Reconstruyendo la imagen…", clase: "alerta" };
    case "arrancando":
      return { texto: "Arrancando…", clase: "alerta" };
    case "migrando":
      return { texto: "Migrando la base…", clase: "alerta" };
    case "listo":
      return { texto: "Operativo", clase: "ok" };
    case "error":
      return { texto: "Con problemas", clase: "error" };
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
