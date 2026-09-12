"""
El registro de eventos del motor. Punto 19 del backlog.

Lo que se prueba acá no es que sepa guardar una fila, sino las dos decisiones
que hacen que el panel sirva: que **una clave repetida sume en vez de
multiplicar filas**, y que **el evento quede aunque el mail no salga**.
"""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from src.models.evento import Evento
from src.services import eventos
from src.tiempo import ahora_utc


class TestUnaFilaPorClave:
    def test_la_primera_vez_crea(self, session: Session):
        fila = eventos.registrar(
            session, clave="ingesta:TN", asunto="Feed caído", mensaje="no respondió"
        )
        assert fila.veces == 1
        assert fila.categoria == "ingesta"
        assert fila.primera_vez == fila.ultima_vez

    def test_repetir_suma_en_vez_de_insertar(self, session: Session):
        """
        **La decisión que hace legible el panel.** Un feed caído toda la noche
        daría 96 filas idénticas que tapan todo lo demás.
        """
        for i in range(5):
            eventos.registrar(
                session, clave="ingesta:TN", asunto=f"Feed caído {i}", mensaje="x"
            )
        filas = session.exec(select(Evento)).all()
        assert len(filas) == 1
        assert filas[0].veces == 5

    def test_el_mensaje_que_queda_es_el_ultimo(self, session: Session):
        """El último describe el estado actual; los intermedios ya pasaron."""
        eventos.registrar(session, clave="k", asunto="a", mensaje="primero")
        fila = eventos.registrar(session, clave="k", asunto="b", mensaje="ultimo")
        assert fila.mensaje == "ultimo"

    def test_la_primera_vez_no_se_pisa(self, session: Session):
        """
        Es el dato que contesta «¿desde cuándo viene pasando esto?», que es para
        lo que existe la pantalla.
        """
        primera = eventos.registrar(session, clave="k", asunto="a", mensaje="x")
        cuando = primera.primera_vez
        segunda = eventos.registrar(session, clave="k", asunto="a", mensaje="x")
        assert segunda.primera_vez == cuando
        assert segunda.ultima_vez >= cuando

    def test_claves_distintas_son_filas_distintas(self, session: Session):
        eventos.registrar(session, clave="ingesta:TN", asunto="a", mensaje="x")
        eventos.registrar(session, clave="ingesta:Perfil", asunto="a", mensaje="x")
        assert len(session.exec(select(Evento)).all()) == 2


class TestLaSeveridadSaleSola:
    def test_ignorar_cooldown_marca_terminal(self, session: Session):
        """
        Los tres avisos que pasan `ignorar_cooldown=True` son los que informan
        algo que ya no se puede deshacer. No hace falta un campo de severidad
        aparte: la bandera que ya existía alcanza.
        """
        fila = eventos.registrar(
            session, clave="webhook:agotadas", asunto="a", mensaje="x", terminal=True
        )
        assert fila.terminal is True

    def test_una_vez_terminal_siempre_terminal(self, session: Session):
        """Que la última ocurrencia sea pasajera no borra que algo se perdió."""
        eventos.registrar(session, clave="k", asunto="a", mensaje="x", terminal=True)
        fila = eventos.registrar(session, clave="k", asunto="a", mensaje="x")
        assert fila.terminal is True


class TestElCorteDeLosTextos:
    def test_un_mensaje_enorme_no_entra_entero(self, session: Session):
        """
        El corte está para que un traceback pegado adentro del cuerpo no llegue
        a la base. El log lo sigue teniendo completo.
        """
        fila = eventos.registrar(session, clave="k", asunto="a", mensaje="x" * 9000)
        assert len(fila.mensaje) == 2000


