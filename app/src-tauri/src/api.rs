//! El cliente HTTP contra el motor. Vive en Rust y no en el webview.
//!
//! Con `fetch` desde React, el token tendría que viajar al JavaScript en cada
//! pedido para poder mandarlo en el header. Haciéndolo acá sale del Credential
//! Manager y va directo, sin pasar por la ventana. De paso no hay CORS que
//! configurar, porque el pedido no sale de un navegador.
//!
//! **El front lo ve una sola vez: cuando alguien lo tipea.** Eso es inevitable
//! —el campo está en la ventana— y termina ahí: `token_guardar` lo manda a
//! Rust y ningún comando lo devuelve nunca. Decir "el front nunca lo ve", como
//! decía antes este comentario, prometía de más.

use std::time::Duration;

use serde::de::DeserializeOwned;
use serde::Serialize;
use ts_rs::TS;

use crate::secretos;

/// El motor publica en loopback y solo en loopback: su `docker-compose.yml`
/// liga `127.0.0.1:8000` a propósito, para que un despliegue con IP pública no
/// exponga endpoints que gastan plata.
pub const BASE: &str = "http://127.0.0.1:8000";

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
    /// El motor vive pero no puede atender **este** pedido, y dice por qué.
    ///
    /// Hoy pasa en un solo caso y es el que la motiva: `GET`/`PATCH /entrega`
    /// exigen token siempre, así que contra un despliegue sin `API_TOKEN`
    /// contestan 503 con el texto de qué configurar. Sin esta categoría ese
    /// cuerpo se intentaba deserializar como si fuera una respuesta buena --el
    /// 503 se dejaba pasar-- y la ventana mostraba "error decoding response
    /// body", escondiendo justamente el mensaje que servía.
    NoDisponible(String),
    /// El motor contestó con otro código de error. Se informa el número.
    Respuesta(u16),
    /// Cualquier otra cosa de red.
    Red(String),
}

