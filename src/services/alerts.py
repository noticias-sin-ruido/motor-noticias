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
from . import eventos
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


def _destinos() -> list:
    """
    A quién avisar: la fila de configuración, con el `.env` como red.

    **Se resuelve acá y no en los nueve puntos de llamada.** `enviar_alerta` se
    invoca desde cinco módulos, algunos con sesión a mano y otros no; pasarles a
    todos un parámetro nuevo para leer una fila sería mover el problema a nueve
    lugares en vez de resolverlo en uno.

    **El `.env` sigue siendo la red, y no es transición: es el caso que más
    importa.** Si la base no responde, eso es exactamente cuando hace falta
    avisar — y una alerta que necesita la base para saber a dónde ir se apaga
    justo cuando el problema es la base. `ALERT_EMAIL_TO` queda como el destino
    que funciona sin nada más.
    """
    try:
        # Import adentro de la función: `alerts` lo importan módulos del
        # pipeline, y colgarle una dependencia a la capa de base en el import
        # de arriba haría que un problema de base rompa el módulo que existe
        # para avisar de los problemas.
        from ..database import get_engine
        from .alertas import destinos_de_alerta
        from sqlmodel import Session

        with Session(get_engine()) as sesion:
            configurados = destinos_de_alerta(sesion)
        if configurados:
            return configurados
    except Exception as error:
        # No se propaga: quedarse sin avisar porque falló leer a quién avisar
        # es la peor forma de fallar que tiene esto.
        logger.warning(f"No se pudieron leer los destinos de alerta: {error}")

    return [settings.ALERT_EMAIL_TO] if settings.ALERT_EMAIL_TO else []


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
    # **El evento se registra ANTES del cooldown, y ese orden es la decisión.**
    # Son dos consumidores del mismo hecho con necesidades opuestas: el mail se
    # calla para no inundar la casilla, el panel tiene que seguir contando. Si
    # esto fuera después del `return`, un feed que falló cuarenta veces
    # aparecería una sola en la pantalla -- y saber que fueron cuarenta es
    # justamente lo que distingue un tropiezo de algo roto.
    eventos.registrar_sin_romper(
        clave=clave, asunto=asunto, mensaje=cuerpo, terminal=ignorar_cooldown
    )

    if not ignorar_cooldown and _en_cooldown(clave):
        logger.warning(f"Alerta '{clave}' silenciada por cooldown: {asunto}")
        return False

    destinos = _destinos()
    if not settings.SMTP_HOST or not destinos:
        # Este log ES la entrega cuando no hay a dónde mandar: lleva el cuerpo
        # entero a propósito. Por eso tampoco estampa el cooldown -- silenciarlo
        # una hora sería perder la alerta, no ahorrarse un mail.
        #
        # **Sin destinos es un estado válido**, no un error de configuración: el
        # operador puede haber vaciado la lista a propósito. El log sigue siendo
        # el canal, y sigue diciendo todo.
        logger.error(f"Sin destino de alerta, no se envió -- {asunto}: {cuerpo}")
        return False

    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = settings.SMTP_USER or destinos[0]
    # **Todos los destinos en un solo mensaje.** Mandar uno por dirección
    # multiplicaría los intentos contra el servidor de correo por cada alerta, y
    # con el cooldown por clave eso no compra nada: si el SMTP rechaza, rechaza
    # para todos.
    mensaje["To"] = ", ".join(destinos)
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
            # **STARTTLS si el servidor lo ofrece, y si no depende de si hay
            # credenciales.** Antes se llamaba siempre, y eso hacía imposible
            # hablar con un servidor plano: un relay SMTP en localhost o un
            # capturador de correo de prueba no ofrecen TLS, y el envío fallaba
            # antes de empezar.
            #
            # **La rama del medio es la que no se puede saltear**: sin cifrado,
            # un `login()` manda usuario y contraseña en texto plano por la red.
            # Antes que eso, no se manda el aviso -- perder una alerta es malo,
            # filtrar la credencial del correo es peor y no se deshace.
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls()
                smtp.ehlo()
            elif settings.SMTP_USER and settings.SMTP_PASSWORD:
                raise RuntimeError(
                    f"{settings.SMTP_HOST} no ofrece STARTTLS y hay credenciales "
                    f"configuradas: no se mandan en texto plano."
                )

            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(mensaje)
    except Exception as error:
        logger.error(f"No se pudo enviar la alerta '{asunto}': {error}")
        return False

    # Recién acá, con el mail ya entregado, arranca el cooldown de esta clave.
    _ultimo_aviso[clave] = ahora_utc()
    return True
