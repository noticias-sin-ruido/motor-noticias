# Qué cambió en cada versión

Escrito para quien **usa** Sin Ruido: qué se puede hacer ahora que antes no, y
qué hay que tocar al actualizar.

Las decisiones de diseño —qué se evaluó, qué se descartó y por qué— viven en
[`specs/change_logs.md`](specs/change_logs.md). Son dos documentos con dos
lectores distintos y no conviene mezclarlos.

> **Cómo se actualiza.** Traer el repo (`git pull`) y volver a abrir la app: al
> arrancar reconstruye el motor y aplica las migraciones sola. Si una versión
> pide tocar el `.env`, figura acá abajo con el aviso **⚠ Hay que tocar el `.env`**.

---

## 1.2.0 — La cabina

La primera versión con **aplicación de escritorio**. Hasta acá el motor se
operaba editando archivos y reiniciando contenedores; ahora hay una ventana.

**Motor y app se numeran juntos desde esta versión.** No son dos productos: la
cabina no aplica sobre ninguna otra cosa que el motor, así que una versión
incluye a los dos.

### La aplicación

- **Prende y apaga el motor**, y muestra en qué anda mientras arranca:
  *arrancando → migrando → listo*. Cerrar pregunta si se detiene el motor o se
  lo deja corriendo; las dos salidas están en el menú de la bandeja.
- **Lista de trabajo** — los clusters que esperan síntesis, con el detalle de
  cada paso del ciclo. Se puede sintetizar uno a mano, eligiendo el modelo.
- **Feed de lectura** — las síntesis publicadas, con la comparativa por medio.
- **Medios** — sumar, editar y dejar de leer un medio, con el sondeo del feed a
  la vista antes de guardar. Incluye un panel que dice, por medio, en cuántos
  clusters queda solo: es material que se produce y **no se llega a publicar**,
  y sirve para decidir qué medio conviene sumar.
- **Modelos** — qué modelo de IA sintetiza, prenderlo y apagarlo, dar de alta
  uno nuevo.
- **Actividad** — una fila por corrida del ciclo, con lo que produjo.
- **Problemas** — lo que se rompió, con un contador en la pestaña. Es la primera
  forma de enterarse de un fallo sin abrir una terminal.
- **Ajustes** — la carpeta del motor, el token, a dónde se entregan las
  síntesis, y a quién avisar cuando algo falla. Funciona con el motor apagado,
  porque es donde se arregla que el motor no arranque.

### Lo que ahora se configura sin editar archivos

- **A dónde se entregan las síntesis.** Antes era `WEBHOOK_URL` en el `.env`.
- **A quién le avisa el motor.** Antes era `ALERT_EMAIL_TO`, y ahora admite
  varias direcciones. Hay un botón **Enviar prueba**: un destino configurado no
  garantiza que el mail salga.
- **El roster de medios** — alta, baja y edición, con re-sondeo del feed.
- **Los modelos de IA** — alta y activación.

Las credenciales siguen en el `.env` a propósito: `SMTP_*`, `WEBHOOK_SECRET` y
las claves de los proveedores. Una credencial en la base se respalda, se dumpea
y se filtra sola.

### Correcciones que se ven

- **Los medios que publican el cuerpo en `<description>`** —Xataka, por
  ejemplo— ya no se leían: el motor sólo miraba `content:encoded`. Ahora entran.
- **Los acentos escapados** ya no salen publicados. Aparecían de dos formas:
  entidades HTML en los cuerpos de las noticias (`Entre R&iacute;os`) y escapes
  en la respuesta del modelo (`clasificaci&#243;n`, `respald%f3`). Son dos
  causas distintas en puntas opuestas del pipeline, y las dos están cerradas.
- **Un back-end caído ya no cuesta material.** Sin destino configurado, las
  síntesis se acumulan entregables en vez de quemarse.

### ⚠ Al actualizar

**No hay que tocar el `.env`.** Las dos migraciones de esta versión —el destino
de entrega y los destinos de alerta— **se siembran solas** desde las variables
que ya estaban, así que un despliegue existente sigue funcionando igual.

Después de actualizar, esas dos cosas se configuran desde la app y las variables
del `.env` pasan a ser sólo la red por si la base no responde.

---

## 1.1.0

Segunda vía de ingesta por URL, motor de IA desacoplado, token de operador para
la API y logging con salida.

**⚠ Hay que tocar el `.env`**: `GEMINI_API_KEY` pasó a llamarse
`MODELO_API_KEY`, con el mismo valor. Y la migración deja la fila del modelo
**apagada** a propósito —ninguna migración elige proveedor por vos—, así que hay
que prenderla. Sin eso el motor no sintetiza, y lo dice.

Para quien consume el motor no cambió nada: el payload es idéntico y la API es
retrocompatible.

---

## 1.0.0

Primera versión. Ingesta de feeds, vectorización, agrupamiento por similitud,
síntesis neutra con comparativa de enfoques, y entrega al back-end por webhook
firmado.
