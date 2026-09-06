//! El token del operador, guardado donde Windows guarda secretos.
//!
//! **Por qué el Credential Manager y no el `.env` del motor.** Leerlo de ahí
//! era más cómodo —es la misma máquina y el mismo dueño— pero mete ese archivo
//! en el camino de un segundo programa, y es justamente el archivo que en este
//! proyecto ya se filtró una vez con una key que hubo que rotar. Además ataría
//! la app a una ruta del disco, que es lo primero que se rompe al mover el repo.
//!
//! **Y no se saltea el token.** El motor decidió que existe y que sin él la API
//! queda abierta; que la app lo esquivara "porque es localhost" sería desarmar
//! por comodidad una defensa que costó una tanda de auditoría.

use keyring::Entry;

/// Cómo queda identificado en el Credential Manager. Con estos dos valores se
/// lo encuentra a mano en `Panel de control -> Administrador de credenciales`,
/// que es lo que alguien necesita para borrarlo sin la app.
const SERVICIO: &str = "sin-ruido-motor";
const USUARIO: &str = "api-token";

/// El error se devuelve como texto porque cruza a JavaScript, donde no hay
/// tipos de Rust. Lo que **no** se hace es incluir el token en el mensaje.
type Resultado<T> = Result<T, String>;

fn entrada(servicio: &str, usuario: &str) -> Resultado<Entry> {
    Entry::new(servicio, usuario)
        .map_err(|e| format!("No se pudo abrir el Credential Manager: {e}"))
}

// Las tres funciones de abajo toman el par servicio/usuario en vez de usar las
// constantes directamente, para que los tests puedan ejercitar **este mismo
// código** contra una entrada propia sin pisar el token real del operador.

fn leer_de(servicio: &str, usuario: &str) -> Resultado<Option<String>> {
    match entrada(servicio, usuario)?.get_password() {
        Ok(token) => Ok(Some(token)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("No se pudo leer el token: {e}")),
    }
}

fn guardar_en(servicio: &str, usuario: &str, token: &str) -> Resultado<()> {
    let token = token.trim();
    if token.is_empty() {
        return Err("El token no puede estar vacío.".into());
    }
    entrada(servicio, usuario)?
        .set_password(token)
        .map_err(|e| format!("No se pudo guardar el token: {e}"))
}

fn borrar_de(servicio: &str, usuario: &str) -> Resultado<()> {
    match entrada(servicio, usuario)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!("No se pudo borrar el token: {e}")),
    }
}

/// El token guardado, o `None` si todavía no se configuró.
///
/// "No hay token" **no es un error**: es el estado normal la primera vez que
/// alguien abre la app. Devolverlo como `Err` obligaría a quien llama a
/// distinguir ese caso de un fallo real leyendo el texto del mensaje.
pub fn leer() -> Resultado<Option<String>> {
    leer_de(SERVICIO, USUARIO)
}

/// Guarda el token. Pisa el anterior si había uno.
pub fn guardar(token: &str) -> Resultado<()> {
    guardar_en(SERVICIO, USUARIO, token)
}

/// Borra el token. Que no exista **no es un error**: el resultado buscado
/// —que no haya token guardado— ya se cumple.
pub fn borrar() -> Resultado<()> {
    borrar_de(SERVICIO, USUARIO)
}

#[cfg(test)]
mod pruebas {
    use super::*;

    /// Estos tests hablan con el **Credential Manager de verdad**, que es el
    /// punto: compilar prueba que `keyring` enlaza, no que funcione contra el
    /// almacén nativo.
    ///
    /// Usan un servicio propio para no pisar el token real del operador, y
    /// limpian al terminar. Por eso mismo **solo corren en Windows**: el CI de
    /// la app tiene que usar un runner `windows-latest`.
    const SERVICIO_DE_PRUEBA: &str = "sin-ruido-motor--test";

    fn usuario_unico(nombre: &str) -> String {
        format!("prueba-{nombre}")
    }

    #[test]
    fn guarda_lee_y_borra_contra_el_almacen_real() {
        let usuario = usuario_unico("ciclo");
        let _ = borrar_de(SERVICIO_DE_PRUEBA, &usuario);

        guardar_en(SERVICIO_DE_PRUEBA, &usuario, "un-token-de-prueba").unwrap();
        assert_eq!(
            leer_de(SERVICIO_DE_PRUEBA, &usuario).unwrap(),
            Some("un-token-de-prueba".to_string())
        );

        borrar_de(SERVICIO_DE_PRUEBA, &usuario).unwrap();
        assert_eq!(leer_de(SERVICIO_DE_PRUEBA, &usuario).unwrap(), None);
    }

    #[test]
    fn sin_entrada_devuelve_none_y_no_error() {
        let usuario = usuario_unico("inexistente");
        let _ = borrar_de(SERVICIO_DE_PRUEBA, &usuario);

        assert_eq!(leer_de(SERVICIO_DE_PRUEBA, &usuario).unwrap(), None);
    }

    #[test]
    fn borrar_lo_que_no_existe_no_es_error() {
        let usuario = usuario_unico("borrar-dos-veces");
        let _ = borrar_de(SERVICIO_DE_PRUEBA, &usuario);

        assert!(borrar_de(SERVICIO_DE_PRUEBA, &usuario).is_ok());
    }

    #[test]
    fn un_token_vacio_se_rechaza() {
        let usuario = usuario_unico("vacio");
        // **Limpiar ANTES no es ceremonia.** Una mutación que sacaba esta misma
        // validación dejó un token vacío guardado, y esa credencial sobrevivió
        // al restaurar el código: el test siguió fallando contra el código
        // bueno. Un test que habla con un almacén de verdad tiene que empezar
        // desde un estado conocido, no desde el que dejó la corrida anterior.
        let _ = borrar_de(SERVICIO_DE_PRUEBA, &usuario);

        assert!(guardar_en(SERVICIO_DE_PRUEBA, &usuario, "   ").is_err());
        // Y no dejó nada guardado.
        assert_eq!(leer_de(SERVICIO_DE_PRUEBA, &usuario).unwrap(), None);

        let _ = borrar_de(SERVICIO_DE_PRUEBA, &usuario);
    }

    #[test]
    fn se_le_recortan_los_espacios() {
        let usuario = usuario_unico("recorte");
        let _ = borrar_de(SERVICIO_DE_PRUEBA, &usuario);

        guardar_en(SERVICIO_DE_PRUEBA, &usuario, "  con-espacios  ").unwrap();
        assert_eq!(
            leer_de(SERVICIO_DE_PRUEBA, &usuario).unwrap(),
            Some("con-espacios".to_string())
        );

        borrar_de(SERVICIO_DE_PRUEBA, &usuario).unwrap();
    }
}
