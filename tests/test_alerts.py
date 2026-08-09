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