class TestCategorias:
    @pytest.mark.parametrize(
        "clave,esperada",
        [
            ("ingesta:La Nación", "ingesta"),
            ("webhook:agotadas", "webhook"),
            ("robots:https://x.com", "robots"),
            ("sinDosPuntos", "sinDosPuntos"),
        ],
    )
    def test_sale_de_lo_que_va_antes_del_dos_puntos(self, clave, esperada):
        assert eventos.categoria_de(clave) == esperada

    def test_se_pueden_listar_las_que_existen(self, session: Session):
        eventos.registrar(session, clave="ingesta:TN", asunto="a", mensaje="x")
        eventos.registrar(session, clave="webhook:rechazo", asunto="a", mensaje="x")
        eventos.registrar(session, clave="ingesta:Perfil", asunto="a", mensaje="x")
        assert eventos.categorias(session) == ["ingesta", "webhook"]

    def test_listar_filtra_por_categoria(self, session: Session):
        eventos.registrar(session, clave="ingesta:TN", asunto="a", mensaje="x")
        eventos.registrar(session, clave="webhook:rechazo", asunto="a", mensaje="x")
        assert len(eventos.listar(session, categoria="ingesta")) == 1
        assert len(eventos.listar(session)) == 2


class TestLaPurga:
    def test_borra_lo_que_no_se_repite_hace_mucho(self, session: Session):
        vieja = eventos.registrar(session, clave="vieja", asunto="a", mensaje="x")
        vieja.ultima_vez = ahora_utc() - timedelta(days=120)
        session.add(vieja)
        session.commit()
        eventos.registrar(session, clave="nueva", asunto="a", mensaje="x")

        assert eventos.purgar_viejos(session) == 1
        quedan = [e.clave for e in session.exec(select(Evento)).all()]
        assert quedan == ["nueva"]

    def test_lo_de_hace_dos_meses_sobrevive(self, session: Session):
        """
        **Ancla el valor de la retención, no su coherencia interna.**

        Los otros casos usan eventos de 120 días, así que pasan igual con
        cualquier retención más chica: bajar `DIAS_DE_RETENCION` a 1 no los
        rompía — comprobado por mutación, se escapó. Dos meses es el caso que
        distingue 90 de casi cualquier otro número, y es realista: «esto viene
        pasando desde hace un par de meses» es justo la pregunta que el panel
        contesta.
        """
        fila = eventos.registrar(session, clave="de-hace-rato", asunto="a", mensaje="x")
        fila.ultima_vez = ahora_utc() - timedelta(days=60)
        session.add(fila)
        session.commit()

        assert eventos.purgar_viejos(session) == 0

    def test_mira_ultima_vez_y_no_primera_vez(self, session: Session):
        """
        **Un problema que empezó hace cuatro meses y sigue ocurriendo es
        exactamente el que no hay que borrar.** Con `primera_vez` como criterio,
        la purga se llevaría justo el evento más importante.
        """
        fila = eventos.registrar(session, clave="cronica", asunto="a", mensaje="x")
        fila.primera_vez = ahora_utc() - timedelta(days=120)
        fila.ultima_vez = ahora_utc()
        session.add(fila)
        session.commit()

        assert eventos.purgar_viejos(session) == 0


class TestElRegistroNoPuedeRomperNada:
    def test_si_la_base_falla_no_propaga(self, monkeypatch):
        """
        **La regla del avisador.** Fallar al registrar un fallo no puede ser lo
        que tire la corrida — y si la base es justamente lo que está roto, este
        camino se ejercita en el peor momento posible.

        Éste es el único test que llama a la función **de verdad**, así que
        deshace el doble que `conftest` pone para toda la suite. Ese doble
        existe porque `registrar_sin_romper` abre su propia sesión contra la
        base real, y sin él cada corrida de pytest ensuciaba producción.
        """
        import src.database

        monkeypatch.undo()
        monkeypatch.setattr(
            src.database,
            "get_engine",
            lambda: (_ for _ in ()).throw(RuntimeError("sin base")),
        )
        # No levanta: eso es todo lo que tiene que hacer. Y como `get_engine`
        # explota, tampoco toca ninguna base.
        eventos.registrar_sin_romper("k", "a", "x")
