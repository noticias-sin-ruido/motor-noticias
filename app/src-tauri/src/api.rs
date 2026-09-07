//! El cliente HTTP contra el motor. Vive en Rust y no en el webview.
//!
//! Con `fetch` desde React, el token tendría que viajar al JavaScript para
//! poder mandarlo en el header. Haciéndolo acá, sale del Credential Manager y
//! va directo al pedido: el front nunca lo ve. De paso no hay CORS que
//! configurar, porque el pedido no sale de un navegador.

use std::time::Duration;

use serde::de::DeserializeOwned;
use serde::Serialize;
use ts_rs::TS;

use crate::secretos;

/// El motor publica en loopback y solo en loopback: su `docker-compose.yml`
/// liga `127.0.0.1:8000` a propósito, para que un despliegue con IP pública no
/// exponga endpoints que gastan plata.
const BASE: &str = "http://127.0.0.1:8000";

/// Corto a propósito. Estos pedidos son a la máquina local, así que si tardan
/// segundos no es lentitud: es que el motor no está levantado, y eso hay que
/// informarlo rápido en vez de dejar la ventana colgada.
const TIMEOUT: Duration = Duration::from_secs(5);

/// Sintetizar sí tarda: hay una llamada a un proveedor de IA en el medio, con
/// su propia cadena de fallback. El techo generoso es para no cortar de este
/// lado algo que del otro va a terminar bien — y que además se paga igual.
const TIMEOUT_LARGO: Duration = Duration::from_secs(180);

/// Por qué falló un pedido, en categorías cerradas.
///
/// **No es un `String` con el error crudo**, y es la misma decisión que el
/// motor tomó para su campo `agotados` después de que un mensaje de error
/// filtrara el nombre de una variable de entorno: lo que cruza una frontera
/// viaja como categoría, y el detalle se queda de este lado.
#[derive(Debug, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
#[serde(tag = "tipo", content = "detalle", rename_all = "snake_case")]
pub enum ErrorDeApi {
    /// Todavía no se configuró el token.
    SinToken,
    /// El motor no contesta: probablemente los contenedores están parados.
    MotorCaido,
    /// El motor contestó, pero rechazó la credencial.
    NoAutorizado,
    /// Lo que se pidió no existe: un cluster o una síntesis con ese id.
    NoEncontrado,
    /// El pedido era inválido. Pasa con un cursor mal formado, y también
    /// cuando el proveedor de IA rechaza el contenido o falla — el motor
    /// devuelve 422 en los dos casos, con el mensaje ya saneado.
    Invalida(String),
    /// El motor contestó con otro código de error. Se informa el número.
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
            Self::NoEncontrado => write!(f, "El motor no encontró eso."),
            Self::Invalida(detalle) => write!(f, "El motor rechazó el pedido: {detalle}"),
            Self::Respuesta(codigo) => write!(f, "El motor respondió {codigo}."),
            Self::Red(detalle) => write!(f, "Error de red: {detalle}"),
        }
    }
}

/// Lo que el motor manda en los cuerpos de error. Solo se le saca el
/// `detalle`; si viniera con otra forma se ignora y queda el código pelado.
#[derive(serde::Deserialize)]
struct CuerpoDeError {
    detalle: String,
}

/// Manda el pedido ya autenticado y traduce la respuesta al tipo que se pida.
///
/// **Los parámetros van por `query` y no concatenados a mano**: el cursor de
/// `/sintesis` viene en base64, que trae `+`, `/` y `=`. Pegado crudo a la URL
/// se rompe, y el motor devolvería un 422 por culpa nuestra.
async fn enviar<T: DeserializeOwned>(
    metodo: reqwest::Method,
    ruta: &str,
    parametros: &[(&str, String)],
    timeout: Duration,
) -> Result<T, ErrorDeApi> {
    let token = secretos::leer()
        .map_err(ErrorDeApi::Red)?
        .ok_or(ErrorDeApi::SinToken)?;

    let cliente = reqwest::Client::builder()
        .timeout(timeout)
        .build()
        .map_err(|e| ErrorDeApi::Red(e.to_string()))?;

    let respuesta = cliente
        .request(metodo, format!("{BASE}{ruta}"))
        .query(parametros)
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
    // El 503 de `GET /` NO es un error para nosotros: significa que la API está
    // viva y la base todavía no. Es la señal que la fase 2 usa para mostrar
    // "migrando", así que se deja pasar y la decide quien llama.
    if !estado.is_success() && estado != reqwest::StatusCode::SERVICE_UNAVAILABLE {
        return Err(traducir_error(estado, respuesta).await);
    }

    respuesta
        .json::<T>()
        .await
        .map_err(|e| ErrorDeApi::Red(e.to_string()))
}

/// Convierte un código de error en una categoría.
///
/// **404 y 422 se separan del resto** porque piden reacciones distintas: un id
/// que no existe se arregla refrescando la lista, un cursor inválido se arregla
/// volviendo a la primera página, y un 500 no se arregla solo. Antes los tres
/// caían en `Respuesta(u16)` y la ventana solo podía mostrar un número.
async fn traducir_error(estado: reqwest::StatusCode, respuesta: reqwest::Response) -> ErrorDeApi {
    match estado {
        reqwest::StatusCode::UNAUTHORIZED => ErrorDeApi::NoAutorizado,
        reqwest::StatusCode::NOT_FOUND => ErrorDeApi::NoEncontrado,
        reqwest::StatusCode::UNPROCESSABLE_ENTITY => {
            // El `detalle` del motor ya viene saneado —es lo que se muestra en
            // la ventana— pero si el cuerpo no tiene la forma esperada no se
            // inventa nada.
            let detalle = respuesta
                .json::<CuerpoDeError>()
                .await
                .map(|c| c.detalle)
                .unwrap_or_else(|_| "el motor no explicó por qué".into());
            ErrorDeApi::Invalida(detalle)
        }
        otro => ErrorDeApi::Respuesta(otro.as_u16()),
    }
}

