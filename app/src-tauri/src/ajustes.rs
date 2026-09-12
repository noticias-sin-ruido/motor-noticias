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
//! un plugin: son dos campos, y una dependencia más para leer un archivo de dos
//! líneas no se paga sola.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct Ajustes {
    /// Carpeta del repo del motor, la que contiene `docker-compose.yml`.
    pub ruta_del_repo: Option<PathBuf>,

    /// Hasta cuándo se revisaron los problemas, en ISO-8601.
    ///
    /// **Vive acá y no en el motor, y es una decisión de semántica antes que de
    /// costo.** "¿Lo vi yo?" es del operador y de su máquina: si dos personas
    /// usan el mismo motor desde dos instalaciones, cada una tiene su estado, y
    /// que una lo marque como visto no puede apagarle la burbuja a la otra.
    ///
    /// El contador cuenta los eventos cuya `ultima_vez` es posterior a esto.
    /// **Que un evento repetido vuelva a contar es la propiedad que se busca**,
    /// no un efecto secundario: un feed que falló, se revisó, y volvió a fallar
    /// mañana tiene que avisar de nuevo. Un "descartar" lo escondería.
    ///
    /// Se pierde al reinstalar, y ahí la burbuja muestra todo una vez. Es el
    /// costo de que sea local, y es barato: se apaga volviendo a mirar.
    #[serde(default)]
    pub problemas_vistos_hasta: Option<String>,
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

/// Cambia un campo dejando los demás como estaban.
///
/// **Existe desde que hay más de un campo, y evita una clase entera de bug.**
/// Con `guardar` a secas, quien construye un `Ajustes` para tocar la ruta pisa
/// en silencio lo que no nombró: guardar la carpeta del repo borraría la marca
/// de problemas revisados. Leer-modificar-escribir en un solo lugar hace que
/// ese error no se pueda cometer desde afuera.
pub fn actualizar(app: &AppHandle, cambio: impl FnOnce(&mut Ajustes)) -> Resultado<()> {
    let mut ajustes = leer(app)?;
    cambio(&mut ajustes);
    guardar(app, &ajustes)
}

/// Pisa los ajustes enteros con lo que se le pase. **Para cambiar un campo va
/// `actualizar`**, que conserva el resto.
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
