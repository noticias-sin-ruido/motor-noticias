# 🔌 Contrato del webhook — Sin Ruido → back-end

Documento para el equipo de back-end. Describe qué manda el motor, cómo validarlo y qué esperamos de vuelta.

El motor **no expone las síntesis por polling**: las empuja apenas las genera. Del otro lado cada síntesis es una fila con vida propia (likes, comentarios, suscripciones), así que lo que importa acá es que sea siempre reconocible entre entregas.

---

## 1. Qué es una "síntesis"

La unidad que se publica **no es la noticia ni el evento, sino el ángulo**.

El motor agrupa toda la cobertura de un mismo hecho en un *cluster*, y después un modelo lee esos textos y los separa en los ángulos distintos que encuentra (el hecho central, sus consecuencias, las reacciones). **Un hecho puede producir varias publicaciones**: en la última corrida real, 39 hechos dieron 47 publicaciones, y 7 de esos hechos aportaron más de un ángulo.

Cada publicación trae, además del resumen neutro, **la comparativa de cómo lo contó cada medio** — qué destacó, qué omitió y una cita textual que lo respalda. Eso es el producto.

---

## 2. El request

```
POST <la URL que nos pasen>
Content-Type: application/json; charset=utf-8
X-SinRuido-Timestamp: 1754740800
X-SinRuido-Signature: sha256=81011cb08fe2ac64a...
```

| Header | Qué es |
|---|---|
| `X-SinRuido-Timestamp` | Unix epoch en segundos, UTC, al momento de firmar |
| `X-SinRuido-Signature` | `sha256=` + HMAC-SHA256 en hexadecimal (ver punto 6) |

**Un request por síntesis, no lotes.** El status HTTP es el acuse de esa síntesis puntual. Si mandáramos un lote, un 200 parcial nos dejaría sin saber qué marcar como entregado.

---

## 3. El payload

Este ejemplo es real, generado por el motor sobre la cobertura del 9/8 (recortado en las partes repetitivas).

```jsonc
{
  "version": 1,
  "evento": "sintesis.creada",

  "sintesis": {
    "id": 23,
    "titulo": "Fallecimiento y velorio de Jorge Messi en Rosario",
    "resumen": "Jorge Messi, padre y exrepresentante de Lionel Messi, falleció a los 68 años en el Sanatorio Centro de Rosario. Sus restos fueron velados en una ceremonia íntima en el cementerio El Prado…",
    "puntos_clave": [
      "Jorge Messi murió a los 68 años tras atravesar un delicado estado de salud.",
      "Lionel Messi viajó de urgencia desde Estados Unidos junto a su esposa, Antonela Roccuzzo, y sus hijos.",
      "El velorio se realizó en el cementerio El Prado en un ambiente de hermetismo, con fuerte custodia policial."
    ],
    "topicos": ["deportes", "espectaculos"],
    "subtopicos": [],
    "fecha_generacion": "2026-08-09T22:47:24Z"
  },

  "hecho": {
    "id": 190,
    "abierto": true
  },

  "comparativa": [
    {
      "medio": { "id": 1, "nombre": "La Nación" },
      "destaco": "El comunicado oficial del Sanatorio Centro, los homenajes de Newell's y la trayectoria de Jorge como pilar de la carrera de su hijo.",
      "omitio": "Los detalles sobre el operativo vial específico del cementerio.",
      "cita": "Jorge Messi, el padre de Lionel, murió a los 68 años este sábado 8 de agosto a las 2 de la madrugada en el Sanatorio Centro, donde se encontraba internado"
    },
    {
      "medio": { "id": 4, "nombre": "TN" },
      "destaco": "Detalles geográficos de la ubicación del cementerio El Prado en Pérez y el esquema de control de ingresos.",
      "omitio": "El paso a paso del viaje de Lionel Messi desde Miami.",
      "cita": "El cementerio El Prado, en las afueras de Rosario, fue el elegido de la familia para despedir a uno de los pilares de la carrera del capitán argentino."
    }
  ],

  "fuentes": [
    {
      "medio": { "id": 4, "nombre": "TN" },
      "titulo": "Cómo es y dónde queda “El Prado”, el cementerio elegido por Lionel Messi para despedir a su papá",
      "url": "https://tn.com.ar/deportes/futbol/2026/08/09/como-es-y-donde-queda-el-prado…",
      "fecha_publicacion": "2026-08-09T12:21:23Z"
    }
  ]
}
```

