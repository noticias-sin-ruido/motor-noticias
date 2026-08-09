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

logger = logging.getLogger(__name__)

# Último aviso enviado por clave. Un fallo permanente dispararía 96 mails por
# día a 15 minutos de intervalo, y a partir del tercero ya nadie los lee.
_ultimo_aviso: Dict[str, datetime] = {}


def _corresponde_avisar(clave: str) -> bool:
    """True si pasó el tiempo mínimo desde el último aviso de esta clave."""
    ahora = datetime.utcnow()
    anterior = _ultimo_aviso.get(clave)

    if anterior and ahora - anterior < timedelta(minutes=settings.ALERT_COOLDOWN_MINUTOS):
        return False

    _ultimo_aviso[clave] = ahora
    return True


def enviar_alerta(asunto: str, cuerpo: str, clave: str) -> bool:
    """
    Manda un mail de alerta. Devuelve si se envió.

    `clave` agrupa avisos del mismo problema para no repetirlos: el primero sale
    enseguida y los siguientes recién pasado `ALERT_COOLDOWN_MINUTOS`.

    Nunca propaga excepciones: si el aviso falla, el pipeline tiene que seguir.
    Fallar al avisar de un fallo no puede ser lo que tire el proceso.
    """
    if not _corresponde_avisar(clave):
        logger.warning(f"Alerta '{clave}' silenciada por cooldown: {asunto}")
        return False

    if not settings.SMTP_HOST or not settings.ALERT_EMAIL_TO:
        logger.error(f"SMTP sin configurar, no se envió la alerta -- {asunto}: {cuerpo}")
        return False

    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = settings.SMTP_USER or settings.ALERT_EMAIL_TO
    mensaje["To"] = settings.ALERT_EMAIL_TO
    mensaje.set_content(cuerpo)

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
            smtp.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(mensaje)
        return True
    except Exception as error:
        logger.error(f"No se pudo enviar la alerta '{asunto}': {error}")
        return False
