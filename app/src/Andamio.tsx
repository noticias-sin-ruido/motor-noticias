/**
 * El banco de pruebas del puente. **Acompaña todo el desarrollo y se borra en
 * la fase 9, antes de empaquetar** — no antes.
 *
 * Nació en la fase 4 como andamio descartable, para cruzar por primera vez los
 * seis comandos que nadie había invocado. Se quedó porque cubre lo que ninguna
 * pantalla cubre todavía, y porque mide cosas que sólo se pueden medir con la
 * ventana abierta.
 *
 * **Por qué hace falta un banco y no bastan los tests.** Entre Rust y el webview
 * hay una capa que ningún compilador ve: `cargo test` llama las funciones
 * directo, y `tsc` tipa el retorno de `invoke<T>()` con lo que uno le declare —
 * no sabe qué comandos existen ni qué argumentos piden.
 * `invoke("comando_inexistente", { fruta: 3 })` compila perfecto.
 *
 * **Es el único lugar que llama al puente crudo, y a propósito.** El resto de la
 * app pasa por `datos.ts`; acá se llama directo para poder equivocarse a
 * mandato — mandar `cluster_id` en vez de `clusterId` y ver qué pasa. Un test
 * de Rust (`guardas::el_puente_no_se_llama_desde_cualquier_lado`) hace cumplir
 * esa separación.
 *
 * **Qué mantener acá.** Cuando una fase agregue comandos, agregarles su prueba;
 * cuando una pantalla real pase a ejercitar uno, su prueba de acá puede irse.
 * Las pruebas **corren solas al montar** y son todas de lectura: abrir la
 * ventana alcanza. El único POST —el que puede gastar plata— está abajo, detrás
 * de un botón aparte.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import type { RespuestaClusters } from "./bindings/RespuestaClusters";
import type { RespuestaSintesis } from "./bindings/RespuestaSintesis";
import type { RespuestaDetalle } from "./bindings/RespuestaDetalle";
import type { RespuestaPipeline } from "./bindings/RespuestaPipeline";
import type { RespuestaModelos } from "./bindings/RespuestaModelos";
import type { Sintetizado } from "./bindings/Sintetizado";

type Marca = "ok" | "error" | "alerta";

type Prueba = {
  nombre: string;
  llamada: string;
  marca: Marca;
  veredicto: string;
  ms: number;
  crudo: string | null;
};

/**
 * Lo que rechaza un `invoke` no siempre tiene la forma de `ErrorDeApi`: si el
 * puente rechaza antes de entrar a nuestro código —un argumento que no matchea,
 * un comando que no existe— lo que vuelve es un string pelado. Distinguir los
 * dos casos es justamente el punto de este andamio.
 */
function describirRechazo(e: unknown): string {
  if (typeof e === "object" && e !== null && "tipo" in e) {
    const err = e as { tipo: string; detalle?: unknown };
    const cola = err.detalle === undefined ? "" : `: ${String(err.detalle)}`;
    return `ErrorDeApi «${err.tipo}»${cola}`;
  }
  return `el puente rechazó — ${String(e)}`;
}

function recortar(valor: unknown, tope = 700): string {
  const texto = JSON.stringify(valor, null, 2);
  return texto.length > tope ? `${texto.slice(0, tope)}\n… (recortado)` : texto;
}

