"""
Avisos por mail cuando algo del pipeline falla.

El pipeline se recupera solo —cada paso es idempotente y la corrida siguiente
retoma donde quedó— así que esto no es un mecanismo de recuperación: es para
enterarse. Sin esto, un paso que falla en cada corrida durante una semana no se
nota, porque la excepción muere en el log del scheduler.
"""
import logging
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Dict

from ..config import settings
from ..tiempo import ahora_utc

logger = logging.getLogger(__name__)

# Último aviso enviado por clave. Un fallo permanente dispararía 96 mails por
# día a 15 minutos de intervalo, y a partir del tercero ya nadie los lee.
_ultimo_aviso: Dict[str, datetime] = {}


def _en_cooldown(clave: str) -> bool:
    """
    Si todavía no pasó el tiempo mínimo desde el último aviso **entregado**.

    Solo consulta: **no estampa**. Antes esta función marcaba el timestamp al
    responder, o sea antes de intentar el envío, y entonces un envío fallido se
    comía la ventana: los siguientes 60 minutos quedaban mudos sin que se
    hubiera entregado nada.

    El cooldown existe para no inundar la casilla —lo dice su propio comentario
    en `config.py`— y un envío que falla no manda ningún mail, así que no tiene
    por qué gastar ese presupuesto. Quien estampa ahora es `enviar_alerta`, y
    solo cuando el mail salió de verdad.
    """
    anterior = _ultimo_aviso.get(clave)
    if not anterior:
        return False
    return ahora_utc() - anterior < timedelta(minutes=settings.ALERT_COOLDOWN_MINUTOS)


def enviar_alerta(
    asunto: str, cuerpo: str, clave: str, ignorar_cooldown: bool = False
) -> bool:
    """
    Manda un mail de alerta. Devuelve si se envió.

    `clave` agrupa avisos del mismo problema para no repetirlos: el primero sale
    enseguida y los siguientes recién pasado `ALERT_COOLDOWN_MINUTOS`.

    `ignorar_cooldown` es para los avisos **terminales**: los que informan algo
    que ya pasó, que no se va a reintentar y que no se va a volver a informar.
    El cooldown protege contra un fallo que se repite; frente a un evento
    irreversible protege de más y se traga información que nadie va a volver a
    ver. Usarlo solo cuando el emisor garantiza que no repite — si no, vuelve
    el problema de los 96 mails por día.

    Nunca propaga excepciones: si el aviso falla, el pipeline tiene que seguir.
    Fallar al avisar de un fallo no puede ser lo que tire el proceso.
    """
    if not ignorar_cooldown and _en_cooldown(clave):
        logger.warning(f"Alerta '{clave}' silenciada por cooldown: {asunto}")
        return False

    if not settings.SMTP_HOST or not settings.ALERT_EMAIL_TO:
        # Este log ES la entrega cuando no hay SMTP: lleva el cuerpo entero a
        # propósito. Por eso tampoco estampa el cooldown -- silenciarlo una
        # hora sería perder la alerta, no ahorrarse un mail.
        logger.error(f"SMTP sin configurar, no se envió la alerta -- {asunto}: {cuerpo}")
        return False

    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = settings.SMTP_USER or settings.ALERT_EMAIL_TO
    mensaje["To"] = settings.ALERT_EMAIL_TO
    mensaje.set_content(cuerpo)

    try:
        # El `timeout` es obligatorio, no una prolijidad: sin él `smtplib`
        # bloquea indefinidamente y como esto corre en el hilo del job, un
        # servidor colgado traba el pipeline entero. Ver `SMTP_TIMEOUT_SEGUNDOS`.
        with smtplib.SMTP(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            timeout=settings.SMTP_TIMEOUT_SEGUNDOS,
        ) as smtp:
            smtp.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(mensaje)
    except Exception as error:
        logger.error(f"No se pudo enviar la alerta '{asunto}': {error}")
        return False

    # Recién acá, con el mail ya entregado, arranca el cooldown de esta clave.
    _ultimo_aviso[clave] = ahora_utc()
    return True
