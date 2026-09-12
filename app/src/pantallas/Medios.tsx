/**
 * El roster de medios: qué feeds lee el motor, y quién los decide.
 *
 * Cierra el hueco que el bloque F encontró: `POST`/`PATCH /medios` existían en
 * el motor desde el punto 3 y **no los consumía nadie**, así que sumar un medio
 * seguía siendo editar la base a mano.
 *
 * Tres cosas de esta pantalla no son adorno:
 *
 * - **El sondeo se muestra.** El alta del motor no es un CRUD: lee los feeds
 *   antes de guardar y devuelve qué encontró. Esconder eso convertiría una
 *   decisión ("esto es lo que hay, ¿lo querés?") en un formulario.
 * - **La advertencia de términos va antes de guardar.** Después no serviría.
 * - **Cambiar de host se repregunta.** Ver `ConfirmarDominio`.
 */
import { useCallback, useEffect, useState } from "react";

import * as datos from "../datos";
import Modal from "../componentes/Modal";
import type { MedioPublico } from "../bindings/MedioPublico";
import type { FilaDelPanel } from "../bindings/FilaDelPanel";
import type { RespuestaPanel } from "../bindings/RespuestaPanel";
import type { Sondeo } from "../bindings/Sondeo";

/** Un feed por línea, sin vacíos. Es como se escriben, no como viajan. */
function aLista(texto: string): string[] {
  return texto
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}

/**
 * Lo que el motor encontró del otro lado, antes de guardar.
 *
 * Los avisos **no** impiden guardar y se muestran distinto de un error, porque
 * son criterio del operador: que el medio no publique el cuerpo o que su
 * `robots.txt` sea restrictivo son cosas que se deciden, no fallas.
 */
