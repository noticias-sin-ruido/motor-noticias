from datetime import datetime
from typing import List, Optional

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from .tipos import JSONVariant

# El id de la única fila, igual que en `ConfiguracionEntrega`. Mismo motivo: hay
# una sola configuración de alertas, y la tabla existe para tener dónde
# guardarla, no para tener muchas.
FILA_UNICA = 1

# Tope por dirección. El RFC 5321 fija 254 caracteres para una dirección
# completa; se acota acá para que una cadena absurda no llegue a la base.
MAX_LARGO_MAIL = 254

# Cuántos destinos se aceptan. No es una restricción técnica sino de propósito:
# esto son las alertas de operación de **un** motor, no una lista de difusión.
# Si hiciera falta avisarle a más gente, el lugar es un alias del lado del
# servidor de correo, que además se administra sin tocar esto.
MAX_DESTINOS = 5


class ConfiguracionAlertas(SQLModel, table=True):
    """
    A quién le avisa el motor cuando algo se rompe. **Lo elige el operador.**

    Hasta el punto 9 del backlog el destino era `ALERT_EMAIL_TO` en el entorno,
    así que cambiarlo era editar un archivo y reiniciar un contenedor. Y las
    alertas **son del operador**: hablan de sus medios, sus feeds y sus corridas.

    **Las credenciales SMTP no están acá, y esa es la misma línea que en
    `ConfiguracionEntrega`.** `SMTP_HOST`, `SMTP_USER` y `SMTP_PASSWORD` siguen
    en el entorno. La distinción tiene contenido: el destino es *a quién se le
    avisa*, la credencial es *con qué cuenta se manda*. Robar el primero desvía
    los avisos; robar el segundo deja mandar mail como vos.

    **Son varios destinos y no uno, por un caso que ya ocurrió.** La casilla que
    se usaba para probar fue deshabilitada por su proveedor, y el motor siguió
    intentando contra una dirección muerta sin que nadie se enterara — el envío
    fallido sólo deja una línea en un log que nadie mira. Un segundo destino es
    la red contra quedarse ciego, que es peor que no tener alertas: creés que
    las tenés.
    """

    id: Optional[int] = Field(default=FILA_UNICA, primary_key=True)

    # **Lo que se guarda acá ya pasó por `services/alertas.validar_destinos`.**
    # La columna no lo puede hacer cumplir, así que la escritura pasa por una
    # sola función.
    #
    # Lista vacía es un estado válido y significa "no avisar por mail". No es lo
    # mismo que estar sin configurar: es una decisión, y el motor la respeta
    # cayendo al log, que es lo que `enviar_alerta` ya hace.
    destinos: List[str] = Field(
        default_factory=list, sa_column=Column(JSONVariant, nullable=False)
    )

    # Cuándo se cambió por última vez, y cuándo se probó por última vez con
    # éxito. Lo segundo es lo que distingue "hay un mail configurado" de "el
    # mail llega", que es justamente la confusión que motivó este punto.
    actualizado_en: Optional[datetime] = Field(default=None)
    ultima_prueba_ok: Optional[datetime] = Field(default=None)
