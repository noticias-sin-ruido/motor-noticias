// Evita que se abra una consola atrás de la ventana en release. No sacar.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    cabina_lib::run()
}
