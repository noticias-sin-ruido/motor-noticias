//! En qué anda el motor, y cómo se lo espera sin adivinar.

use std::time::Duration;

use serde::Serialize;

/// Dónde publica el motor. El `docker-compose.yml` liga `127.0.0.1:8000` a
/// propósito, para que un despliegue con IP pública no exponga la API.
const SALUD: &str = "http://127.0.0.1:8000/";

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
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
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

    match cliente.get(SALUD).send().await {
        Ok(r) => match r.status().as_u16() {
            200 => Sondeo::Lista,
            503 => Sondeo::Degradada,
            otro => Sondeo::Otra(otro),
        },
        Err(_) => Sondeo::Rechazada,
    }
}

/// Traduce un sondeo al estado que se muestra.
pub fn estado_de(sondeo: &Sondeo) -> Estado {
    match sondeo {
        Sondeo::Rechazada => Estado::Arrancando,
        Sondeo::Degradada => Estado::Migrando,
        Sondeo::Lista => Estado::Listo,
        Sondeo::Otra(_) => Estado::Error,
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
        assert_eq!(estado_de(&Sondeo::Degradada), Estado::Migrando);
    }

    #[test]
    fn una_conexion_rechazada_es_arrancando() {
        assert_eq!(estado_de(&Sondeo::Rechazada), Estado::Arrancando);
    }

    #[test]
    fn el_200_es_listo() {
        assert_eq!(estado_de(&Sondeo::Lista), Estado::Listo);
    }

    #[test]
    fn cualquier_otro_codigo_es_error() {
        // Un 500 o un 404 en la salud no son etapas del arranque: son algo
        // que no entendemos, y conviene decirlo en vez de seguir esperando.
        assert_eq!(estado_de(&Sondeo::Otra(500)), Estado::Error);
        assert_eq!(estado_de(&Sondeo::Otra(404)), Estado::Error);
    }

    #[test]
    fn el_techo_de_espera_da_para_una_migracion() {
        // 120 s / 750 ms = 160 intentos. Si alguien baja el techo sin pensar,
        // este test lo hace notar: una migración sobre la base real puede
        // tardar más que un arranque en vacío.
        assert_eq!(intentos_maximos(), 160);
        assert!(TECHO >= Duration::from_secs(60));
    }
}
