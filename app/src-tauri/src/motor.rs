//! En qué anda el motor, y cómo se lo espera sin adivinar.

use std::time::Duration;

use serde::Serialize;
use ts_rs::TS;

/// El `GET /` del motor. La base sale de `api::BASE` y no se repite acá: eran
/// dos constantes con el mismo host y puerto, y nada avisaba si una cambiaba
/// sin la otra.
fn salud() -> String {
    format!("{}/", crate::api::BASE)
}

/// Cada cuánto se pregunta mientras arranca. Es local: 750 ms se siente
/// inmediato y no castiga a nadie.
const CADA: Duration = Duration::from_millis(750);

/// Techo de la espera. El arranque corre `alembic upgrade head` antes de
/// uvicorn, así que puede tardar; pasado esto, algo está mal y hay que decirlo
/// en vez de girar para siempre.
const TECHO: Duration = Duration::from_secs(120);

/// Corto: si el motor está apagado, la conexión se rechaza al instante y no
/// tiene sentido esperar.
const TIMEOUT_SONDEO: Duration = Duration::from_millis(1500);

/// En qué anda el motor, tal como lo ve la interfaz.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
#[serde(rename_all = "snake_case")]
pub enum Estado {
    /// Los contenedores están parados.
    Parado,
    /// Se está reconstruyendo la imagen. Puede tardar minutos la primera vez
    /// después de tocar `requirements.txt`.
    Reconstruyendo,
    /// Los contenedores arrancaron pero la API todavía no acepta conexiones.
    Arrancando,
    /// La API contesta pero la base todavía no: es el `alembic upgrade head`
    /// del arranque, o Postgres terminando de levantar.
    Migrando,
    /// Listo para usarse.
    Listo,
    /// Se agotó la espera o falló el arranque.
    Error,
}

/// Lo que devuelve un sondeo, antes de convertirlo en `Estado`.
#[derive(Debug, PartialEq, Eq)]
pub enum Sondeo {
    /// Nadie escuchando: los contenedores no están arriba todavía.
    Rechazada,
    /// **HTTP 503.** El motor lo devuelve cuando la API vive y la base no —
    /// ver `GET /` en `src/main.py`. No es un error nuestro: es la señal de
    /// que está a mitad de camino, y es lo que deja distinguir `arrancando`
    /// de `migrando` sin leer logs ni preguntarle a Docker.
    Degradada,
    /// HTTP 200.
    Lista,
    /// Cualquier otra cosa.
    Otra(u16),
}

/// Un sondeo al `GET /`.
///
/// **Sin token, y no es un descuido.** La salud está en `auth.RUTAS_ABIERTAS`:
/// es el endpoint que usa el `HEALTHCHECK` del contenedor, así que no puede
/// exigir credencial. Pedirla acá haría que el arranque fallara antes de que
/// el operador llegue a configurar nada.
pub async fn sondear() -> Sondeo {
    let cliente = match reqwest::Client::builder().timeout(TIMEOUT_SONDEO).build() {
        Ok(c) => c,
        Err(_) => return Sondeo::Rechazada,
    };

    match cliente.get(salud()).send().await {
        Ok(r) => match r.status().as_u16() {
            200 => Sondeo::Lista,
            503 => Sondeo::Degradada,
            otro => Sondeo::Otra(otro),
        },
        Err(_) => Sondeo::Rechazada,
    }
}

/// Traduce un sondeo al estado que se muestra.
/// Qué significa un sondeo **mientras se está arrancando**.
///
/// Acá una conexión rechazada es `arrancando`: se le acaba de pedir a Docker
/// que levante los contenedores, así que "nadie escucha" es una etapa esperada
/// del camino y no un problema.
pub fn estado_al_arrancar(sondeo: &Sondeo) -> Estado {
    match sondeo {
        Sondeo::Rechazada => Estado::Arrancando,
        Sondeo::Degradada => Estado::Migrando,
        Sondeo::Lista => Estado::Listo,
        Sondeo::Otra(_) => Estado::Error,
    }
}

