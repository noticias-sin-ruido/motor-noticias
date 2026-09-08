//! La cabina del motor de Sin Ruido.
//!
//! El shell es deliberadamente chico: la app **maneja** el motor, no lo
//! empaqueta. Empaquetarlo habría significado ~2 GB —el entorno de Python pesa
//! 1,8 GB— y además sacar pgvector, que es lo único del sistema sin plan B
//! escrito. Ver el punto 14 de `specs/roadmap.md`.

mod ajustes;
mod api;
mod bandeja;
mod docker;
mod motor;
mod secretos;
mod tipos;

use std::path::PathBuf;

use tauri::{AppHandle, Emitter};

use ajustes::Ajustes;
use api::ErrorDeApi;
use docker::ErrorDocker;
use motor::Estado;
use tipos::{
    Id, RespuestaClusters, RespuestaDetalle, RespuestaModelos, RespuestaPipeline,
    RespuestaSintesis, RespuestaSintetizar, Salud, Sintetizado,
};

/// El canal por el que la interfaz se entera de en qué anda el motor. Se emite
/// en cada transición, no al final: arrancar puede tardar minutos y una ventana
/// que no dice nada mientras tanto parece colgada.
const CANAL_ESTADO: &str = "motor-estado";

// --- El token -------------------------------------------------------------

/// Si ya hay un token guardado. **No devuelve el token**: el front no lo
/// necesita para nada, porque los pedidos los hace Rust.
#[tauri::command]
fn token_existe() -> Result<bool, String> {
    secretos::leer().map(|t| t.is_some())
}

/// Guarda el token. Es lo unico que lo recibe desde la ventana, y no lo
/// devuelve nadie: de aca en adelante vive solo en el Credential Manager.
#[tauri::command]
fn token_guardar(token: String) -> Result<(), String> {
    secretos::guardar(&token)
}

/// Olvida el token. La app vuelve a pedirlo en el proximo arranque.
#[tauri::command]
fn token_borrar() -> Result<(), String> {
    secretos::borrar()
}

// --- Dónde está el repo ---------------------------------------------------

/// La ruta del repo guardada, o `None` si todavía no se configuró.
#[tauri::command]
fn repo_leer(app: AppHandle) -> Result<Option<PathBuf>, String> {
    Ok(ajustes::leer(&app)?.ruta_del_repo)
}

/// Guarda la ruta del repo, **después de comprobar que lo es**.
///
/// El error sale acá, cuando alguien elige la carpeta y puede corregirlo, y no
/// dos pantallas después cuando el arranque falle sin decir por qué.
#[tauri::command]
fn repo_guardar(app: AppHandle, ruta: PathBuf) -> Result<(), String> {
    if !ajustes::tiene_compose(&ruta) {
        return Err(format!(
            "En «{}» no hay un `docker-compose.yml`. Elegí la carpeta del repo del motor.",
            ruta.display()
        ));
    }
    ajustes::guardar(
        &app,
        &Ajustes {
            ruta_del_repo: Some(ruta),
        },
    )
}

// --- El motor -------------------------------------------------------------

fn ruta_configurada(app: &AppHandle) -> Result<PathBuf, ErrorDocker> {
    ajustes::leer(app)
        .map_err(ErrorDocker::Fallo)?
        .ruta_del_repo
        .ok_or_else(|| {
            ErrorDocker::Fallo("Todavía no se configuró dónde está el repo del motor.".into())
        })
}

fn avisar(app: &AppHandle, estado: Estado) {
    // Si el canal falla no hay nada que hacer —la ventana puede haberse
    // cerrado— y no es motivo para abortar el arranque.
    let _ = app.emit(CANAL_ESTADO, estado);
}

/// Un sondeo suelto, para saber en qué anda sin tocar nada.
#[tauri::command]
async fn motor_estado() -> Estado {
    // **En reposo**, no durante un arranque: acá una conexión rechazada
    // significa que el motor está apagado, no que está por levantar.
    motor::estado_en_reposo(&motor::sondear().await)
}