function InformeDeSondeo({
  sondeo,
  avisos,
}: {
  sondeo: Sondeo;
  avisos: string[];
}) {
  return (
    <div className="sondeo">
      <p className="ayuda">
        {sondeo.items_totales} items · {sondeo.items_con_cuerpo} con el cuerpo
        en el feed
      </p>
      <ul className="lista-feeds">
        {sondeo.feeds.map((f) => (
          <li key={f.url}>
            <code>{f.url}</code>
            <span className="modelo-detalle">
              {f.items} items · {f.con_cuerpo} con cuerpo
              {f.ventana_horas !== null &&
                ` · ${Math.round(f.ventana_horas)} h de ventana`}
            </span>
          </li>
        ))}
      </ul>
      {/* **Tres estados y no dos.** `permite_extraer` es nulo cuando el
          robots.txt no se pudo leer: ahí no se sabe, y decir "no permite"
          sería afirmar algo que nadie comprobó. `detalle` sólo viene en ese
          caso — pegarlo siempre imprimía "…de las páginas. null". */}
      <p className="ayuda">
        {!sondeo.robots.legible
          ? `No se pudo leer el robots.txt del medio${sondeo.robots.detalle ? `: ${sondeo.robots.detalle}` : "."}`
          : sondeo.robots.permite_extraer
            ? "El robots.txt permite ir a buscar el cuerpo a la página."
            : "El robots.txt no permite ir a buscar el cuerpo a la página."}
      </p>
      {avisos.length > 0 && (
        <ul className="avisos">
          {avisos.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * La advertencia que va **antes** de dar de alta un medio.
 *
 * No pide declarar que se leyeron los términos del medio, y es deliberado: a
 * nadie se lo puede obligar a leerlos, así que guardar una constancia de que
 * los leyó sería fabricar un consentimiento que no ocurrió. Lo que sí se puede
 * hacer es decir cuál es el riesgo, antes de que se tome la decisión.
 *
 * Misma forma que el modal previo a guardar el destino de entrega.
 */
function AvisoDeTerminos({
  medio,
  alAceptar,
  alCerrar,
}: {
  medio: string;
  alAceptar: () => void;
  alCerrar: () => void;
}) {
  return (
    <Modal
      titulo="Antes de sumar este medio"
      bajada={medio}
      alCerrar={alCerrar}
    >
      <p>
        El motor va a leer el canal RSS de este medio cada quince minutos y a
        guardar lo que traiga.
      </p>
      <p>
        <b>Un RSS abierto no es un permiso ilimitado.</b> Muchos medios publican
        condiciones de uso que permiten la lectura personal y restringen la
        reproducción, la redistribución o el uso comercial de sus contenidos.
        Esas condiciones las pone cada medio y cambian entre uno y otro.
      </p>
      <p className="ayuda">
        Sin Ruido no puede comprobar eso por vos. Si lo que producís con este
        motor va a salir de tu uso personal, revisá las condiciones del medio
        antes de sumarlo.
      </p>
      <div className="acciones">
        <button type="button" onClick={alCerrar} className="secundario">
          Cancelar
        </button>
        <button type="button" onClick={alAceptar}>
          Entendido, sumar el medio
        </button>
      </div>
    </Modal>
  );
}

/**
 * La repregunta cuando lo que se guarda apunta a un host que el medio no tenía.
 *
 * **Lo que esta guarda evita no es técnico sino de atribución**: si a un medio
 * con historia se le cambia el feed por el de otra redacción, las síntesis salen
 * **firmadas** diciendo que ese medio publicó algo que publicó otro, y el
 * back-end las recibe como legítimas. Es la misma familia que el destino de
 * entrega — redirigir el producto, no filtrar una credencial.
 *
 * Por eso nombra los hosts en vez de preguntar "¿estás seguro?": lo único que
 * hace útil a una confirmación es que diga qué se está confirmando.
 */
function ConfirmarDominio({
  medio,
  hosts,
  alConfirmar,
  alCerrar,
}: {
  medio: string;
  hosts: string[];
  alConfirmar: () => void;
  alCerrar: () => void;
}) {
  return (
    <Modal
      titulo="Eso apunta a otro dominio"
      bajada={medio}
      alCerrar={alCerrar}
    >
      <p>
        Lo que estás por guardar hace que <b>{medio}</b> lea de:
      </p>
      <ul className="lista-feeds">
        {hosts.map((h) => (
          <li key={h}>
            <code>{h}</code>
          </li>
        ))}
      </ul>
      <p>
        Si el medio mudó su RSS a ese dominio, está bien. Si es{" "}
        <b>otra redacción</b>, no: las síntesis saldrían diciendo que las
        publicó {medio}.
      </p>
      <p className="ayuda">
        Si es otro medio, va como alta nueva y no como cambio.
      </p>
      <div className="acciones">
        <button type="button" onClick={alCerrar} className="secundario">
          Cancelar
        </button>
        <button type="button" onClick={alConfirmar}>
          Es el mismo medio, guardar
        </button>
      </div>
    </Modal>
  );
}

/**
 * La casilla que deja que el motor vaya a la página a buscar el cuerpo.
 *
 * **Es una decisión del operador y no del motor, y por eso es una casilla y no
 * algo que el sondeo prenda solo.** El feed es lo que el medio eligió publicar;
 * ir a la página a buscar lo que dejó afuera es otra cosa, y quien acepta los
 * términos del medio es quien la usa, no nosotros.
 *
 * Sin esto en la interfaz, un medio como Clarín se da de alta, queda activo,
 * pide el feed cada quince minutos y **guarda cero** sin que nada lo diga.
 */
function CasillaExtraer({
  valor,
  alCambiar,
}: {
  valor: boolean;
  alCambiar: (v: boolean) => void;
}) {
  return (
    <label className={valor ? "casilla casilla-encendida" : "casilla"}>
      <input
        type="checkbox"
        checked={valor}
        onChange={(e) => alCambiar(e.target.checked)}
      />
      <span>
        <b>Ir a buscar el cuerpo a la página del medio.</b> Hace falta cuando el
        feed trae sólo el titular y la bajada: sin esto, el motor descarta esas
        notas y el medio no aporta nada. Actívalo si el sondeo te avisa que
        ningún item trae el cuerpo — y revisá antes las condiciones de uso del
        medio, porque estarías leyendo lo que eligió no publicar en su feed.
      </span>
    </label>
  );
}

/** El formulario de alta, con su advertencia previa. */
function Alta({ alAlta }: { alAlta: () => void }) {
  const [nombre, setNombre] = useState("");
  const [urlBase, setUrlBase] = useState("");
  const [feeds, setFeeds] = useState("");
  const [extraerPorUrl, setExtraerPorUrl] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [advirtiendo, setAdvirtiendo] = useState(false);
  const [sondeo, setSondeo] = useState<{
    sondeo: Sondeo;
    avisos: string[];
  } | null>(null);

  const completo = nombre.trim() && urlBase.trim() && aLista(feeds).length > 0;

  async function guardar() {
    setAdvirtiendo(false);
    setEnviando(true);
    setError(null);
    setSondeo(null);
    try {
      const r = await datos.altaMedio({
        nombre: nombre.trim(),
        urlBase: urlBase.trim(),
        feedsRss: aLista(feeds),
        extraerPorUrl,
      });
      if (r.sondeo) setSondeo({ sondeo: r.sondeo, avisos: r.avisos });
      setNombre("");
      setUrlBase("");
      setFeeds("");
      setExtraerPorUrl(false);
      alAlta();
    } catch (e) {
      setError(datos.mensajeDeRechazo(e));
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form
      className="tarjeta"
      onSubmit={(e) => {
        e.preventDefault();
        // La advertencia va **antes** de guardar. Después no serviría: el motor
        // empieza a leer el feed en la corrida siguiente.
        setAdvirtiendo(true);
      }}
    >
      <h2>Sumar un medio</h2>
      <p className="ayuda">
        El motor lee los feeds antes de guardar nada y te muestra qué encontró.
      </p>

      <label>
        Nombre
        <input
          value={nombre}
          onChange={(e) => setNombre(e.target.value)}
          placeholder="La Nación"
        />
      </label>
      <label>
        Sitio
        <input
          value={urlBase}
          onChange={(e) => setUrlBase(e.target.value)}
          placeholder="https://www.lanacion.com.ar"
        />
      </label>
      <label>
        Feeds RSS, uno por línea
        <textarea
          value={feeds}
          onChange={(e) => setFeeds(e.target.value)}
          rows={3}
          placeholder="https://www.lanacion.com.ar/arc/outboundfeeds/rss/"
        />
      </label>

      <CasillaExtraer valor={extraerPorUrl} alCambiar={setExtraerPorUrl} />

      {error && <div className="aviso">{error}</div>}
      {sondeo && (
        <InformeDeSondeo sondeo={sondeo.sondeo} avisos={sondeo.avisos} />
      )}

      <div className="acciones">
        <button type="submit" disabled={!completo || enviando}>
          {enviando ? "Sondeando los feeds…" : "Sumar medio"}
        </button>
      </div>

      {advirtiendo && (
        <AvisoDeTerminos
          medio={nombre.trim()}
          alAceptar={() => void guardar()}
          alCerrar={() => setAdvirtiendo(false)}
        />
      )}
    </form>
  );
}

/** El formulario de edición de un medio que ya existe. */
function Editar({
  medio,
  alGuardar,
  alCerrar,
}: {
  medio: MedioPublico;
  alGuardar: () => void;
  alCerrar: () => void;
}) {
  const [nombre, setNombre] = useState(medio.nombre);
  const [urlBase, setUrlBase] = useState(medio.url_base);
  const [feeds, setFeeds] = useState(medio.feeds_rss.join("\n"));
  const [extraerPorUrl, setExtraerPorUrl] = useState(medio.extraer_por_url);
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [porConfirmar, setPorConfirmar] = useState<string[] | null>(null);

  async function guardar(confirmando: boolean) {
    setEnviando(true);
    setError(null);
    try {
      await datos.editarMedio({
        medioId: medio.id,
        nombre: nombre.trim(),
        urlBase: urlBase.trim(),
        feedsRss: aLista(feeds),
        extraerPorUrl,
        confirmarDominioNuevo: confirmando,
      });
      setPorConfirmar(null);
      alGuardar();
      alCerrar();
    } catch (e) {
      // El motor no hizo nada y quiere que alguien mire. No es un error que se
      // muestre y se olvide: se repregunta nombrando los hosts y se reintenta.
      const hosts = datos.hostsPorConfirmar(e);
      if (hosts) {
        setPorConfirmar(hosts);
      } else {
        setError(datos.mensajeDeRechazo(e));
      }
    } finally {
      setEnviando(false);
    }
  }

  return (
    <Modal
      titulo="Editar medio"
      bajada={medio.nombre}
      bloqueado={enviando}
      alCerrar={alCerrar}
    >
      <label>
        Nombre
        <input value={nombre} onChange={(e) => setNombre(e.target.value)} />
      </label>
      <label>
        Sitio
        <input value={urlBase} onChange={(e) => setUrlBase(e.target.value)} />
      </label>
      <label>
        Feeds RSS, uno por línea
        <textarea
          value={feeds}
          onChange={(e) => setFeeds(e.target.value)}
          rows={3}
        />
      </label>

      <CasillaExtraer valor={extraerPorUrl} alCambiar={setExtraerPorUrl} />

      {error && <div className="aviso">{error}</div>}
      <p className="ayuda">
        Cambiar las URLs vuelve a sondear los feeds. Cambiar sólo el nombre no
        sale a la red, así que funciona con el medio caído.
      </p>

      <div className="acciones">
        <button
          type="button"
          onClick={alCerrar}
          className="secundario"
          disabled={enviando}
        >
          Cancelar
        </button>
        <button
          type="button"

          onClick={() => void guardar(false)}
          disabled={enviando}
        >
          {enviando ? "Guardando…" : "Guardar"}
        </button>
      </div>

      {porConfirmar && (
        <ConfirmarDominio
          medio={medio.nombre}
          hosts={porConfirmar}
          alConfirmar={() => void guardar(true)}
          alCerrar={() => setPorConfirmar(null)}
        />
      )}
    </Modal>
  );
}

/**
 * El panel de composición: con quién se junta cada medio, y cuándo no.
 *
 * **Se pide aparte de la lista y a pedido.** Del otro lado el motor recorre
 * todas las noticias agrupadas, así que cuesta más que listar; cargarlo siempre
 * haría más lenta una pantalla que la mayoría de las veces se abre para otra
 * cosa.
 *
 * La columna que importa es `solo`: son clusters donde ese medio es el único, y
 * un cluster que no llega al mínimo **no se sintetiza nunca**. Es material que
 * se produce y no se publica, y el tópico dice qué redacción falta para que
 * empiece a publicarse.
 */
/**
 * Cuánto del material de un medio no llega a publicarse.
 *
 * **Es la única pieza con color de la pantalla**, y eso es deliberado: de todo
 * lo que se puede mirar acá, el número que decide algo es éste. Un cluster de un
 * solo medio no alcanza el mínimo y no se sintetiza nunca.
 *
 * La barra muestra la proporción y no la cantidad, que es lo comparable entre
 * medios de tamaños muy distintos: La Nación con 66 sueltos sobre 423 clusters
 * desperdicia más que TN con 30 sobre 475.
 */
function Rendimiento({ fila }: { fila: FilaDelPanel }) {
  if (fila.clusters === 0) {
    return <p className="medio-vacio">Todavía no aportó material.</p>;
  }

  // **Nada y casi-nada tienen que verse distinto.** La barra va en escalones de
  // 5%, así que una proporción menor a 2,5% redondeaba a cero: Revista Gente,
  // con 2 clusters sin publicar sobre 89, se dibujaba idéntica a un medio que
  // no tiene ninguno. El texto de al lado sí lo decía, pero la barra es la
  // pieza que se lee de un vistazo, y estaba mintiendo.
  const pct = (fila.solo / fila.clusters) * 100;
  const temas = fila.topicos_cuando_esta_solo
    .slice(0, 3)
    .map((t) => `${t.topico} ${t.clusters}`)
    .join(" · ");

  return (
    <div className="medio-rendimiento">
      <svg
        className="barra"
        role="img"
        aria-label={`${fila.solo} de ${fila.clusters} clusters sin publicar`}
      >
        <rect className="barra-relleno" width={`${pct}%`} height="100%" />
      </svg>
      <p className="medio-numeros">
        {fila.clusters} clusters
        {fila.solo > 0 && (
          <>
            {" · "}
            <b>{fila.solo} sin publicar</b>
            {temas && <span className="medio-temas"> {temas}</span>}
          </>
        )}
      </p>
    </div>
  );
}

/** Cómo se ordena la lista. */
type Orden = "nombre" | "sin-publicar";

export default function Medios() {
  const [medios, setMedios] = useState<MedioPublico[]>([]);
  const [panel, setPanel] = useState<RespuestaPanel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);
  const [tocando, setTocando] = useState<number | null>(null);
  const [editando, setEditando] = useState<MedioPublico | null>(null);
  const [busqueda, setBusqueda] = useState("");
  const [orden, setOrden] = useState<Orden>("nombre");

  const traer = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      // **Los dos juntos, y medido**: el panel tarda ~37 ms contra 3 ms de la
      // lista. Es diez veces más y sigue siendo imperceptible, así que
      // esconderlo detrás de un botón era esconder el dato por las dudas.
      const [lista, composicion] = await Promise.all([
        datos.listarMedios(),
        datos.panelDeMedios(),
      ]);
      setMedios(lista.medios);
      setPanel(composicion);
    } catch (e) {
      setError(datos.mensajeDeRechazo(e));
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void traer();
  }, [traer]);

  async function cambiar(medio: MedioPublico) {
    setTocando(medio.id);
    setError(null);
    try {
      await datos.activarMedio(medio.id, !medio.activo);
      await traer();
    } catch (e) {
      setError(datos.mensajeDeRechazo(e));
    } finally {
      setTocando(null);
    }
  }

  const porMedio = new Map((panel?.medios ?? []).map((f) => [f.medio_id, f]));

  // Se busca por nombre **y por feed**: cuando algo deja de entrar, lo que se
  // tiene a mano suele ser la URL que apareció en un log, no el nombre.
  const termino = busqueda.trim().toLowerCase();
  const visibles = medios
    .filter(
      (m) =>
        !termino ||
        m.nombre.toLowerCase().includes(termino) ||
        m.url_base.toLowerCase().includes(termino) ||
        m.feeds_rss.some((f) => f.toLowerCase().includes(termino)),
    )
    .sort((a, b) => {
      if (orden === "nombre") return a.nombre.localeCompare(b.nombre, "es");
      return (porMedio.get(b.id)?.solo ?? 0) - (porMedio.get(a.id)?.solo ?? 0);
    });

  const activos = medios.filter((m) => m.activo).length;

  return (
    <div className="ajustes">
      <section className="tarjeta">
        <div className="medios-cabecera">
          <h2>De dónde lee el motor</h2>
          <p className="ayuda">
            {activos} de {medios.length} leyendo
            {panel && (
              <>
                {" · "}
                {panel.clusters.total} clusters, {panel.clusters.solo} con un
                solo medio
              </>
            )}
          </p>
        </div>

        <div className="medios-controles">
          <input
            type="search"
            value={busqueda}
            onChange={(e) => setBusqueda(e.target.value)}
            placeholder="Buscar por nombre o por feed"
            aria-label="Buscar medio"
          />
          <select
            value={orden}
            onChange={(e) => setOrden(e.target.value as Orden)}
            aria-label="Ordenar la lista"
          >
            <option value="nombre">Ordenar por nombre</option>
            <option value="sin-publicar">
              Ordenar por material sin publicar
            </option>
          </select>
          {/* Los números de composición cambian con cada corrida del motor, y
              hasta acá la única forma de releerlos era salir de la pestaña y
              volver. */}
          <button
            type="button"
            className="chico secundario"
            onClick={() => void traer()}
            disabled={cargando}
          >
            {cargando ? "Actualizando…" : "Actualizar"}
          </button>
        </div>

        {error && <div className="aviso">{error}</div>}

        {medios.length === 0 && !cargando && (
          <p className="ayuda">
            El motor no está leyendo ningún medio todavía. Sumá el primero acá
            abajo y va a empezar a ingerir en la próxima corrida.
          </p>
        )}

        {medios.length > 0 && visibles.length === 0 && (
          <p className="ayuda">
            Ningún medio coincide con «{busqueda.trim()}».
          </p>
        )}

        <ul className="lista-medios">
          {visibles.map((m) => {
            const fila = porMedio.get(m.id);
            return (
              <li key={m.id} className={m.activo ? "medio" : "medio apagado"}>
                <div className="medio-titulo">
                  <h3>{m.nombre}</h3>
                  {!m.activo && <span className="medio-estado">No se lee</span>}
                  <div className="acciones">
                    <button
                      type="button"
                      className="chico secundario"
                      onClick={() => setEditando(m)}
                    >
                      Editar
                    </button>
                    <button
                      type="button"
                      className="chico"
                      onClick={() => void cambiar(m)}
                      disabled={tocando === m.id}
                    >
                      {tocando === m.id
                        ? "…"
                        : m.activo
                          ? "Dejar de leer"
                          : "Volver a leer"}
                    </button>
                  </div>
                </div>

                {/* Los feeds se muestran enteros y no contados. Esta pantalla
                    existe para responder de dónde lee el motor, y "1 feed" no
                    lo responde. */}
                <ul className="medio-feeds">
                  {m.feeds_rss.map((f) => (
                    <li key={f}>
                      <code>{f}</code>
                    </li>
                  ))}
                </ul>

                {m.extraer_por_url && (
                  <p className="medio-nota">
                    Va a buscar a la página el cuerpo que el medio no publica en
                    el feed.
                  </p>
                )}

                {fila && <Rendimiento fila={fila} />}
              </li>
            );
          })}
        </ul>

        <p className="ayuda">
          Dejar de leer un medio <b>no borra nada</b>: sus noticias, sus
          clusters y sus síntesis ya entregadas siguen donde están. Se puede
          volver a prender cuando sea.
        </p>
      </section>

      <Alta alAlta={() => void traer()} />

      {editando && (
        <Editar
          medio={editando}
          alGuardar={() => void traer()}
          alCerrar={() => setEditando(null)}
        />
      )}
    </div>
  );
}
