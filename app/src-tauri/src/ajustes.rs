//! Lo que la app tiene que recordar entre arranques. Hoy es una sola cosa:
//! dónde está el repo del motor.
//!
//! **Por qué se guarda y no se deduce.** Deducirlo de la ubicación del
//! ejecutable funciona mientras se corre con `npm run tauri dev` desde adentro
//! del repo, y se rompe apenas exista el instalador y la app viva en
//! `Archivos de programa`. Preguntarlo una vez y guardarlo anda en los dos
//! casos.
//!
//! Se usa un JSON propio en el directorio de configuración de la app en vez de
//! un plugin: es un campo, y una dependencia más para leer un archivo de una
//! línea no se paga sola.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct Ajustes {
    /// Carpeta del repo del motor, la que contiene `docker-compose.yml`.
    pub ruta_del_repo: Option<PathBuf>,
}

type Resultado<T> = Result<T, String>;

fn archivo(app: &AppHandle) -> Resultado<PathBuf> {
    let dir = app
        .path()
        .app_config_dir()
        .map_err(|e| format!("No se pudo resolver el directorio de configuración: {e}"))?;
    fs::create_dir_all(&dir).map_err(|e| format!("No se pudo crear {}: {e}", dir.display()))?;
    Ok(dir.join("ajustes.json"))
}

/// Los ajustes guardados. **Un archivo ilegible no es un error fatal**: se
/// arranca con los valores por defecto y la app vuelve a preguntar la ruta.
/// Reventar acá dejaría la ventana inutilizable por un JSON roto a mano.
pub fn leer(app: &AppHandle) -> Resultado<Ajustes> {
    let ruta = archivo(app)?;
    match fs::read_to_string(&ruta) {
        Ok(texto) => Ok(serde_json::from_str(&texto).unwrap_or_default()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Ajustes::default()),
        Err(e) => Err(format!("No se pudo leer {}: {e}", ruta.display())),
    }
}

/// Pisa los ajustes con lo que se le pase. Son un campo: no hay merge que
/// hacer, y fingir uno seria inventar complejidad que nadie pidio.
pub fn guardar(app: &AppHandle, ajustes: &Ajustes) -> Resultado<()> {
    let ruta = archivo(app)?;
    let texto = serde_json::to_string_pretty(ajustes)
        .map_err(|e| format!("No se pudo serializar los ajustes: {e}"))?;
    fs::write(&ruta, texto).map_err(|e| format!("No se pudo escribir {}: {e}", ruta.display()))
}

/// Si esa carpeta es efectivamente el repo del motor.
///
/// **Se comprueba al guardar y no al usar**, para que el error salga cuando
/// alguien elige la carpeta —que es cuando puede corregirlo— y no dos pantallas
/// después, cuando el arranque falle sin decir por qué.
pub fn tiene_compose(ruta: &Path) -> bool {
    ruta.join("docker-compose.yml").is_file()
}

#[cfg(test)]
mod pruebas {
    use super::*;

    #[test]
    fn reconoce_una_carpeta_con_compose() {
        let dir = std::env::temp_dir().join("cabina-prueba-compose");
        let _ = fs::create_dir_all(&dir);
        let _ = fs::write(dir.join("docker-compose.yml"), "services: {}\n");

        assert!(tiene_compose(&dir));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn rechaza_una_carpeta_cualquiera() {
        let dir = std::env::temp_dir().join("cabina-prueba-sin-compose");
        let _ = fs::create_dir_all(&dir);
        // Sin `docker-compose.yml`: es una carpeta, pero no es el repo.
        assert!(!tiene_compose(&dir));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn un_archivo_no_alcanza_tiene_que_ser_carpeta() {
        let dir = std::env::temp_dir().join("cabina-prueba-archivo");
        let _ = fs::create_dir_all(&dir);
        // `docker-compose.yml` existiendo como DIRECTORIO no cuenta: se exige
        // que sea un archivo, o el `-f` de docker fallaría después.
        let _ = fs::create_dir_all(dir.join("docker-compose.yml"));

        assert!(!tiene_compose(&dir));

        let _ = fs::remove_dir_all(&dir);
    }
}
