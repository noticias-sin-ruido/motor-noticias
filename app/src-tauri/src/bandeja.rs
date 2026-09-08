//! El ícono de bandeja y las dos formas de salir.
//!
//! **La bandeja se construye acá y no en `tauri.conf.json`, a propósito.**
//! Tauri arma una desde la config si esa clave existe (`app.rs`, "initialize
//! default tray icon if defined"), así que tenerla en los dos lados produce dos
//! íconos con el mismo id. Como el menú sólo se puede definir en código, la
//! config queda sin `trayIcon` y esta es la única fuente.
//!
//! **Las dos salidas van nombradas, y esa es la decisión de fondo.** Un
//! programa que maneja contenedores tiene dos cierres legítimos —irse dejando
//! el motor produciendo, o pararlo todo— y cuál ocurre no puede depender de
//! dónde se hizo clic. Así que en vez de que la cruz signifique una cosa y el
//! menú otra, las dos están escritas con todas las letras.
//!
//! **La bandeja no ejecuta nada por su cuenta**: cada opción le avisa a la
//! ventana y la ventana hace el trabajo. Parar el motor tarda segundos, y
//! hacerlo desde acá dejaría a quien mira sin ninguna señal de que algo está
//! pasando — además de duplicar, en Rust, la lógica que la pantalla ya tiene.

use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Runtime};

/// El canal por el que la bandeja y la cruz le piden a la ventana que decida.
pub const CANAL_SALIDA: &str = "pedido-de-salida";

/// Las etiquetas del menú, como constantes.
///
/// No es ceremonia: el test de abajo afirma sobre **estas** y no sobre copias
/// escritas en el propio test, que es lo que haría que la prueba pase mientras
/// el menú dice otra cosa.
pub const ETIQUETA_DETENER: &str = "Detener motor y salir";
pub const ETIQUETA_DEJAR: &str = "Salir dejando el motor corriendo";
pub const ETIQUETA_ABRIR: &str = "Abrir la cabina";

/// El id de la opción que no le pide nada a la ventana.
const ID_ABRIR: &str = "abrir";

/// Qué se le pide a la ventana. Viaja como texto por el canal de eventos.
pub mod pedido {
    /// La cruz de la ventana: hay que preguntar antes de hacer nada.
    pub const PREGUNTAR: &str = "preguntar";
    /// Ya se eligió parar el motor y salir.
    pub const DETENER_Y_SALIR: &str = "detener_y_salir";
    /// Ya se eligió salir dejando el motor corriendo.
    pub const SALIR_SIN_DETENER: &str = "salir_sin_detener";
}

/// Trae la ventana al frente. Antes de pedir una decisión hay que asegurarse
/// de que haya dónde mostrarla: puede estar minimizada.
pub fn mostrar_ventana<R: Runtime>(app: &AppHandle<R>) {
    if let Some(v) = app.get_webview_window("main") {
        let _ = v.unminimize();
        let _ = v.show();
        let _ = v.set_focus();
    }
}

fn pedir<R: Runtime>(app: &AppHandle<R>, que: &str) {
    mostrar_ventana(app);
    // Si el canal falla no hay nada que hacer acá; la ventana quedó visible y
    // la persona puede volver a intentar desde la cruz.
    let _ = tauri::Emitter::emit(app, CANAL_SALIDA, que);
}

pub fn construir<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let abrir = MenuItem::with_id(app, ID_ABRIR, ETIQUETA_ABRIR, true, None::<&str>)?;
    let detener = MenuItem::with_id(
        app,
        pedido::DETENER_Y_SALIR,
        ETIQUETA_DETENER,
        true,
        None::<&str>,
    )?;
    let dejar = MenuItem::with_id(
        app,
        pedido::SALIR_SIN_DETENER,
        ETIQUETA_DEJAR,
        true,
        None::<&str>,
    )?;
    let menu = Menu::with_items(
        app,
        &[
            &abrir,
            &PredefinedMenuItem::separator(app)?,
            &detener,
            &dejar,
        ],
    )?;

    TrayIconBuilder::with_id("principal")
        .icon(app.default_window_icon().cloned().ok_or_else(|| {
            tauri::Error::AssetNotFound("no hay ícono por defecto para la bandeja".into())
        })?)
        .tooltip("Sin Ruido — Cabina del motor")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(al_elegir)
        .build(app)?;

    Ok(())
}

/// Qué pedido le corresponde a cada opción del menú.
///
/// Vive separada de `al_elegir` **para poder probarla**: `al_elegir` necesita
/// un `AppHandle`, que en un test no existe, así que si la tabla de decisión
/// quedara adentro sólo se podría verificar reescribiéndola en el test — y una
/// copia deja de avisar justo cuando el original cambia.
///
/// `None` es "no hay nada que pedirle a la ventana": abrir la cabina se
/// resuelve acá mismo.
fn pedido_de(id: &str) -> Option<&'static str> {
    match id {
        pedido::DETENER_Y_SALIR => Some(pedido::DETENER_Y_SALIR),
        pedido::SALIR_SIN_DETENER => Some(pedido::SALIR_SIN_DETENER),
        _ => None,
    }
}

fn al_elegir<R: Runtime>(app: &AppHandle<R>, evento: MenuEvent) {
    let id = evento.id().as_ref();
    match pedido_de(id) {
        Some(que) => pedir(app, que),
        None if id == ID_ABRIR => mostrar_ventana(app),
        None => {}
    }
}

#[cfg(test)]
mod pruebas {
    use super::*;

    #[test]
    fn cada_opcion_del_menu_despacha_su_pedido() {
        // Sobre la funcion de verdad, no sobre una copia: un id que no matchea
        // deja la opción muda sin que nada falle, y eso es lo que se vigila.
        assert_eq!(
            pedido_de(pedido::DETENER_Y_SALIR),
            Some(pedido::DETENER_Y_SALIR)
        );
        assert_eq!(
            pedido_de(pedido::SALIR_SIN_DETENER),
            Some(pedido::SALIR_SIN_DETENER)
        );
        assert_eq!(pedido_de("abrir"), None);
        assert_eq!(pedido_de("inventado"), None);
    }

    #[test]
    fn las_dos_salidas_dicen_que_le_pasa_al_motor() {
        // Afirma sobre las constantes que el menú usa de verdad, no sobre
        // copias escritas acá. Que la cruz signifique una cosa y el menú otra
        // es lo que esta fase existe para evitar: si alguien acorta esto a
        // "Salir", la decisión vuelve a depender de dónde se hizo clic.
        for etiqueta in [ETIQUETA_DETENER, ETIQUETA_DEJAR] {
            assert!(
                etiqueta.to_lowercase().contains("motor"),
                "la etiqueta tiene que decir qué le pasa al motor: {etiqueta}"
            );
        }
        assert_ne!(ETIQUETA_DETENER, ETIQUETA_DEJAR);
    }
}
