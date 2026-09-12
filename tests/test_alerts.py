"""
Tests del avisador.

El pipeline se recupera solo; esto existe para enterarse. Lo que se prueba es
que no inunde el mail y que nunca tire el proceso: fallar al avisar de un fallo
no puede ser lo que rompa la corrida.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.config import settings
from src.services import alerts


@pytest.fixture(autouse=True)
def _limpiar_cooldown():
    alerts._ultimo_aviso.clear()
    yield
    alerts._ultimo_aviso.clear()


@pytest.fixture
def smtp_configurado(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "ALERT_EMAIL_TO", "alertas@test")
    monkeypatch.setattr(settings, "SMTP_USER", None)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", None)


class TestCooldown:
    def test_el_primer_aviso_se_envia(self, smtp_configurado):
        with patch("src.services.alerts.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value = MagicMock()
            assert alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x") is True

    def test_el_segundo_aviso_de_la_misma_clave_se_silencia(self, smtp_configurado):
        """A 15 minutos de intervalo, un fallo permanente serían 96 mails al día."""
        with patch("src.services.alerts.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value = MagicMock()
            alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x")
            enviado = alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x")
            assert enviado is False
            assert smtp.call_count == 1

    def test_claves_distintas_no_se_silencian_entre_si(self, smtp_configurado):
        """Que falle La Nación no debe tapar el aviso de que falló TN."""
        with patch("src.services.alerts.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value = MagicMock()
            alerts.enviar_alerta("A", "...", clave="ingesta:La Nación")
            enviado = alerts.enviar_alerta("B", "...", clave="ingesta:TN")
            assert enviado is True
            assert smtp.call_count == 2

    def test_vuelve_a_avisar_pasado_el_cooldown(self, smtp_configurado):
        alerts._ultimo_aviso["paso:x"] = datetime.utcnow() - timedelta(
            minutes=settings.ALERT_COOLDOWN_MINUTOS + 1
        )
        with patch("src.services.alerts.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value = MagicMock()
            assert alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x") is True


class TestTolerancia:
    def test_sin_smtp_configurado_no_falla(self, monkeypatch):
        monkeypatch.setattr(settings, "SMTP_HOST", None)

        assert alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x") is False

    def test_un_error_de_smtp_no_se_propaga(self, smtp_configurado):
        """Fallar al avisar de un fallo no puede ser lo que tire el proceso."""
        with patch("src.services.alerts.smtplib.SMTP", side_effect=OSError("sin red")):
            assert alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x") is False

    def test_un_timeout_de_smtp_tampoco(self, smtp_configurado):
        """El modo de falla que el timeout convierte en error en vez de cuelgue."""
        with patch(
            "src.services.alerts.smtplib.SMTP", side_effect=TimeoutError("no responde")
        ):
            assert alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x") is False


class TestTimeoutDeSmtp:
    """
    Sin `timeout` explícito, `smtplib` bloquea indefinidamente: su default es
    `socket._GLOBAL_DEFAULT_TIMEOUT` y `socket.getdefaulttimeout()` es `None`.

    Importa porque `enviar_alerta` corre en el hilo del job. Un servidor que
    acepta la conexión y no responde deja ese hilo colgado para siempre, y con
    `max_instances=1` el scheduler saltea todos los ciclos siguientes: el
    pipeline queda trabado sin más recuperación que reiniciar el proceso.

    No se puede colgar un socket de verdad en un unit test, así que esto es una
    aserción de contrato y no de comportamiento — más débil que las otras
    guardas del repo, y conviene saberlo.
    """

    def test_se_le_pasa_un_timeout_acotado(self, smtp_configurado):
        with patch("src.services.alerts.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value = MagicMock()
            alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x")

        timeout = smtp.call_args.kwargs.get("timeout")
        assert timeout is not None, "sin timeout, smtplib bloquea para siempre"
        assert 0 < timeout < 60, f"timeout fuera de rango razonable: {timeout}"


class TestElCooldownCuentaEntregas:
    """
    El cooldown existe para no inundar la casilla, así que lo tiene que gastar
    un mail entregado y no un intento. Antes el timestamp se estampaba **antes**
    de intentar el envío, y un fallo consumía la ventana igual: se perdían 60
    minutos de avisos sin haber entregado nada.
    """

    def test_un_envio_fallido_no_consume_la_ventana(self, smtp_configurado):
        with patch("src.services.alerts.smtplib.SMTP", side_effect=OSError("sin red")):
            assert alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x") is False

        # El SMTP se recupera y el aviso tiene que salir YA, no en una hora.
        with patch("src.services.alerts.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value = MagicMock()
            assert alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x") is True
            assert smtp.call_count == 1

    def test_sin_smtp_configurado_tampoco_la_consume(self, monkeypatch, smtp_configurado):
        """
        Ahí el `logger.error` con el cuerpo entero **es** la entrega. Silenciarlo
        por cooldown sería perder la alerta, no ahorrarse un mail.
        """
        monkeypatch.setattr(settings, "SMTP_HOST", None)
        assert alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x") is False
        assert "paso:x" not in alerts._ultimo_aviso

    def test_un_envio_exitoso_si_la_consume(self, smtp_configurado):
        """La contracara: entregado el mail, el cooldown tiene que arrancar."""
        with patch("src.services.alerts.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value = MagicMock()
            alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x")
            assert alerts.enviar_alerta("Asunto", "Cuerpo", clave="paso:x") is False
            assert smtp.call_count == 1


def _smtp_falso(ofrece_starttls: bool):
    """Un `smtplib.SMTP` de mentira que dice si ofrece STARTTLS."""
    falso = MagicMock()
    falso.has_extn.return_value = ofrece_starttls
    contexto = MagicMock()
    contexto.__enter__.return_value = falso
    return contexto, falso


class TestStarttlsCondicional:
    """
    **La rama del medio es la que importa**: sin cifrado, un `login()` manda
    usuario y contraseña en texto plano. Antes que eso, no se manda el aviso.

    El caso salió de necesitar probar el canal sin credenciales reales: un
    capturador de correo local no ofrece STARTTLS, y la versión anterior lo
    llamaba siempre, así que fallaba antes de empezar. Un relay SMTP en
    localhost tiene exactamente la misma forma.
    """

    def test_si_el_servidor_lo_ofrece_se_usa(self, smtp_configurado):
        contexto, falso = _smtp_falso(ofrece_starttls=True)
        with patch.object(alerts.smtplib, "SMTP", return_value=contexto):
            assert alerts.enviar_alerta("a", "b", clave="k") is True
        falso.starttls.assert_called_once()
        falso.send_message.assert_called_once()

    def test_sin_starttls_y_sin_credenciales_se_manda_igual(self, smtp_configurado):
        """El capturador de prueba y el relay local: no hay nada que filtrar."""
        contexto, falso = _smtp_falso(ofrece_starttls=False)
        with patch.object(alerts.smtplib, "SMTP", return_value=contexto):
            assert alerts.enviar_alerta("a", "b", clave="k") is True
        falso.starttls.assert_not_called()
        falso.login.assert_not_called()
        falso.send_message.assert_called_once()

    def test_sin_starttls_y_con_credenciales_no_se_manda(
        self, smtp_configurado, monkeypatch
    ):
        """
        **La guarda.** Perder una alerta es malo; filtrar la credencial del
        correo es peor y no se deshace.
        """
        monkeypatch.setattr(settings, "SMTP_USER", "cuenta@test")
        monkeypatch.setattr(settings, "SMTP_PASSWORD", "secreta")
        contexto, falso = _smtp_falso(ofrece_starttls=False)
        with patch.object(alerts.smtplib, "SMTP", return_value=contexto):
            assert alerts.enviar_alerta("a", "b", clave="k") is False
        falso.login.assert_not_called()
        falso.send_message.assert_not_called()

    def test_el_motivo_no_filtra_la_contrasena(self, smtp_configurado, monkeypatch, caplog):
        """El log dice qué pasó y nombra el host, nunca la credencial."""
        monkeypatch.setattr(settings, "SMTP_USER", "cuenta@test")
        monkeypatch.setattr(settings, "SMTP_PASSWORD", "secretisima")
        contexto, _ = _smtp_falso(ofrece_starttls=False)
        with patch.object(alerts.smtplib, "SMTP", return_value=contexto):
            with caplog.at_level("ERROR"):
                alerts.enviar_alerta("a", "b", clave="k")
        registro = caplog.text
        assert "secretisima" not in registro
        assert "smtp.test" in registro


class TestLosDestinosSalenDeLaFila:
    """
    A quién avisar lo decide la fila de configuración, no el `.env`. Punto 9.
    """

    def test_se_mandan_todos_los_destinos_en_un_mensaje(
        self, smtp_configurado, monkeypatch
    ):
        monkeypatch.setattr(
            alerts, "_destinos", lambda: ["uno@test.com", "dos@test.com"]
        )
        contexto, falso = _smtp_falso(ofrece_starttls=True)
        with patch.object(alerts.smtplib, "SMTP", return_value=contexto):
            assert alerts.enviar_alerta("a", "b", clave="k") is True
        mensaje = falso.send_message.call_args[0][0]
        assert mensaje["To"] == "uno@test.com, dos@test.com"

    def test_sin_destinos_no_se_manda_pero_el_log_lleva_todo(
        self, smtp_configurado, monkeypatch, caplog
    ):
        """
        Lista vacía es una decisión válida, no un error: el motor cae al log, y
        **el log lleva el cuerpo entero** porque es la entrega.
        """
        monkeypatch.setattr(alerts, "_destinos", list)
        with caplog.at_level("ERROR"):
            assert alerts.enviar_alerta("Se cayó algo", "el detalle", clave="k") is False
        assert "Se cayó algo" in caplog.text
        assert "el detalle" in caplog.text

    def test_si_la_base_no_contesta_cae_al_entorno(self, monkeypatch):
        """
        **El caso que más importa.** Si la base no responde, eso es justo cuando
        hace falta avisar -- y una alerta que necesita la base para saber a
        dónde ir se apaga exactamente cuando el problema es la base.
        """
        monkeypatch.setattr(settings, "ALERT_EMAIL_TO", "red@test.com")
        # Se ejercita la función de verdad, no un doble: el `get_engine` que
        # importa adentro explota y tiene que caer al entorno sin propagar.
        with patch("src.database.get_engine", side_effect=RuntimeError("sin base")):
            assert alerts._destinos() == ["red@test.com"]

    def test_sin_base_y_sin_entorno_devuelve_vacio(self, monkeypatch):
        """Y ahí `enviar_alerta` cae al log, que sigue siendo un canal."""
        monkeypatch.setattr(settings, "ALERT_EMAIL_TO", None)
        with patch("src.database.get_engine", side_effect=RuntimeError("sin base")):
            assert alerts._destinos() == []
