//! La cabina del motor de Sin Ruido.
//!
//! El shell es deliberadamente chico: la app **maneja** el motor, no lo
//! empaqueta. Empaquetarlo habría significado ~2 GB —el entorno de Python pesa
//! 1,8 GB, con torch adentro— y además sacar pgvector, que es lo único del
//! sistema sin plan B escrito. Ver el punto 14 de `specs/roadmap.md`.

mod api;
mod secretos;

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

/// El `GET /` del motor: salud, base, entorno y hora.
///
/// Devuelve el error como categoría (`ErrorDeApi`) y no como texto suelto, así
/// la interfaz puede decidir qué mostrar —"falta el token" y "el motor está
/// apagado" piden acciones distintas— en vez de pintar un mensaje genérico.
#[tauri::command]
async fn motor_salud() -> Result<serde_json::Value, api::ErrorDeApi> {
    api::get("/").await
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            token_existe,
            token_guardar,
            token_borrar,
            motor_salud
        ])
        .run(tauri::generate_context!())
        .expect("error al arrancar la cabina");
}
