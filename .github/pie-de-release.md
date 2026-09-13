
---

## Cómo instalar

`Sin.Ruido_<versión>_x64-setup.exe` — instala **por usuario**, así que no pide permisos de administrador. **GitHub cambia los espacios por puntos** en los adjuntos de una Release: es el mismo archivo que `tauri build` produce como `Sin Ruido_...`.

**Windows va a mostrar "Windows protegió su PC".** El instalador no está firmado: *Más información → Ejecutar de todas formas*. Un certificado de firma son cientos de dólares al año y este es un proyecto propio.

**La app no trae el motor adentro: lo maneja.** Antes de abrirla hacen falta **Docker Desktop corriendo** y **el repositorio clonado** — al primer arranque te pide esa carpeta. Los pasos completos están en el [README](https://github.com/noticias-sin-ruido/motor-noticias#ponerlo-a-andar).

**El motor no necesita nada de esto**: es una API y corre donde corra Docker. La app es sólo Windows.