export default function Andamio() {
  const [pruebas, setPruebas] = useState<Prueba[]>([]);
  const [corriendo, setCorriendo] = useState(false);
  const [abierta, setAbierta] = useState<string | null>(null);
  const [sintetizado, setSintetizado] = useState<Prueba | null>(null);

  // El cluster que usaría la prueba del POST: uno que **ya tiene síntesis**,
  // para que `forzar: false` corte en `sin_material_nuevo` sin llamar al
  // proveedor. Sale de las pruebas de lectura, y va en estado y no en un ref
  // porque de él depende si el botón está habilitado.
  const [clusterConSintesis, setClusterConSintesis] = useState<number | null>(null);

  const correr = useCallback(async () => {
    setCorriendo(true);
    setPruebas([]);
    setSintetizado(null);
    setClusterConSintesis(null);

    const salida: Prueba[] = [];
    const anotar = (p: Prueba) => {
      salida.push(p);
      setPruebas([...salida]);
    };

    // Lo que una prueba le deja a la siguiente. Va en un objeto y no en `let`
    // sueltos para que TypeScript no pierda el tipo entre closures.
    const ctx: { idSintesis: number | null; idCluster: number | null } = {
      idSintesis: null,
      idCluster: null,
    };

    async function probar(
      nombre: string,
      llamada: string,
      fn: () => Promise<{ veredicto: string; crudo?: unknown; marca?: Marca }>,
    ) {
      const t0 = performance.now();
      try {
        const r = await fn();
        anotar({
          nombre,
          llamada,
          marca: r.marca ?? "ok",
          veredicto: r.veredicto,
          ms: Math.round(performance.now() - t0),
          crudo: r.crudo === undefined ? null : recortar(r.crudo),
        });
      } catch (e) {
        anotar({
          nombre,
          llamada,
          marca: "error",
          veredicto: describirRechazo(e),
          ms: Math.round(performance.now() - t0),
          crudo: null,
        });
      }
    }

    // --- Los seis comandos, uno por uno ---------------------------------

    await probar("listar_clusters", "sin argumentos", async () => {
      const r = await invoke<RespuestaClusters>("listar_clusters", {});
      return { veredicto: `${r.cantidad} clusters`, crudo: r.clusters[0] };
    });

    await probar("listar_clusters", '{ estado: "abierto", limite: 5 }', async () => {
      const r = await invoke<RespuestaClusters>("listar_clusters", {
        estado: "abierto",
        limite: 5,
      });
      const todos = r.clusters.every((c) => c.estado === "abierto");
      return {
        veredicto: `${r.cantidad} devueltos, ${todos ? "todos" : "NO todos"} en estado abierto`,
        marca: r.cantidad <= 5 && todos ? "ok" : "error",
      };
    });

    await probar("listar_sintesis", "{ limite: 3 }", async () => {
      const r = await invoke<RespuestaSintesis>("listar_sintesis", { limite: 3 });
      const primera = r.sintesis[0];
      if (primera !== undefined) {
        ctx.idSintesis = primera.id;
        ctx.idCluster = primera.cluster_id;
        setClusterConSintesis(primera.cluster_id);
      }
      const cola =
        r.siguiente === null ? "null (era la última página)" : "hay más páginas";
      return {
        veredicto: `${r.cantidad} síntesis · siguiente = ${cola}`,
        crudo: primera,
      };
    });

    await probar("detalle_de_sintesis", "{ id: <la primera> }", async () => {
      if (ctx.idSintesis === null) throw "no hubo síntesis de la que sacar un id";
      const r = await invoke<RespuestaDetalle>("detalle_de_sintesis", {
        id: ctx.idSintesis,
      });
      const medios = Object.keys(r.sintesis.comparativa_enfoques);
      // Lo que se verifica acá es el `flatten` del struct: si funcionó, los
      // campos del resumen y los del detalle llegan al mismo nivel.
      return {
        veredicto: `id ${r.sintesis.id} · ${r.sintesis.puntos_clave.length} puntos clave · comparativa por medio: ${medios.join(", ") || "vacía"}`,
        crudo: { ...r.sintesis, resumen_neutro: "…", fuentes: "…" },
      };
    });

    await probar("estado_del_pipeline", "{ historial: 3 }", async () => {
      const r = await invoke<RespuestaPipeline>("estado_del_pipeline", {
        historial: 3,
      });
      const ultima = r.ultima === null ? "nunca corrió" : r.ultima.inicio;
      return {
        veredicto: `corriendo=${r.corriendo} · huerfana=${r.huerfana} · última: ${ultima}`,
        crudo: r.ultima,
      };
    });

    await probar("listar_modelos", "sin argumentos", async () => {
      const r = await invoke<RespuestaModelos>("listar_modelos", {});
      // No es decorativo: `GET /modelos` no puede devolver `api_key_env` ni
      // `base_url`, y esta es la primera vez que la respuesta real llega a la
      // ventana. Si algún día se filtran, se ve acá.
      const limpio = r.modelos.every(
        (m) => !("api_key_env" in m) && !("base_url" in m),
      );
      return {
        veredicto: `${r.modelos.length} modelos · en uso «${r.en_uso}» · sin api_key_env ni base_url: ${limpio ? "sí" : "NO — FUGA"}`,
        marca: limpio ? "ok" : "error",
        crudo: r.modelos,
      };
    });

    // --- La trampa: Tauri convierte los argumentos a camelCase -----------

    await probar(
      "TRAMPA · clusterId contra cluster_id",
      "listar_sintesis, la misma consulta con las dos grafías",
      async () => {
        if (ctx.idCluster === null) throw "no hubo un cluster de referencia";
        const camel = await invoke<RespuestaSintesis>("listar_sintesis", {
          limite: 100,
          clusterId: ctx.idCluster,
        });
        const snake = await invoke<RespuestaSintesis>("listar_sintesis", {
          limite: 100,
          cluster_id: ctx.idCluster,
        });
        const filtro = camel.cantidad < snake.cantidad;
        return {
          veredicto: filtro
            ? `confirmado: clusterId filtró (${camel.cantidad}) y cluster_id fue ignorado (${snake.cantidad}). Escrito en snake_case compila, pasa tsc y no filtra nada.`
            : `no concluyente: camel=${camel.cantidad}, snake=${snake.cantidad}. Puede pasar si ese cluster tiene tantas síntesis como el tope.`,
          marca: filtro ? "ok" : "alerta",
        };
      },
    );

    // Esta va escrita a mano y no con `probar` porque acá **el rechazo es el
    // resultado correcto**, y el `catch` genérico lo pintaría de rojo.
    {
      const nombre = "TRAMPA · argumento obligatorio en snake_case";
      const llamada = "sintetizar_cluster con { cluster_id, modelo_id }";
      const t0 = performance.now();
      if (ctx.idCluster === null) {
        anotar({
          nombre,
          llamada,
          marca: "alerta",
          veredicto: "salteada: no hubo un cluster de referencia",
          ms: 0,
          crudo: null,
        });
      } else {
        try {
          // **No gasta plata.** `clusterId` es obligatorio: al no matchear, el
          // puente rechaza mientras deserializa los argumentos, antes de entrar
          // al cuerpo del comando y por lo tanto antes de que salga un pedido.
          await invoke<Sintetizado>("sintetizar_cluster", {
            cluster_id: ctx.idCluster,
            modelo_id: null,
            forzar: false,
          });
          anotar({
            nombre,
            llamada,
            marca: "error",
            veredicto:
              "SOBREVIVE: el puente aceptó snake_case. Si aparece esto, el modelo mental de la conversión está mal y hay que rehacerlo.",
            ms: Math.round(performance.now() - t0),
            crudo: null,
          });
        } catch (e) {
          anotar({
            nombre,
            llamada,
            marca: "ok",
            veredicto: `correcto — ${describirRechazo(e)}. Rechazado antes de salir a la red, así que no costó nada.`,
            ms: Math.round(performance.now() - t0),
            crudo: null,
          });
        }
      }
    }

    // --- La medición que la fase 4 necesita antes de dibujar -------------

    await probar(
      "MEDICIÓN · el Set<cluster_id>",
      "paginar /sintesis hasta agotar el cursor, de a 100",
      async () => {
        const set = new Set<number>();
        let cursor: string | null = null;
        let paginas = 0;
        let filas = 0;
        const t0 = performance.now();
        do {
          // El tipo va anotado: sin esto, `cursor` se infiere de `p.siguiente`
          // y `p` de una llamada que recibe `cursor`. TypeScript corta el
          // circulo con TS7022.
          const p: RespuestaSintesis = await invoke<RespuestaSintesis>("listar_sintesis", {
            limite: 100,
            cursor,
          });
          paginas += 1;
          filas += p.sintesis.length;
          for (const s of p.sintesis) set.add(s.cluster_id);
          cursor = p.siguiente;
        } while (cursor !== null && paginas < 20);
        const ms = Math.round(performance.now() - t0);
        return {
          veredicto: `${paginas} páginas · ${filas} síntesis · ${set.size} clusters distintos · ${ms} ms. Es lo que costaría saber qué cluster ya tiene síntesis, antes de pintar una sola fila.`,
          marca: ms < 400 ? "ok" : "alerta",
        };
      },
    );

    await probar(
      "MEDICIÓN · el campo nuevo",
      "listar_clusters — cantidad_sintesis viene en la misma respuesta",
      async () => {
        const t0 = performance.now();
        const r = await invoke<RespuestaClusters>("listar_clusters", { limite: 20 });
        const ms = Math.round(performance.now() - t0);
        const resueltos = r.clusters.filter((c) => c.cantidad_sintesis > 0).length;
        return {
          veredicto: `${ms} ms en 1 pedido · ${resueltos} de ${r.cantidad} clusters ya tienen síntesis, y el dato llegó junto con la lista. La medición de arriba es lo que costaba lo mismo antes.`,
          crudo: r.clusters.map((c) => ({
            id: c.id,
            estado: c.estado,
            cantidad_sintesis: c.cantidad_sintesis,
          })),
        };
      },
    );

    // --- La CSP: comprobar que de verdad bloquea -------------------------

    await probar(
      "CSP · bloquea lo externo",
      "fetch a un host de afuera, que la politica no permite",
      async () => {
        // **La senal que distingue los dos casos.** Si la CSP esta activa, el
        // motor de render emite `securitypolicyviolation` y el pedido nunca
        // sale. Si NO esta activa, el pedido sale de verdad y falla por red,
        // sin evento. Mirar solo si el fetch fallo no alcanza: falla en los
        // dos casos, y una politica escrita pero no aplicada se ve identica a
        // una que funciona.
        let violacion: SecurityPolicyViolationEvent | null = null;
        const escuchar = (e: SecurityPolicyViolationEvent) => {
          violacion = e;
        };
        document.addEventListener("securitypolicyviolation", escuchar);
        let fallo = "";
        try {
          await fetch("https://example.com/csp-probe");
        } catch (e) {
          fallo = String(e);
        }
        // El evento se despacha en una tarea aparte: hay que darle un turno.
        await new Promise((r) => setTimeout(r, 120));
        document.removeEventListener("securitypolicyviolation", escuchar);

        const v = violacion as SecurityPolicyViolationEvent | null;
        if (v !== null) {
          return {
            veredicto: `bloqueado por la CSP · directiva «${v.violatedDirective}» · destino «${v.blockedURI}». La politica esta activa y aplicandose.`,
          };
        }
        return {
          veredicto: `SIN VIOLACION: el pedido salio a la red y ${fallo || "no fallo"}. La CSP no esta bloqueando nada -- esta escrita pero no aplicada.`,
          marca: "error",
        };
      },
    );

    setCorriendo(false);
  }, []);

  // **La guarda existe por `React.StrictMode`** (ver `main.tsx`), que en
  // desarrollo invoca cada efecto dos veces a propósito para destapar efectos
  // no idempotentes. Acá el efecto es idempotente, pero **no es gratis**: sin
  // la guarda salían dos corridas simultáneas, se veía en los logs del motor
  // como cada GET repetido, y las dos competían por la red -- así que los
  // tiempos que este andamio mide salían inflados. Un banco de medición que
  // corre dos veces se mide a sí mismo.
  //
  // El botón "correr de nuevo" no pasa por acá y sigue funcionando.
  const yaArranco = useRef(false);
  useEffect(() => {
    if (yaArranco.current) return;
    yaArranco.current = true;
    void correr();
  }, [correr]);

  /** El único POST. Se aprieta a mano, nunca solo. */
  async function sintetizar() {
    if (clusterConSintesis === null) return;
    const llamada = `{ clusterId: ${clusterConSintesis}, modeloId: null, forzar: false }`;
    const t0 = performance.now();
    try {
      const r = await invoke<Sintetizado>("sintetizar_cluster", {
        clusterId: clusterConSintesis,
        modeloId: null,
        forzar: false,
      });
      setSintetizado({
        nombre: "sintetizar_cluster",
        llamada,
        marca: r.tipo === "cortada" ? "ok" : "alerta",
        veredicto:
          r.tipo === "cortada"
            ? `cortada · motivo «${r.motivo}» — el enum discriminó bien y no se llamó al proveedor`
            : `hecha · ${r.creados} creados, ${r.actualizados} actualizados, ${r.descartados} descartados. OJO: esto sí llamó al proveedor.`,
        ms: Math.round(performance.now() - t0),
        crudo: recortar(r),
      });
    } catch (e) {
      setSintetizado({
        nombre: "sintetizar_cluster",
        llamada,
        marca: "error",
        veredicto: describirRechazo(e),
        ms: Math.round(performance.now() - t0),
        crudo: null,
      });
    }
  }

  const fallidas = pruebas.filter((p) => p.marca === "error").length;
  const dudosas = pruebas.filter((p) => p.marca === "alerta").length;
  const marcaGlobal: Marca = fallidas > 0 ? "error" : dudosas > 0 ? "alerta" : "ok";

  function Fila({ p, clave }: { p: Prueba; clave: string }) {
    return (
      <>
        <div className="prueba-cab">
          <span className={`estado ${p.marca}`} />
          <code className="prueba-nombre">{p.nombre}</code>
          <span className="prueba-args">{p.llamada}</span>
          <span className="prueba-ms">{p.ms} ms</span>
        </div>
        <p className="prueba-veredicto">{p.veredicto}</p>
        {p.crudo !== null && (
          <>
            <button
              className="secundario chico"
              onClick={() => setAbierta(abierta === clave ? null : clave)}
            >
              {abierta === clave ? "ocultar payload" : "ver payload"}
            </button>
            {abierta === clave && <pre className="crudo">{p.crudo}</pre>}
          </>
        )}
      </>
    );
  }

  return (
    <>
      <div className="tarjeta">
        <p className="ayuda sin-tope">
          Andamio descartable de la fase 4: cruza el puente <code>invoke()</code>{" "}
          con los seis comandos que nunca se llamaron desde la ventana. Todo lo
          que corre solo es <b>de lectura</b>.
        </p>
        <span className={`estado ${marcaGlobal}`}>
          {corriendo
            ? "corriendo…"
            : `${pruebas.length} pruebas · ${fallidas} fallidas · ${dudosas} no concluyentes`}
        </span>
      </div>

      <ul className="pruebas">
        {pruebas.map((p, i) => (
          <li key={`${p.nombre}-${i}`} className="prueba">
            <Fila p={p} clave={`${p.nombre}-${i}`} />
          </li>
        ))}
      </ul>

      <div className="tarjeta peligro">
        <p className="ayuda sin-tope">
          <b>El único POST.</b> Sobre el cluster{" "}
          <code>{clusterConSintesis ?? "—"}</code>, que ya tiene síntesis. Con{" "}
          <code>forzar: false</code> el motor debería contestar{" "}
          <code>sin_material_nuevo</code> <b>sin llamar al proveedor</b>, así que
          no cuesta nada — y de paso prueba que la ventana discrimina bien el
          enum <code>Sintetizado</code>. Si contesta <code>hecha</code>, ese
          cluster tenía material nuevo y la llamada sí se pagó.
        </p>
        <div className="acciones">
          <button
            className="secundario"
            disabled={corriendo || clusterConSintesis === null}
            onClick={() => void sintetizar()}
          >
            Probar sintetizar_cluster (forzar: false)
          </button>
        </div>
        {sintetizado && (
          <div className="prueba suelta">
            <Fila p={sintetizado} clave="post" />
          </div>
        )}
      </div>

      <div className="acciones">
        <button className="secundario" disabled={corriendo} onClick={() => void correr()}>
          Correr de nuevo
        </button>
      </div>
    </>
  );
}