/// Levanta el motor y **avisa cada transición** hasta que esté listo.
#[tauri::command]
async fn motor_arrancar(app: AppHandle) -> Result<Estado, ErrorDocker> {
    let ruta = ruta_configurada(&app)?;

    avisar(&app, Estado::Reconstruyendo);
    let ruta_para_docker = ruta.clone();
    tauri::async_runtime::spawn_blocking(move || docker::arrancar(&ruta_para_docker))
        .await
        .map_err(|e| ErrorDocker::Fallo(format!("No se pudo lanzar docker: {e}")))?
        .inspect_err(|_| avisar(&app, Estado::Error))?;

    // Los contenedores ya arrancaron; falta que la API acepte conexiones y que
    // termine el `alembic upgrade head`. Eso se sondea, no se supone.
    let mut ultimo = Estado::Arrancando;
    avisar(&app, ultimo);

    for _ in 0..motor::intentos_maximos() {
        let sondeo = motor::sondear().await;
        let estado = motor::estado_al_arrancar(&sondeo);
        if estado != ultimo {
            ultimo = estado;
            avisar(&app, estado);
        }
        if estado == Estado::Listo {
            return Ok(estado);
        }
        // **Un fallo se devuelve como `Err` y no como `Ok(Estado::Error)`.**
        // La ventana solo entra a su `catch` con un `Err`: devolviendo `Ok` con
        // el estado en error, el semáforo quedaba rojo y el cuadro de aviso
        // vacío. El camino del plazo vencido, abajo, sí explicaba qué pasó —
        // dos finales igual de malos contaban cosas distintas.
        if let motor::Sondeo::Otra(codigo) = sondeo {
            avisar(&app, Estado::Error);
            return Err(ErrorDocker::Fallo(format!(
                "El motor contestó {codigo} mientras arrancaba. Mirá los logs del \
                 contenedor: `docker compose logs app`."
            )));
        }
        tokio::time::sleep(motor::intervalo()).await;
    }

    avisar(&app, Estado::Error);
    Err(ErrorDocker::Fallo(
        "El motor no terminó de arrancar dentro del plazo.".into(),
    ))
}

/// Para el motor. Los contenedores quedan parados hasta que se los vuelva a
/// levantar: `stop` persiste frente a `restart: unless-stopped`.
#[tauri::command]
async fn motor_detener(app: AppHandle) -> Result<Estado, ErrorDocker> {
    let ruta = ruta_configurada(&app)?;

    tauri::async_runtime::spawn_blocking(move || docker::detener(&ruta))
        .await
        .map_err(|e| ErrorDocker::Fallo(format!("No se pudo lanzar docker: {e}")))??;

    avisar(&app, Estado::Parado);
    Ok(Estado::Parado)
}

// --- Lo que el motor sabe -------------------------------------------------
//
// Un comando por endpoint, cada uno devolviendo su tipo. La ventana no arma
// URLs ni interpreta códigos HTTP: pide `listar_sintesis` y recibe o los datos
// o una categoría de error.

/// El `GET /` del motor, ya autenticado.
#[tauri::command]
async fn motor_salud() -> Result<Salud, ErrorDeApi> {
    api::get("/", &[]).await
}

#[tauri::command]
async fn listar_clusters(
    estado: Option<String>,
    limite: Option<u32>,
) -> Result<RespuestaClusters, ErrorDeApi> {
    let mut parametros = vec![("limite", limite.unwrap_or(20).to_string())];
    if let Some(estado) = estado {
        parametros.push(("estado", estado));
    }
    api::get("/clusters", &parametros).await
}

/// Una página de síntesis.
///
/// `cursor` es opaco: sale del campo `siguiente` de la respuesta anterior y se
/// manda tal cual. No se interpreta de este lado — es base64 y el motor es el
/// único que sabe leerlo.
#[tauri::command]
async fn listar_sintesis(
    limite: Option<u32>,
    cursor: Option<String>,
    cluster_id: Option<Id>,
    entregado: Option<bool>,
) -> Result<RespuestaSintesis, ErrorDeApi> {
    let mut parametros = vec![("limite", limite.unwrap_or(20).to_string())];
    if let Some(cursor) = cursor {
        parametros.push(("cursor", cursor));
    }
    if let Some(cluster_id) = cluster_id {
        parametros.push(("cluster_id", cluster_id.to_string()));
    }
    if let Some(entregado) = entregado {
        parametros.push(("entregado", entregado.to_string()));
    }
    api::get("/sintesis", &parametros).await
}

#[tauri::command]
async fn detalle_de_sintesis(id: Id) -> Result<RespuestaDetalle, ErrorDeApi> {
    api::get(&format!("/sintesis/{id}"), &[]).await
}

#[tauri::command]
async fn estado_del_pipeline(historial: Option<u32>) -> Result<RespuestaPipeline, ErrorDeApi> {
    api::get(
        "/pipeline",
        &[("historial", historial.unwrap_or(5).to_string())],
    )
    .await
}

#[tauri::command]
async fn listar_modelos() -> Result<RespuestaModelos, ErrorDeApi> {
    api::get("/modelos", &[]).await
}

