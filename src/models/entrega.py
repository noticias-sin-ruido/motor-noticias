from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

# El id de la única fila. La configuración de entrega es **una sola** —el motor
# empuja a un back-end, no a varios— así que la tabla existe para tener un lugar
# donde guardarla, no para tener muchas.
#
# Se eligió una fila fija antes que una tabla clave/valor genérica: con una sola
# cosa que configurar, el diccionario genérico agrega una capa de indirección y
# pierde los tipos, y el día que haya una segunda configuración la respuesta es
# una columna más acá, no una tabla de propósito general.
FILA_UNICA = 1

# Mismo tope que `main.MAX_LARGO_URL`. Está repetido a propósito y no importado:
# el modelo no puede depender de la capa de API.
MAX_LARGO_URL = 2048


class ConfiguracionEntrega(SQLModel, table=True):
    """
    A dónde el motor empuja las síntesis. **La configura el operador, no el `.env`.**

    Hasta el punto 11 del backlog el destino era `WEBHOOK_URL` en el entorno, lo
    que significaba que cambiarlo era editar un archivo y reiniciar un
    contenedor. Para el operador que despliega esto en su máquina eso no es
    configurar: es redeployar.

    **El secreto NO está acá, y esa es la línea que no se cruza.** `WEBHOOK_SECRET`
    sigue viviendo en el entorno. La base se respalda, se dumpea y se lee desde
    endpoints; una credencial compartida con otro equipo adentro de una fila se
    filtra sola. Es exactamente la misma regla que `ModeloIA.api_key_env`, que se
    escribió después de encontrar la fuga.

    Que el destino sí pueda vivir en la base y el secreto no es una distinción
    con contenido: la URL es a dónde va el producto, el secreto es lo que prueba
    que el producto es nuestro. Robar la primera desvía; robar el segundo permite
    falsificar.
    """

    __tablename__ = "configuracion_entrega"

    id: Optional[int] = Field(default=FILA_UNICA, primary_key=True)

    # `None` o vacío = no hay a dónde entregar, y el barrido no corre. Es un
    # estado legítimo y frecuente: durante el desarrollo el back-end todavía no
    # existe. Ver `webhook_delivery.entregar_pendientes`.
    #
    # **Lo que se guarda acá ya pasó por `services/entrega.validar_url_de_entrega`.**
    # La columna no lo puede hacer cumplir, así que la escritura pasa por una
    # sola función y el barrido revalida antes de usarla.
    url: Optional[str] = Field(default=None, max_length=MAX_LARGO_URL)

    # Cuándo se cambió por última vez. Es lo que permite explicar un "esto dejó
    # de llegar desde el martes" sin adivinar.
    actualizado_en: Optional[datetime] = Field(default=None)
