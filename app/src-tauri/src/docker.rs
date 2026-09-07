//! Prender y apagar los contenedores del motor.
//!
//! La app **maneja** el motor, no lo empaqueta. Todo lo que hace es correr el
//! `docker compose` del repo, así que el motor sigue siendo exactamente el
//! mismo que corre sin la app.

use std::path::Path;
use std::process::{Command, Output};

use serde::Serialize;
use ts_rs::TS;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

/// Sin esto, cada `docker compose` abre una ventana negra de consola arriba de
/// la app. No es cosmético: la ventana roba el foco.
#[cfg(windows)]
const SIN_VENTANA: u32 = 0x0800_0000;

/// Por qué no se pudo manejar el motor, en categorías cerradas.
///
/// **La diferencia entre `NoInstalado` y `DemonioCaido` es la que importa**:
/// piden acciones distintas de quien mira —instalar algo contra prender algo—
/// y sin separarlas la app solo puede mostrar el texto crudo de Docker, que
/// dice qué pasó pero no qué hacer.
#[derive(Debug, Serialize, PartialEq, Eq, TS)]
#[ts(export, export_to = "../../src/bindings/")]
#[serde(tag = "tipo", content = "detalle", rename_all = "snake_case")]
pub enum ErrorDocker {
    /// No hay un `docker` en el PATH.
    NoInstalado,
    /// El binario está, pero el demonio no responde: Docker Desktop apagado.
    DemonioCaido,
    /// Cualquier otro fallo, con el texto que lo explique. Es el cajón de lo
    /// que no tiene una acción propia del otro lado.
    Fallo(String),
}

type Resultado<T> = Result<T, ErrorDocker>;

/// Traduce un fallo al *lanzar* el proceso.
///
/// Se separa en su propia función para poder probarla: construir un
/// `io::Error` es fácil, tener a Docker desinstalado en un test no.
fn fallo_al_lanzar(e: &std::io::Error) -> ErrorDocker {
    if e.kind() == std::io::ErrorKind::NotFound {
        ErrorDocker::NoInstalado
    } else {
        ErrorDocker::Fallo(format!("No se pudo ejecutar docker: {e}"))
    }
}

fn correr(orden: &mut Command) -> Resultado<Output> {
    #[cfg(windows)]
    orden.creation_flags(SIN_VENTANA);

    orden.output().map_err(|e| fallo_al_lanzar(&e))
}

/// Si el demonio de Docker está atendiendo.
///
/// **Se mira el código de salida y no el texto del error**, y no es un detalle:
/// los mensajes de las herramientas vienen traducidos según el Windows en el
/// que corran —en esta misma máquina el compilador contesta en español— así que
/// buscar `"cannot connect"` andaría en una máquina y en otra no.
///
/// `docker info` cuesta milisegundos con el demonio vivo y falla rápido cuando
/// no lo está, así que se puede preguntar antes de cada arranque.
fn demonio_vivo() -> Resultado<bool> {
    let mut orden = Command::new("docker");
    orden.args(["info", "--format", "{{.ServerVersion}}"]);
    Ok(correr(&mut orden)?.status.success())
}

/// Comprueba que se pueda trabajar, o dice exactamente qué falta.
///
/// **Detectar es todo lo que hace.** Prender Docker Desktop desde acá se
/// evaluó y se descartó: el ejecutable no está en una ruta fija —en esta
/// máquina la instalación es por usuario, no en `Archivos de programa`— y
/// arrancarlo tarda entre 30 y 60 segundos, con diálogos propios de por medio.
/// Decir qué falta cuesta milisegundos y no puede salir mal.
fn exigir_docker() -> Resultado<()> {
    if demonio_vivo()? {
        Ok(())
    } else {
        Err(ErrorDocker::DemonioCaido)
    }
}