El ejemplo de arriba pesa 7.530 bytes y es **el más grande de los 47**: la mediana está en **2.431 bytes**, y el percentil 90 en 3.756.

### Campos

| Campo | Tipo | Notas |
|---|---|---|
| `version` | int | Versión de **este contrato**. Va en el cuerpo y no en la URL para que puedan soportar dos versiones a la vez durante una migración, sin coordinar un cambio de endpoint |
| `evento` | `"sintesis.creada"` \| `"sintesis.actualizada"` | **Informativo.** El upsert por `sintesis.id` va igual en los dos casos. Sirve para decisiones suyas, como notificar suscriptores solo cuando la publicación es nueva |
| `sintesis.id` | int | **La clave.** Estable para siempre, ver punto 5 |
| `sintesis.titulo` | string | Qué recorte del hecho cubre esta publicación. Es el título que ve el usuario |
| `sintesis.resumen` | string | Redacción neutra, solo hechos que sostenga más de un medio |
| `sintesis.puntos_clave` | string[] | Entre 2 y 4 sobre 47 publicaciones reales, casi siempre 3. No asuman cantidad fija |
| `sintesis.topicos` | string[] | 1 o 2, de la lista cerrada del punto 4. **No asuman orden significativo** — son un conjunto, no una principal + una secundaria |
| `sintesis.subtopicos` | string[] | 0 o más, recorte más fino dentro de los tópicos elegidos. Puede venir vacío. Ver punto 4 |
| `sintesis.fecha_generacion` | ISO 8601 UTC | Ver punto 7 (entregas cruzadas) |
| `sintesis.publicacion_redes` | objeto \| `null` | `null` en la mayoría de las síntesis. Ver punto 9 |
| `hecho.id` | int | Agrupa los ángulos de una misma historia. Ver la advertencia de abajo |
| `hecho.abierto` | bool | El hecho todavía puede sumar ángulos o actualizar los que tiene. Alcanza para mostrarlo como "en desarrollo" |
| `comparativa[]` | array | **Una entrada por medio**, no por nota. Ordenada alfabéticamente por nombre. Todo medio que aparezca acá tiene al menos una nota en `fuentes` — ver punto 5 |
| `fuentes[]` | array | **Una entrada por nota.** Un medio puede aparecer varias veces. Ordenadas por fecha de publicación |

**Sobre `hecho`: mandamos el id pero deliberadamente no un título.** Internamente el cluster tiene un nombre, pero es el titular de la primera nota que lo formó — o sea, el encuadre de un medio puntual. Mostrarlo como nombre "del hecho" sería presentar como neutro justo lo que el producto se propone no hacer. Si necesitan una etiqueta para agrupar en pantalla, avisen y la generamos neutra; no reciclen ese campo.

**Sobre `medio`: usen el `id`, no el `nombre`.** El nombre es para mostrar y puede cambiar (un rebranding, una tilde corregida). Si lo usan como clave, esos cambios les dejan filas huérfanas.

---

## 4. Tópicos y subtópicos

> **Cambio de forma sobre una versión anterior de este contrato**: `topico` + `topico_secundario` (dos strings, una principal y otra secundaria) pasaron a `topicos` + `subtopicos` (dos arrays). El campo viejo mezclaba dos preguntas distintas bajo una jerarquía que no correspondía — ver el porqué más abajo. Avisen si esto ya estaba integrado de su lado.

### Tópicos: 1 o 2, categorías **pares**

Lista **cerrada**. Si el valor pudiera ser texto libre terminarían con "Deportes", "deportes" y "Fútbol" conviviendo, y la navegación se rompe sola.

```
politica        internacional
economia        deportes
sociedad        espectaculos
policiales      tecnologia
ciencia         lifestyle
```

- `salud` y `educacion` entran en **sociedad**; `cultura` en **espectaculos**. Con 6 medios no juntan volumen propio.
- **No hay `opinion` ni `columnistas`.** Eso es género, no tema: una columna sobre inflación es `economia`.
- Si necesitan una categoría nueva, es un cambio nuestro de una línea. Pídanla.