pub async fn get<T: DeserializeOwned>(
    ruta: &str,
    parametros: &[(&str, String)],
) -> Result<T, ErrorDeApi> {
    enviar(reqwest::Method::GET, ruta, parametros, TIMEOUT).await
}

/// Un POST al motor. Sin cuerpo: los endpoints que la app usa toman todo por
/// query string.
pub async fn post<T: DeserializeOwned>(
    ruta: &str,
    parametros: &[(&str, String)],
) -> Result<T, ErrorDeApi> {
    enviar(reqwest::Method::POST, ruta, parametros, TIMEOUT_LARGO).await
}

#[cfg(test)]
mod pruebas {
    use super::*;

    #[test]
    fn las_categorias_viajan_con_su_etiqueta() {
        // El front discrimina por `tipo`, así que los nombres son contrato.
        let json = serde_json::to_string(&ErrorDeApi::NoEncontrado).unwrap();
        assert_eq!(json, r#"{"tipo":"no_encontrado"}"#);

        let json = serde_json::to_string(&ErrorDeApi::Invalida("cursor feo".into())).unwrap();
        assert_eq!(json, r#"{"tipo":"invalida","detalle":"cursor feo"}"#);
    }

    #[test]
    fn el_cuerpo_de_error_del_motor_se_lee() {
        let crudo = r#"{"status":"error","detalle":"El cursor no es válido."}"#;
        let c: CuerpoDeError = serde_json::from_str(crudo).unwrap();
        assert_eq!(c.detalle, "El cursor no es válido.");
    }

    #[test]
    fn un_cuerpo_de_error_con_otra_forma_no_hace_entrar_en_panico() {
        // El motor podría devolver un 422 de FastAPI (que usa `detail`, no
        // `detalle`). No es motivo para reventar: se pierde el texto, nada más.
        let ajeno = r#"{"detail":[{"loc":["query","cursor"],"msg":"muy largo"}]}"#;
        assert!(serde_json::from_str::<CuerpoDeError>(ajeno).is_err());
    }

    // --- Contra el motor real ---------------------------------------------
    //
    // Marcadas `#[ignore]` porque necesitan los contenedores arriba y el token
    // guardado en el Credential Manager. Se corren a mano:
    //
    //     cargo test -- --ignored --nocapture
    //
    // Son las únicas que prueban la cadena entera. Los tests de `tipos.rs`
    // deserializan fixtures: no tocan el token, ni el armado de la query, ni
    // la red. **Solo hacen GET**: ningún test manda un POST que pueda terminar
    // en una llamada paga a un proveedor.

    use crate::tipos::{RespuestaPipeline, RespuestaSintesis, Salud};

    #[tokio::test]
    #[ignore = "necesita el motor arriba y el token guardado"]
    async fn el_get_llega_y_deserializa() {
        let salud: Salud = get("/", &[]).await.expect("GET / contra el motor real");
        assert_eq!(salud.status, "ok");

        let p: RespuestaPipeline = get("/pipeline", &[("historial", "3".into())])
            .await
            .unwrap();
        assert!(p.intervalo_minutos > 0);
    }

    /// **La prueba que justifica el `query()` en vez de concatenar.**
    ///
    /// El cursor viene en base64, que trae `+`, `/` y `=`. Si no se codifica
    /// bien, el motor devuelve un 422 y la segunda página nunca llega. Acá se
    /// pide una página, se toma su `siguiente` y se pide la que sigue.
    #[tokio::test]
    #[ignore = "necesita el motor arriba y el token guardado"]
    async fn el_cursor_sobrevive_al_viaje() {
        let primera: RespuestaSintesis = get("/sintesis", &[("limite", "2".into())]).await.unwrap();
        let cursor = primera
            .siguiente
            .expect("tiene que haber mas de una pagina");

        let segunda: RespuestaSintesis =
            get("/sintesis", &[("limite", "2".into()), ("cursor", cursor)])
                .await
                .expect("la segunda pagina, con el cursor tal cual vino");

        // Si el cursor se hubiera roto, el motor habría devuelto la primera
        // página otra vez o un 422. Ninguna síntesis puede repetirse.
        let ids_primera: Vec<_> = primera.sintesis.iter().map(|s| s.id).collect();
        for s in &segunda.sintesis {
            assert!(!ids_primera.contains(&s.id), "la pagina 2 repite la 1");
        }
    }

    #[tokio::test]
    #[ignore = "necesita el motor arriba y el token guardado"]
    async fn un_id_que_no_existe_es_no_encontrado() {
        let r: Result<serde_json::Value, _> = get("/sintesis/999999", &[]).await;
        assert!(matches!(r, Err(ErrorDeApi::NoEncontrado)), "{r:?}");
    }

    #[tokio::test]
    #[ignore = "necesita el motor arriba y el token guardado"]
    async fn un_cursor_roto_es_invalida_y_no_un_numero() {
        let r: Result<serde_json::Value, _> =
            get("/sintesis", &[("cursor", "esto-no-es-un-cursor".into())]).await;
        match r {
            Err(ErrorDeApi::Invalida(detalle)) => assert!(!detalle.is_empty()),
            otro => panic!("se esperaba Invalida, vino {otro:?}"),
        }
    }
}
