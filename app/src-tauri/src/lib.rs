//! La cabina del motor de Sin Ruido.
//!
//! El shell es deliberadamente chico: la app **maneja** el motor, no lo
//! empaqueta. Empaquetarlo habría significado ~2 GB —el entorno de Python pesa
//! 1,8 GB— y además sacar pgvector, que es lo único del sistema sin plan B
//! escrito. Ver el punto 14 de `specs/roadmap.md`.

mod ajustes;
mod api;
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

#[tauri::command]
fn token_guardar(token: String) -> Result<(), String> {
    secretos::guardar(&token)
}

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
    motor::estado_de(&motor::sondear().await)
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
        let estado = motor::estado_de(&motor::sondear().await);
        if estado != ultimo {
            ultimo = estado;
            avisar(&app, estado);
        }
        if matches!(estado, Estado::Listo | Estado::Error) {
            return Ok(estado);
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
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
            sintetizar_cluster
        ])
        .run(tauri::generate_context!())
        .expect("error al arrancar la cabina");
}
