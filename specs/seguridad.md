# Legajo de seguridad del motor

**Para qué existe.** Es lo que se le pega al encargo de una revisión de seguridad,
en vez de mandar a alguien a leer 3.200 líneas de `change_logs.md`. Las **clases**
de falla que hay que buscar son generales y viven en el vault del método; acá está
lo que es de **este** proyecto: qué ya se rompió, qué no puede salir, y qué guarda
protege cada cosa.

**Por qué está escrito y no en la cabeza de alguien.** La investigación sobre
reintroducción de vulnerabilidades mide que **el 25,5% de las funciones que reciben
un arreglo de seguridad lo vuelven a necesitar, con hasta 515 días entre un arreglo
y el siguiente**. Medio año largo después nadie se acuerda. Una defensa que depende
de la memoria no es una defensa.

---

## 1. Incidentes cerrados, y qué los reabriría

Ésta es la sección que importa: un cambio que pase cerca de una de estas puertas se
revisa aunque no parezca tocar seguridad.

### SR-01 — la credencial viajó antes de validar el destino (cerrado 03/09/2026)

`POST /modelos` mandaba `MODELO_API_KEY` como Bearer al `base_url` que indicara
quien llamaba —dos veces, una por cada mecanismo que prueba el sondeo— **antes de
saber si el proveedor servía**. El alta devolvía 422 y no guardaba nada, pero la
credencial ya había viajado. Verificado con un captor local que registró su llegada
sin imprimir el valor.

**Arreglo:** lista blanca de destinos declarados. La regla se invirtió: no se trata
de bloquear más adentro, sino de **mirar hacia afuera**.

**Lo reabre:** cualquier camino nuevo que adjunte una credencial a un request cuyo
destino venga de la base o de la API. Un proveedor nuevo, un sondeo nuevo, un
reintento que arme la URL de otra forma.

### La exfiltración que se reabrió por la puerta de al lado

Una auditoría cerró una vía de exfiltración. Una tanda posterior de multimodelo
—que no era irreversible ni tocaba datos, así que **no calificaba para revisión
reforzada**— la reabrió por un camino adyacente. La encontró el tercer par de ojos.

**Es el caso que justifica esta sección entera.** Lo reabre: cualquier trabajo
sobre proveedores, credenciales o destinos de salida.

### La clave de Groq en un respaldo de `.env`

Un respaldo de `.env` quedó sin ignorar en un repo público y hubo que rotar la
clave. **El arreglo fue estructural y no de disciplina**: `.gitignore` pasó de
`.env` exacto a `.env.*` con `!.env.example`, comprobado contra los cinco casos.

**Lo reabre:** un patrón de `.gitignore` más flojo, un archivo de configuración
nuevo con otro nombre, o un script que escriba una copia de un `.env`.

### El timestamp corrido tres horas rompía la anti-replay

La firma HMAC usaba `datetime.utcnow().timestamp()`. Sobre un datetime *naive* eso
se interpreta como hora local: desde Argentina el timestamp salía corrido 3 horas y
**cualquier receptor que validara la ventana anti-replay respondía 401**. Se arregló
con `time.time()`.

**Sólo se ve contra un receptor real**: con el cliente HTTP mockeado, todos los
tests pasaban. Lo reabre: tocar la firma, el timestamp o cualquier fecha que entre
en un mensaje firmado.

### La suite escribía en la base de producción (12/09/2026)

`eventos.registrar_sin_romper` abre **su propia sesión** con `get_engine()`, y los
tests corren fuera del contenedor con el mismo `DATABASE_URL`. Resultado: 37 filas
de eventos de prueba mezcladas con las reales. Se arregló con una fixture autouse
en `conftest.py` que lo neutraliza.

**Lo reabre:** cualquier servicio nuevo que abra su propia sesión en vez de recibirla
por parámetro.

---

## 2. Lo que nunca sale en una respuesta

- **`api_key_env`** — ni el valor ni el nombre. No se acepta en el alta y no se
  devuelve. Ninguna respuesta puede nombrar la variable de entorno: saber *cómo se
  llama* ya es información útil para quien ataca.
- **`base_url`** de un modelo, por `GET /modelos`.
- **El cuerpo de la respuesta de un proveedor.** Un error suyo puede traer una
  página entera; se recorta antes de loguear y no se reenvía.
- **La URL del webhook**, por `GET /`, que es ruta abierta.

## 3. Los seis endpoints que exigen token SIEMPRE

`API_TOKEN` es opcional: un despliegue puede tener la API abierta. Estos seis no
aceptan esa opcionalidad y usan `exigir_token_estricto`:

```
GET  /entrega        PATCH /entrega
GET  /alertas        PATCH /alertas
POST /alertas/probar
GET  /eventos
```

**El motivo no es uniforme y conviene saberlo:** los de `/entrega` redirigen **el
producto**, y firmado — quien reciba una entrega desviada obtiene contenido que
parece legítimo porque lo es. Los de `/alertas` pueden **apagar los avisos**, que es
el paso previo a que nadie se entere de lo demás. `/eventos` expone información
operativa del despliegue.

**Un endpoint nuevo que redirija salida, apague avisos o exponga operación va acá.**

## 4. Los dos validadores de red, y por qué son distintos

Es el ejemplo de que **el mismo patrón no se copia, se revisa**.

| | `medios.REDES_PROHIBIDAS` | `proveedores.base.REDES_PROHIBIDAS` |
|---|---|---|
| Bloquea | loopback, los tres rangos privados de IPv4, `fc00::/7`, `0.0.0.0/8`, link-local | **sólo link-local** |
| Además | — | el destino público tiene que estar **declarado** |
| Por qué | un diario en `127.0.0.1` o `10.0.0.5` no tiene uso legítimo: sin bloquearlo el endpoint es un escáner de la red interna a pedido | un modelo en `localhost:11434` **sí** es legítimo, y es el único escenario donde los cuerpos de los artículos no salen de la máquina |

La divergencia es deliberada y tiene un test que la fija. **Unificarlos rompe uno de
los dos**: bloquear privadas en proveedores mata el modelo local; permitirlas en
medios abre el escáner.

## 5. Datos que serían código

**`Adaptador` es un enum cerrado a propósito** (`src/models/modelo_ia.py:31`). La
tentación al leer "que cada operador use el modelo que quiera" es guardar en la base
la ruta de import del adaptador: eso es **ejecución remota de código**, porque quien
pueda escribir una fila elige qué corre adentro del proceso.

**Lo reabre:** cualquier cosa que resuelva un nombre guardado en base hacia código
—un import, un `getattr`, un `eval`, un nombre de clase.

## 6. Dónde viven los secretos

En el entorno, nunca en la base: `WEBHOOK_SECRET`, `MODELO_API_KEY`, `SMTP_*`,
`API_TOKEN`. Lo que sí pasó a la base es la **configuración** —el destino de
entrega, los destinatarios de alertas—, y esa asimetría es deliberada: una
credencial compartida con otro equipo no va en una fila que se respalda y se dumpea.

**Los `.env` los maneja el operador.** No se editan ni se copian desde una sesión;
cuando hace falta un token para una llamada, el pedido sale **desde adentro del
contenedor**, donde el entorno ya está cargado.