`topicos` trae **más de una entrada solo cuando la cobertura pertenece con igual derecho a dos temas** — nunca una principal y otra secundaria. El caso real que motivó el diseño: el velorio de Jorge Messi lo publicó TN en deportes y Paparazzi en espectáculos, y las dos secciones tienen razón. La versión anterior de este contrato representaba eso como `topico: "deportes", topico_secundario: "espectaculos"`, una jerarquía que no existe en la realidad — las dos categorías son igual de válidas. Ahora es `topicos: ["deportes", "espectaculos"]`, dos entradas pares.

### Subtópicos: 0 o más, recorte fino DENTRO de un tópico

Lista cerrada y separada, para la pregunta que `topico_secundario` no distinguía de la anterior: no "de qué otra categoría es esto", sino "qué recorte más específico tiene dentro de una categoría ya elegida".

```
deportes:       futbol, rugby, hockey, tenis, automovilismo, basquetbol
espectaculos:   teve, musica, cine, chimentos
economia:       negocios, campo
internacional:  estados_unidos
sociedad:       salud, educacion
lifestyle:      propiedades, autos, cocina
```

`salud` y `educacion` son la excepción a "medido, no inventado": se sumaron por decisión editorial pese a volumen bajo o nulo en el corpus medido, porque son categorías que se buscan específicamente y no tenerlas desde el día uno dejaría esas búsquedas sin filtro fino. Mismo criterio que ya se aplicó con los deportes minoritarios (rugby, hockey, etc.).

Política, policiales, tecnología y ciencia no tienen subtópicos todavía — no hay suficiente volumen medido para justificar una lista cerrada ahí. Puede sumarse más adelante.

**Garantía**: todo subtópico en `subtopicos` tiene su categoría correspondiente presente en `topicos`. No lo decide el modelo caso por caso — lo asegura el motor después de generar la síntesis, agregando la categoría si hiciera falta. Nunca van a recibir `subtopicos: ["futbol"]` sin `"deportes"` en `topicos`.

Ejemplo real con subtópico poblado:

```jsonc
{
  "sintesis": {
    "titulo": "Rumores de romance entre Griselda Siciliani y Emiliano Brancciari y la desmentida de ambos",
    "topicos": ["espectaculos"],
    "subtopicos": ["chimentos"]
    // ...
  }
}
```

### Cómo usarlos

**Filtren por `topicos` como conjunto** (¿contiene X?), no traten el primer elemento como "el principal" — no hay orden garantizado. Para navegación más fina, agreguen `subtopicos` como filtro opcional encima; para la navegación principal alcanza con `topicos`.

El tópico y el subtópico los decide el modelo leyendo los textos, no la sección de la URL.

Para dimensionar la navegación, así se reparten los tópicos sobre las 80 publicaciones reales medidas tras el rediseño: espectáculos 33, sociedad 16, economía 16, deportes 13, policiales 11, política 10, lifestyle 5, internacional 5, ciencia 2 (suman más de 80 porque algunas tienen 2). Tecnología no apareció en esta corrida, pero va a aparecer.

**31 de las 80 traen un segundo tópico**, un 39% — en línea con lo que ya veíamos con `topico_secundario`. Los subtópicos recién empiezan a poblarse (la taxonomía es de esta misma revisión del contrato): esperen que la proporción crezca corrida a corrida a medida que el volumen de cada categoría lo justifique.

**Ni los tópicos ni los subtópicos cambian entre entregas** (ver punto 5).

---

## 5. Idempotencia: `sintesis.id` es la clave

**`sintesis.id` no cambia nunca.** Es la clave que tienen que usar para el upsert.

Está garantizado por diseño y no por casualidad: cuando llega cobertura nueva de un hecho el motor **re-sintetiza** — actualiza el contenido de los ángulos que ya existen y agrega los que aparecieron, pero **nunca los renombra, ni los parte, ni los combina**. La descomposición en ángulos se congela en la primera síntesis, justamente para no dejarlos con ítems huérfanos que ya tenían likes encima.