/// Sintetiza un cluster puntual.
///
/// **`forzar` no tiene default acá y va explícito**: del lado del motor esa
/// bandera es lo único que separa "volver a sintetizar" de gastar una llamada
/// paga sin que nadie lo haya decidido. Un default en el medio del camino la
/// volvería invisible.
#[tauri::command]
async fn sintetizar_cluster(
    cluster_id: Id,
    modelo_id: Option<Id>,
    forzar: bool,
) -> Result<Sintetizado, ErrorDeApi> {
    let mut parametros = vec![("forzar", forzar.to_string())];
    if let Some(modelo_id) = modelo_id {
        parametros.push(("modelo_id", modelo_id.to_string()));
    }
    let cruda: RespuestaSintetizar =
        api::post(&format!("/clusters/{cluster_id}/synthesize"), &parametros).await?;
    Ok(cruda.into())
}

/// Cierra la aplicación. **No para el motor**: quien la llama ya decidió qué
/// hacer con los contenedores, y mezclar las dos cosas acá volvería a hacer que
/// el destino del motor dependa de por dónde se salió.
#[tauri::command]
fn salir(app: AppHandle) {
    app.exit(0);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            bandeja::construir(app.handle())?;
            Ok(())
        })
        // **La cruz no cierra: pregunta.** Un programa que maneja contenedores
        // tiene dos cierres legítimos, y cuál ocurre no puede ser un efecto de
        // dónde se hizo clic. El `prevent_close` deja la ventana viva y le pasa
        // la decisión a la pantalla, que es la única que puede mostrar en qué
        // anda mientras el motor se detiene.
        .on_window_event(|ventana, evento| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = evento {
                api.prevent_close();
                let _ = ventana.emit(bandeja::CANAL_SALIDA, bandeja::pedido::PREGUNTAR);
            }
        })
        .invoke_handler(tauri::generate_handler![
            token_existe,
            token_guardar,
            token_borrar,
            repo_leer,
            repo_guardar,
            motor_estado,
            motor_arrancar,
            motor_detener,
            motor_salud,
            listar_clusters,
            listar_sintesis,
            detalle_de_sintesis,
            estado_del_pipeline,
            listar_modelos,
            sintetizar_cluster,
            salir
        ])
        .run(tauri::generate_context!())
        .expect("error al arrancar la cabina");
}

#[cfg(test)]
mod guardas {
    use std::fs;
    use std::path::Path;

    /// Los únicos archivos del front que pueden llamar a `invoke` directo.
    ///
    /// `datos.ts` y `motor.ts` son los envoltorios; `App.tsx` conserva las dos
    /// llamadas del token y la ruta del repo; `Andamio.tsx` es el instrumento
    /// de desarrollo, que llama crudo a propósito —incluso con nombres mal
    /// escritos— porque de eso se trata.
    const PERMITIDOS: [&str; 4] = ["datos.ts", "motor.ts", "App.tsx", "Andamio.tsx"];

    fn recorrer(dir: &Path, encontrados: &mut Vec<String>) {
        let Ok(entradas) = fs::read_dir(dir) else {
            return;
        };
        for entrada in entradas.flatten() {
            let ruta = entrada.path();
            if ruta.is_dir() {
                // `bindings/` lo genera ts-rs y no contiene llamadas.
                if ruta.file_name().is_some_and(|n| n == "bindings") {
                    continue;
                }
                recorrer(&ruta, encontrados);
                continue;
            }
            let Some(nombre) = ruta.file_name().and_then(|n| n.to_str()) else {
                continue;
            };
            if !(nombre.ends_with(".ts") || nombre.ends_with(".tsx")) {
                continue;
            }
            if PERMITIDOS.contains(&nombre) {
                continue;
            }
            if fs::read_to_string(&ruta)
                .is_ok_and(|t| t.contains("invoke<") || t.contains("invoke("))
            {
                encontrados.push(nombre.to_string());
            }
        }
    }

    /// **Las llamadas al puente viven en un solo lugar, y esto lo hace cumplir.**
    ///
    /// El motivo no es orden: es que Tauri convierte los nombres de los
    /// argumentos a camelCase, así que escribir `cluster_id` compila, pasa
    /// `tsc` y falla en ejecución sin decir por qué. Medido el 07/09/2026: la
    /// misma consulta devolvió 1 fila con `clusterId` y 100 con `cluster_id`.
    ///
    /// Concentradas, hay una línea por comando donde equivocarse. Dispersas,
    /// una por cada componente que llame — y ningún compilador mira ninguna.
    #[test]
    fn el_puente_no_se_llama_desde_cualquier_lado() {
        let front = Path::new(env!("CARGO_MANIFEST_DIR")).join("../src");
        assert!(front.is_dir(), "no se encontro el front en {front:?}");

        let mut sueltas = Vec::new();
        recorrer(&front, &mut sueltas);

        assert!(
            sueltas.is_empty(),
            "estos archivos llaman a `invoke` fuera de los envoltorios: {sueltas:?}.
             Agregá la llamada a `datos.ts` en vez de escribirla ahí: es donde              estan todos los nombres de argumento, y el unico lugar donde se              revisa que vayan en camelCase."
        );
    }
}
