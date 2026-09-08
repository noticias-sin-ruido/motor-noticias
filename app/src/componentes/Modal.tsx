/**
 * La caja modal y lo que le debe al teclado.
 *
 * Se extrajo al aparecer el segundo modal, no antes: con uno solo, el manejo de
 * foco vivía adentro del componente y estaba bien ahí. Con dos, dejarlo
 * duplicado es pedir que se desincronicen — y lo que se desincroniza en silencio
 * es siempre la mitad menos visible, o sea la del teclado.
 */
import { useEffect, useRef } from "react";

/** Lo que puede recibir foco adentro del diálogo. */
const ENFOCABLES =
  'button:not([disabled]), select:not([disabled]), input:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

export default function Modal({
  titulo,
  bajada,
  bloqueado = false,
  alCerrar,
  ancho = "normal",
  children,
}: {
  titulo: string;
  bajada?: string;
  /** Mientras esté en `true`, Escape no cierra. */
  bloqueado?: boolean;
  alCerrar: () => void;
  /**
   * El ancho va por clase y **no por `style` en línea**: la CSP de producción
   * es `default-src 'self'` sin `'unsafe-inline'`, y eso bloquea también los
   * atributos `style`. Un ancho puesto así se vería bien en desarrollo —donde
   * no hay CSP— y se perdería en el ejecutable, sin avisar.
   */
  ancho?: "normal" | "ancho";
  children: React.ReactNode;
}) {
  const caja = useRef<HTMLDivElement>(null);
  // `bloqueado` y `alCerrar` se leen desde refs y no desde las dependencias: si
  // entraran al efecto, cada render volvería a montar el manejador y a robar el
  // foco de donde lo haya dejado quien está usando la app.
  const bloqueadoRef = useRef(bloqueado);
  const cerrarRef = useRef(alCerrar);
  bloqueadoRef.current = bloqueado;
  cerrarRef.current = alCerrar;

  /**
   * Las cuatro cosas que un diálogo modal le debe al teclado: foco inicial
   * adentro, Escape para salir, el tabulador atrapado en la caja, y devolver el
   * foco a donde estaba al cerrar. Sin esto se puede abrir el diálogo y quedar
   * encerrado sin mouse.
   */
  /**
   * **El fondo no se mueve mientras el diálogo está abierto.**
   *
   * Sin esto, la rueda del mouse sobre el fondo scrollea la lista de atrás:
   * quien cierra el diálogo se encuentra en otro lugar del que estaba, sin
   * haber pedido moverse. Va por clase y no tocando `style`, porque el atributo
   * `style` lo bloquea la CSP de producción.
   *
   * Es un efecto aparte del teclado a propósito: son dos preocupaciones
   * distintas y mezclarlas haría que un cambio en una pueda romper la otra.
   */
  useEffect(() => {
    document.body.classList.add("sin-scroll");
    return () => document.body.classList.remove("sin-scroll");
  }, []);

  useEffect(() => {
    const veniaDe = document.activeElement as HTMLElement | null;
    caja.current?.querySelector<HTMLElement>(ENFOCABLES)?.focus();

    function alTeclado(e: KeyboardEvent) {
      if (e.key === "Escape") {
        if (!bloqueadoRef.current) cerrarRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const lista = Array.from(caja.current?.querySelectorAll<HTMLElement>(ENFOCABLES) ?? []);
      const primero = lista[0];
      const ultimo = lista[lista.length - 1];
      if (primero === undefined || ultimo === undefined) return;
      if (e.shiftKey && document.activeElement === primero) {
        e.preventDefault();
        ultimo.focus();
      } else if (!e.shiftKey && document.activeElement === ultimo) {
        e.preventDefault();
        primero.focus();
      }
    }

    document.addEventListener("keydown", alTeclado);
    return () => {
      document.removeEventListener("keydown", alTeclado);
      veniaDe?.focus();
    };
  }, []);

  return (
    <div className="modal-fondo" role="dialog" aria-modal="true" aria-label={titulo}>
      <div className={`modal modal-${ancho}`} ref={caja}>
        <h2 className="modal-titulo">{titulo}</h2>
        {bajada !== undefined && <p className="modal-hecho">{bajada}</p>}
        {children}
      </div>
    </div>
  );
}