Lo mismo vale cuando el motor detecta que dos clusters eran el mismo hecho y los une: las síntesis se mudan al sobreviviente con su id intacto en vez de borrarse.

Consecuencia práctica: **van a recibir el mismo `sintesis.id` más de una vez**, con contenido actualizado. Es lo esperado, no un error nuestro.

Qué puede cambiar entre entregas del mismo id:

| | |
|---|---|
| ✅ Cambia | `resumen`, `puntos_clave`, `fecha_generacion` |
| ✅ Puede sumar | `comparativa` (medios nuevos), `fuentes` (solo suma, nunca quita) |
| ✅ Puede aparecer o cambiar de contenido | `publicacion_redes` -- ver punto 9, no se retracta una vez que aparece |
| ❌ No cambia | `titulo`, `topicos`, `subtopicos`, `hecho.id` |

Que el título y los tópicos estén congelados es la misma decisión: renombrar una publicación, o moverla de Deportes a Espectáculos entre una entrega y la siguiente, confunde a quien ya la vio.

### Coherencia entre comparativa y fuentes

**Todo medio que aparece en `comparativa` tiene al menos una nota suya en `fuentes`.** Vale en la primera entrega y en todas las siguientes.

Lo aclaramos porque hasta hace poco *no* era cierto: el motor validaba la comparativa contra todos los medios del hecho y no contra los que aportaron notas a ese ángulo puntual, así que podía llegarles un enfoque de un medio sin una sola nota que lo respalde. Está corregido y verificado sobre las 47 publicaciones actuales: **ninguna** tiene ese desajuste.

Consecuencia práctica: si modelan `comparativa` y `fuentes` con una FK al mismo `medio`, no necesitan tolerar el caso huérfano.

---

## 6. Validar la firma

Firmamos `"{timestamp}.{cuerpo_crudo}"` con HMAC-SHA256 y el secreto compartido.

El timestamp va **dentro** del mensaje firmado, no solo en un header suelto: si viajara aparte, cualquiera podría reenviar un request capturado cambiándole la fecha y la firma seguiría validando.

```python
import hmac, hashlib, time

def validar(cuerpo_crudo: bytes, timestamp: str, firma_recibida: str, secreto: str) -> bool:
    # 1. Rechazar lo viejo — acota la ventana de replay.
    if abs(time.time() - int(timestamp)) > 300:  # 5 minutos
        return False

    # 2. Recalcular la firma.
    mensaje = timestamp.encode() + b"." + cuerpo_crudo
    esperada = "sha256=" + hmac.new(secreto.encode(), mensaje, hashlib.sha256).hexdigest()

    # 3. Comparación en tiempo constante, no con ==.
    return hmac.compare_digest(esperada, firma_recibida)
```

Tres cosas críticas:

1. **Usen el cuerpo crudo, los bytes tal como llegaron.** Si lo parsean a un objeto y lo vuelven a serializar para verificar, cualquier diferencia de espaciado o de escapes rompe la firma. En la mayoría de los frameworks hay que pedir el raw body explícitamente.
2. **El cuerpo viene en UTF-8 sin escapar los acentos** (mandamos `"La Nación"`, no `"La Nación"`).
3. **Comparen con `compare_digest`**, no con `==`.

El secreto se comparte por variable de entorno de los dos lados, nunca en el repo. Lo definimos cuando tengan el endpoint.

---

## 7. Qué esperamos de la respuesta

| Respuesta | Qué hacemos |
|---|---|
| **2xx** | La marcamos entregada y no volvemos a mandarla salvo que su contenido cambie |
| **4xx** (menos 408, 425, 429) | **Dejamos de reintentar** y salta una alerta. Un 4xx lo leemos como "el contrato se rompió", y eso se arregla con una corrección, no insistiendo |
| **408 / 425 / 429** | Reintentamos con espera creciente |
| **5xx** o timeout | Reintentamos: 3 veces en el momento, y después una vez por corrida del pipeline (cada 15 min) hasta 5 corridas |

**Respondan rápido: solo tienen que aceptar y encolar.** Cortamos a los 10 segundos y lo tratamos como fallo. Procesen en background.