impl std::fmt::Display for ErrorDeApi {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::SinToken => write!(f, "Falta configurar el token del motor."),
            Self::NoDisponible(detalle) => write!(f, "{detalle}"),
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
///
/// **El motor manda dos formas distintas, y hay que aceptar las dos.** Sus
/// errores propios viajan como `{"detalle": ...}` —los arma él, en español— y
/// los que levanta `HTTPException` de FastAPI viajan como `{"detail": ...}`,
/// que es el nombre que pone la librería. Aceptar sólo uno no rompe nada
/// visible: el parseo falla, se cae al texto de reemplazo, y la ventana muestra
/// "el motor no explicó por qué" **encima de un mensaje que sí explicaba**.
///
/// Pasó con el 503 de `/entrega` contra un motor sin `API_TOKEN`, cuyo cuerpo
/// decía exactamente qué configurar y no llegaba a verse.
#[derive(serde::Deserialize)]
struct CuerpoDeError {
    #[serde(alias = "detail")]
    detalle: String,
}

/// Manda el pedido ya autenticado y traduce la respuesta al tipo que se pida.
///
/// **Los parámetros van por `query` y no concatenados a mano**: el cursor de
/// `/sintesis` viene en base64, que trae `+`, `/` y `=`. Pegado crudo a la URL
/// se rompe, y el motor devolvería un 422 por culpa nuestra.
/// Qué significa un 401 según si mandamos credencial o no.
///
/// **Existe separada para poder probarla**, y reemplaza a un corte local que
/// estaba mal pensado. Antes el cliente exigía token para todo y devolvía
/// `SinToken` *antes de salir a la red*, salvo en `GET /`. Eso codificaba una
/// suposición falsa: que el token siempre hace falta.
///
/// **Quién decide eso es el motor.** `API_TOKEN` es opcional del lado del
/// motor, así que contra un despliegue con la API abierta la app tiene que
/// funcionar sin credencial — y con el corte local no funcionaba ninguna
/// pantalla: todas fallaban sin intentar. Lo encontró la prueba manual de un
/// motor sin token, no la suite.
///
/// Ahora se manda lo que haya y el motor contesta. Un 401 sigue distinguiendo
/// los dos casos, que piden cosas distintas de quien mira: **falta** la
/// credencial (pegala) contra **no sirve** la que hay (cambiala).
fn falta_o_no_sirve(habia_token: bool) -> ErrorDeApi {
    if habia_token {
        ErrorDeApi::NoAutorizado
    } else {
        ErrorDeApi::SinToken
    }
}

async fn enviar<T: DeserializeOwned>(
    metodo: reqwest::Method,
    ruta: &str,
    parametros: &[(&str, String)],
    cuerpo: Option<serde_json::Value>,
    timeout: Duration,
    tolera_503: bool,
) -> Result<T, ErrorDeApi> {
    // Se manda lo que haya. Si el motor no pide credencial, contesta igual; si
    // la pide y no la tenemos, contesta 401 y ahí se distingue. Ver
    // `falta_o_no_sirve`.
    let token = secretos::leer().map_err(ErrorDeApi::Red)?;
    let habia_token = token.is_some();

    let cliente = reqwest::Client::builder()
        .timeout(timeout)
        .build()
        .map_err(|e| ErrorDeApi::Red(e.to_string()))?;

    let mut pedido = cliente
        .request(metodo, format!("{BASE}{ruta}"))
        .query(parametros);
    if let Some(token) = token {
        pedido = pedido.bearer_auth(token);
    }
    if let Some(cuerpo) = cuerpo {
        pedido = pedido.json(&cuerpo);
    }

    let respuesta = pedido.send().await.map_err(|e| {
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
    // **El 503 se tolera sólo donde significa otra cosa, y eso es `GET /`.**
    // Ahí quiere decir "la API está viva y la base todavía no", que es la señal
    // que la fase 2 usa para mostrar "migrando"; el cuerpo llega igual y lo
    // decide quien llama.
    //
    // Antes esta excepción era global, y ese fue el bug: `GET /entrega` contra
    // un motor sin `API_TOKEN` contesta 503 con el texto de qué configurar, se
    // colaba como respuesta buena, y al intentar deserializarlo la ventana
    // mostraba "error decoding response body" en vez del mensaje. Una excepción
    // escrita para una ruta no puede quedar aplicándose a todas.
    if !estado.is_success() && !(tolera_503 && estado == reqwest::StatusCode::SERVICE_UNAVAILABLE) {
        return Err(traducir_error(estado, respuesta, habia_token).await);
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
async fn traducir_error(
    estado: reqwest::StatusCode,
    respuesta: reqwest::Response,
    habia_token: bool,
) -> ErrorDeApi {
    match estado {
        reqwest::StatusCode::UNAUTHORIZED => falta_o_no_sirve(habia_token),
        reqwest::StatusCode::SERVICE_UNAVAILABLE => {
            ErrorDeApi::NoDisponible(detalle_o(respuesta, "el motor no explicó por qué").await)
        }
        reqwest::StatusCode::NOT_FOUND => ErrorDeApi::NoEncontrado,
        reqwest::StatusCode::UNPROCESSABLE_ENTITY => {
            // El `detalle` del motor ya viene saneado —es lo que se muestra en
            // la ventana— pero si el cuerpo no tiene la forma esperada no se
            // inventa nada.
            ErrorDeApi::Invalida(detalle_o(respuesta, "el motor no explicó por qué").await)
        }
        otro => ErrorDeApi::Respuesta(otro.as_u16()),
    }
}

/// El `detail` que manda el motor, o el reemplazo si el cuerpo no tiene esa
/// forma. **No se inventa nada**: el texto del motor ya viene saneado y es lo
/// que se muestra, pero si no está, se dice.
async fn detalle_o(respuesta: reqwest::Response, si_no: &str) -> String {
    respuesta
        .json::<CuerpoDeError>()
        .await
        .map(|c| c.detalle)
        .unwrap_or_else(|_| si_no.into())
}

/// Un GET al motor, ya autenticado y deserializado al tipo que se pida.
pub async fn get<T: DeserializeOwned>(
    ruta: &str,
    parametros: &[(&str, String)],
) -> Result<T, ErrorDeApi> {
    enviar(reqwest::Method::GET, ruta, parametros, None, TIMEOUT, false).await
}

/// El `GET /` del motor, que es la **única** ruta donde un 503 no es un error:
/// significa que la API vive y la base todavía no.
///
/// Tiene verbo propio porque esa tolerancia es de esta ruta y de ninguna otra.
/// Cuando era una excepción global, un 503 de cualquier otro endpoint se colaba
/// como respuesta buena y reventaba al deserializar.
pub async fn get_salud<T: DeserializeOwned>(ruta: &str) -> Result<T, ErrorDeApi> {
    enviar(reqwest::Method::GET, ruta, &[], None, TIMEOUT, true).await
}

/// Un POST al motor, con los parametros por query string.
pub async fn post<T: DeserializeOwned>(
    ruta: &str,
    parametros: &[(&str, String)],
) -> Result<T, ErrorDeApi> {
    enviar(
        reqwest::Method::POST,
        ruta,
        parametros,
        None,
        TIMEOUT_LARGO,
        false,
    )
    .await
}

/// Un POST con cuerpo JSON. Lo usa el alta de modelos, que manda un objeto.
///
/// **Va con el timeout largo**: `POST /modelos` sondea el modelo contra el
/// proveedor antes de guardarlo, asi que tarda lo que tarde el proveedor.
pub async fn post_json<T: DeserializeOwned>(
    ruta: &str,
    cuerpo: serde_json::Value,
) -> Result<T, ErrorDeApi> {
    enviar(
        reqwest::Method::POST,
        ruta,
        &[],
        Some(cuerpo),
        TIMEOUT_LARGO,
        false,
    )
    .await
}

/// Un PATCH con los parametros por query string. Lo usa activar un modelo.
///
/// **Timeout largo, y no es de mas**: `PATCH /modelos/{id}?activo=true` sondea
/// el proveedor antes de prender, para que el error salga cuando se aprieta el
/// boton y no quince minutos despues en la sintesis.
pub async fn patch<T: DeserializeOwned>(
    ruta: &str,
    parametros: &[(&str, String)],
) -> Result<T, ErrorDeApi> {
    enviar(
        reqwest::Method::PATCH,
        ruta,
        parametros,
        None,
        TIMEOUT_LARGO,
        false,
    )
    .await
}

/// Un PATCH con cuerpo JSON. Lo usa cambiar el destino de entrega.
pub async fn patch_json<T: DeserializeOwned>(
    ruta: &str,
    cuerpo: serde_json::Value,
) -> Result<T, ErrorDeApi> {
    enviar(
        reqwest::Method::PATCH,
        ruta,
        &[],
        Some(cuerpo),
        TIMEOUT,
        false,
    )
    .await
}

#[cfg(test)]
mod pruebas {
    use super::*;

    #[test]
    fn sin_token_guardado_un_401_significa_que_falta() {
        // **Este es el caso que estaba roto.** Contra un motor con la API
        // abierta, la app sin credencial tiene que funcionar; y si el motor SI
        // la pide, el 401 tiene que decir "falta" y no "la que tenes no sirve".
        assert!(matches!(falta_o_no_sirve(false), ErrorDeApi::SinToken));
    }

    #[test]
    fn con_token_guardado_un_401_significa_que_no_sirve() {
        // Piden acciones distintas de quien mira: pegar una credencial contra
        // cambiar la que hay porque el .env del motor se movio.
        assert!(matches!(falta_o_no_sirve(true), ErrorDeApi::NoAutorizado));
    }

    #[test]
    fn se_leen_las_dos_formas_de_cuerpo_de_error() {
        // El motor arma unos errores y FastAPI otros, con nombres distintos
        // para el mismo campo. Leer sólo uno no rompe nada visible: se cae al
        // texto de reemplazo y tapa el mensaje que servía.
        let propio: CuerpoDeError = serde_json::from_str(r#"{"detalle":"en español"}"#).unwrap();
        assert_eq!(propio.detalle, "en español");

        let de_fastapi: CuerpoDeError = serde_json::from_str(r#"{"detail":"in english"}"#).unwrap();
        assert_eq!(de_fastapi.detalle, "in english");
    }

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