fn compose(ruta_del_repo: &Path, argumentos: &[&str]) -> Resultado<Output> {
    let compose_yml = ruta_del_repo.join("docker-compose.yml");
    let mut orden = Command::new("docker");
    orden
        .arg("compose")
        .arg("-f")
        .arg(&compose_yml)
        .args(argumentos);
    // El cwd importa: `build: .` del compose es relativo a él.
    orden.current_dir(ruta_del_repo);
    correr(&mut orden)
}

fn revisar(salida: Output, que_hacia: &str) -> Resultado<()> {
    if salida.status.success() {
        return Ok(());
    }
    // El stderr de docker es lo único que dice por qué falló, así que se
    // devuelve — pero recortado: un build fallido escupe cientos de líneas y
    // el mensaje termina en una ventana, no en una terminal.
    let detalle = String::from_utf8_lossy(&salida.stderr);
    let ultimas: Vec<&str> = detalle.lines().rev().take(6).collect();
    let resumen: String = ultimas.into_iter().rev().collect::<Vec<_>>().join("\n");
    Err(ErrorDocker::Fallo(format!("Falló {que_hacia}:\n{resumen}")))
}

/// Levanta los contenedores, **reconstruyendo la imagen si hace falta**.
///
/// **El `--build` no es paranoia y está medido.** Sin él, traer código con una
/// migración nueva deja la base adelantada respecto de la imagen: el
/// `alembic upgrade head` del arranque no encuentra la revisión y el contenedor
/// entra en bucle de reinicio. Desde la app eso se ve como "el motor no
/// responde", sin ninguna pista de por qué. Ya pasó una vez.
///
/// Con la caché caliente cuesta **2 segundos** (medido), así que el seguro sale
/// prácticamente gratis. Lo que sí cuesta es la primera vez después de tocar
/// `requirements.txt`, y por eso `reconstruyendo` es un estado propio que la
/// interfaz muestra en vez de una barra genérica.
pub fn arrancar(ruta_del_repo: &Path) -> Resultado<()> {
    exigir_docker()?;
    let salida = compose(ruta_del_repo, &["up", "-d", "--build"])?;
    revisar(salida, "levantar los contenedores")
}

/// Para los contenedores.
///
/// **`stop` y nunca `down`.** `down` borra los contenedores, y con la bandera
/// equivocada se lleva puesto el volumen de Postgres — o sea las noticias, los
/// clusters y las síntesis. `stop` los deja parados y persiste frente a
/// `restart: unless-stopped`, que es exactamente la semántica que se quiere
/// para "cerrar la app".
pub fn detener(ruta_del_repo: &Path) -> Resultado<()> {
    exigir_docker()?;
    let salida = compose(ruta_del_repo, &["stop"])?;
    revisar(salida, "detener los contenedores")
}

#[cfg(test)]
mod pruebas {
    use super::*;
    use std::io::{Error, ErrorKind};

    #[test]
    fn si_no_esta_el_binario_es_no_instalado() {
        let e = Error::from(ErrorKind::NotFound);
        assert_eq!(fallo_al_lanzar(&e), ErrorDocker::NoInstalado);
    }

    #[test]
    fn otro_fallo_al_lanzar_no_se_confunde_con_no_instalado() {
        // Un permiso denegado no es "no está instalado", y decirlo así mandaría
        // a quien lo lea a instalar algo que ya tiene.
        let e = Error::from(ErrorKind::PermissionDenied);
        assert!(matches!(fallo_al_lanzar(&e), ErrorDocker::Fallo(_)));
    }

    #[test]
    fn las_categorias_viajan_con_su_etiqueta() {
        // El front discrimina por `tipo`, así que el nombre de la variante es
        // parte del contrato con la interfaz y no un detalle interno.
        let json = serde_json::to_string(&ErrorDocker::DemonioCaido).unwrap();
        assert_eq!(json, r#"{"tipo":"demonio_caido"}"#);

        let json = serde_json::to_string(&ErrorDocker::NoInstalado).unwrap();
        assert_eq!(json, r#"{"tipo":"no_instalado"}"#);
    }
}