**Si algo les explota procesando, devuelvan 5xx.** Un 200 nos dice "quedó" y no lo reintentamos nunca más.

Después de agotar los reintentos, la síntesis queda pendiente en nuestra base y sale una alerta. Cuando el problema esté resuelto del lado de ustedes la reenviamos con un disparo manual — nada se pierde.

**Entregas cruzadas:** puede pasar que dos requests de la misma síntesis se solapen (uno lento que reintentamos, y el nuevo). Usen `fecha_generacion` para descartar la más vieja.

---

## 8. Volumen esperado

Medido sobre corridas reales con 6 medios y 1.852 notas ingeridas:

- El pipeline corre **cada 15 minutos**
- Peso del request: **mediana 2,4 KB**, percentil 90 3,8 KB, el más grande 7,5 KB
- De 93 hechos detectados, **39 llegaron a publicar**
- Una corrida tranquila no genera nada; la última corrida grande generó **30 publicaciones nuevas** y entregó 47 en unos 10 segundos, porque también salen las que habían quedado pendientes

O sea: ráfagas cortas y espaciadas. El pico a dimensionar no es sostenido sino **del orden de 50 requests seguidos cada 15 minutos**. No hay nada que justifique una cola del lado de ustedes *por volumen* — sí por no bloquear la respuesta.

Y va a crecer: hoy son 6 medios y el cuello de botella del producto es justamente ese. Si sumamos medios, sube la cantidad de hechos que alcanzan dos voces y con eso las publicaciones por corrida. **No dimensionen para 47.**

---

## 9. Publicación en redes sociales (`publicacion_redes`)

**Aditivo, no rompe nada de lo que ya integraron.** Es un campo nuevo dentro de `sintesis`, `null` salvo que aplique.

No toda síntesis está pensada para publicarse en redes (Twitter/Facebook). El mismo modelo que genera la síntesis evalúa, hecho por hecho, si nombra una persona con reconocimiento público o una institución pública o privada de renombre nacional. Si no, `publicacion_redes` viaja `null` — es el caso de la mayoría.

```jsonc
{
  "sintesis": {
    "titulo": "Fallecimiento y velorio de Jorge Messi en Rosario",
    // ...
    "publicacion_redes": {
      "resumen": "Murió Jorge Messi, padre de Lionel, a los 68 años. Fue velado en Rosario en una ceremonia íntima con fuerte custodia.",
      "hashtags": ["messi", "rosario", "futbol"]
    }
  }
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `publicacion_redes.resumen` | string | Menos de 240 caracteres. **No es el mismo texto que `sintesis.resumen`** — es una bajada distinta, pensada para acompañar un posteo, pero igual de neutra (sin adjetivos valorativos) |
| `publicacion_redes.hashtags` | string[] | Entre 2 y 5, en minúscula y sin `#`. Basados en los temas y actores del hecho — **no asuman que están en tendencia hoy**, eso es una decisión de quien publique, no algo que el motor pueda saber |

**No está congelado como `titulo`/`topicos`.** Es contenido de marketing, no la identidad publicada del ángulo: una resíntesis lo puede reemplazar con contenido más actualizado sin que eso rompa nada de su lado.

**Pero tampoco se retracta.** Si una resíntesis posterior deja de considerar el hecho relevante, `publicacion_redes` **no desaparece** ni pasa a `null` — queda con el último contenido que tuvo. Mismo criterio que con el borrado (punto 10): el motor no retracta lo que ya entregó, porque puede estar publicado en redes del otro lado.

**Qué decide con este campo es una conversación aparte con marketing** — el motor da la señal y el copy crudo; cuándo y cómo publicarlo, con qué cadencia, y si los hashtags se curan antes de salir, es lógica de negocio que todavía no está definida.

---

## 10. Lo que este contrato **no** cubre

- **Categorías sin hecho** (horóscopos, recetas, quiniela). No pasan por síntesis porque no hay enfoques que comparar, y no salen por este webhook. Quedan etiquetadas en la base del motor y cómo se consumen es una conversación aparte.
- **Imágenes.** No mandamos ninguna; las fuentes van como URL.
- **Borrado.** No hay evento de baja: el motor nunca retracta una publicación entregada.
