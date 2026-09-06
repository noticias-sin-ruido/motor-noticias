//! El cliente HTTP contra el motor. Vive en Rust y no en el webview.
//!
//! Con `fetch` desde React, el token tendría que viajar al JavaScript para
//! poder mandarlo en el header. Haciéndolo acá, sale del Credential Manager y
//! va directo al pedido: el front nunca lo ve. De paso no hay CORS que
//! configurar, porque el pedido no sale de un navegador.

use std::time::Duration;

use serde::Serialize;

use crate::secretos;

/// El motor publica en loopback y solo en loopback: su `docker-compose.yml`
/// liga `127.0.0.1:8000` a propósito, para que un despliegue con IP pública no
/// exponga endpoints que gastan plata.
const BASE: &str = "http://127.0.0.1:8000";

/// Corto a propósito. Estos pedidos son a la máquina local, así que si tardan
/// segundos no es lentitud: es que el motor no está levantado, y eso hay que
/// informarlo rápido en vez de dejar la ventana colgada.
const TIMEOUT: Duration = Duration::from_secs(5);

/// Por qué falló un pedido, en categorías cerradas.
///
/// **No es un `String` con el error crudo**, y es la misma decisión que el
/// motor tomó para su campo `agotados` después de que un mensaje de error
/// filtrara el nombre de una variable de entorno: lo que cruza una frontera
/// viaja como categoría, y el detalle se queda de este lado.
#[derive(Debug, Serialize)]
#[serde(tag = "tipo", content = "detalle", rename_all = "snake_case")]
pub enum ErrorDeApi {
    /// Todavía no se configuró el token.
    SinToken,
    /// El motor no contesta: probablemente los contenedores están parados.
    MotorCaido,
    /// El motor contestó, pero rechazó la credencial.
    NoAutorizado,
    /// El motor contestó con un código de error. Se informa el número.
    Respuesta(u16),
    /// Cualquier otra cosa de red.
    Red(String),
}

impl std::fmt::Display for ErrorDeApi {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::SinToken => write!(f, "Falta configurar el token del motor."),
            Self::MotorCaido => write!(
                f,
                "El motor no responde. ¿Están levantados los contenedores?"
            ),
            Self::NoAutorizado => write!(f, "El motor rechazó el token."),
            Self::Respuesta(codigo) => write!(f, "El motor respondió {codigo}."),
            Self::Red(detalle) => write!(f, "Error de red: {detalle}"),
        }
    }
}

/// Un GET al motor, ya autenticado, devuelto como JSON sin interpretar.
///
/// En esta fase alcanza con el JSON crudo: la fase 3 le pone tipos a cada
/// endpoint. Lo que ya está decidido acá es de dónde sale el token y quién
/// hace el pedido.
pub async fn get(ruta: &str) -> Result<serde_json::Value, ErrorDeApi> {
    let token = secretos::leer()
        .map_err(ErrorDeApi::Red)?
        .ok_or(ErrorDeApi::SinToken)?;

    let cliente = reqwest::Client::builder()
        .timeout(TIMEOUT)
        .build()
        .map_err(|e| ErrorDeApi::Red(e.to_string()))?;

    let respuesta = cliente
        .get(format!("{BASE}{ruta}"))
        .bearer_auth(token)
        .send()
        .await
        .map_err(|e| {
            // Una conexión rechazada es el motor apagado, no un problema de red
            // genérico. Distinguirlo es lo que deja mostrar "arrancá el motor"
            // en vez de un mensaje que no dice qué hacer.
            if e.is_connect() || e.is_timeout() {
                ErrorDeApi::MotorCaido
            } else {
                ErrorDeApi::Red(e.to_string())
            }
        })?;

    let estado = respuesta.status();
    if estado == reqwest::StatusCode::UNAUTHORIZED {
        return Err(ErrorDeApi::NoAutorizado);
    }
    // El 503 de `GET /` NO es un error para nosotros: significa que la API está
    // viva y la base todavía no. Es la señal que la fase 2 usa para mostrar
    // "migrando", así que se deja pasar y la decide quien llama.
    if !estado.is_success() && estado != reqwest::StatusCode::SERVICE_UNAVAILABLE {
        return Err(ErrorDeApi::Respuesta(estado.as_u16()));
    }

    respuesta
        .json::<serde_json::Value>()
        .await
        .map_err(|e| ErrorDeApi::Red(e.to_string()))
}