/// Qué significa el mismo sondeo **en reposo**, cuando nadie pidió arrancar.
///
/// **Difiere en una sola rama, y es la que importa**: una conexión rechazada
/// acá significa `parado`, porque no hay ningún arranque en curso que la
/// explique. Son dos funciones y no un parámetro booleano porque el dato es el
/// mismo y lo que cambia es qué se acaba de hacer — y eso lo sabe quien llama,
/// no el sondeo.
///
/// Esto estuvo mal hasta la fase 4: había una sola lectura, la de arriba, y
/// mientras el único que sondeaba era el bucle de arranque nadie lo notó. Con
/// la barra de estado siempre visible, el motor apagado decía «arrancando…»
/// para siempre.
pub fn estado_en_reposo(sondeo: &Sondeo) -> Estado {
    match sondeo {
        Sondeo::Rechazada => Estado::Parado,
        otro => estado_al_arrancar(otro),
    }
}

/// Cuántos intentos entran en el techo de espera.
pub fn intentos_maximos() -> u32 {
    (TECHO.as_millis() / CADA.as_millis()) as u32
}

pub fn intervalo() -> Duration {
    CADA
}

#[cfg(test)]
mod pruebas {
    use super::*;

    #[test]
    fn el_503_es_migrando_y_no_un_error() {
        // Es el corazón de la máquina de estados: el motor usa 503 para decir
        // "vivo pero sin base". Tratarlo como error dejaría a la app diciendo
        // que algo se rompió durante un arranque perfectamente normal.
        assert_eq!(estado_al_arrancar(&Sondeo::Degradada), Estado::Migrando);
        assert_eq!(estado_en_reposo(&Sondeo::Degradada), Estado::Migrando);
    }

    #[test]
    fn una_conexion_rechazada_significa_cosas_distintas_segun_el_contexto() {
        // **El caso que la fase 4 destapó.** El mismo sondeo, dos lecturas:
        // arrancando, "todavía no levantó"; en reposo, "está apagado". Con una
        // sola lectura la barra decía «arrancando…» con el motor detenido.
        assert_eq!(estado_al_arrancar(&Sondeo::Rechazada), Estado::Arrancando);
        assert_eq!(estado_en_reposo(&Sondeo::Rechazada), Estado::Parado);
    }

    #[test]
    fn en_reposo_solo_cambia_la_conexion_rechazada() {
        // Las otras tres ramas tienen que seguir significando lo mismo: si
        // alguna se desalinea, un 503 en reposo dejaría de decir "migrando".
        for sondeo in [Sondeo::Degradada, Sondeo::Lista, Sondeo::Otra(500)] {
            assert_eq!(estado_en_reposo(&sondeo), estado_al_arrancar(&sondeo));
        }
    }

    #[test]
    fn el_200_es_listo() {
        assert_eq!(estado_al_arrancar(&Sondeo::Lista), Estado::Listo);
        assert_eq!(estado_en_reposo(&Sondeo::Lista), Estado::Listo);
    }

    #[test]
    fn cualquier_otro_codigo_es_error() {
        // Un 500 o un 404 en la salud no son etapas del arranque: son algo
        // que no entendemos, y conviene decirlo en vez de seguir esperando.
        assert_eq!(estado_al_arrancar(&Sondeo::Otra(500)), Estado::Error);
        assert_eq!(estado_en_reposo(&Sondeo::Otra(404)), Estado::Error);
    }

    #[test]
    fn el_techo_de_espera_da_para_una_migracion() {
        // 120 s / 750 ms = 160 intentos. Si alguien baja el techo sin pensar,
        // este test lo hace notar: una migración sobre la base real puede
        // tardar más que un arranque en vacío.
        assert_eq!(intentos_maximos(), 160);
        assert!(TECHO >= Duration::from_secs(60));
    }

    #[test]
    fn la_url_de_salud_se_arma_bien_desde_api() {
        // La base la aporta `api::BASE` y la barra la pone esta funcion. Si
        // alguna de las dos cambia, esto se rompe antes que el arranque.
        assert_eq!(salud(), "http://127.0.0.1:8000/");
    }

    #[tokio::test]
    #[ignore = "necesita el motor arriba"]
    async fn el_sondeo_llega_al_motor_real() {
        // El unico test que ejercita `salud()` contra la red. Sin esto, el
        // refactor que saco la constante duplicada no lo cubria nada.
        assert_eq!(sondear().await, Sondeo::Lista);
    }
}
