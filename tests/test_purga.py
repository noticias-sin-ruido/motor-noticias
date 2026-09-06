"""
Tests del servicio de purga de cuerpos (backlog post-1.0, punto 8).

Todo corre contra la sesión SQLite en memoria de `conftest.py`: la purga es SQL
portable (un `UPDATE` condicional), sin nada que dependa de Postgres.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from src.config import settings
from src.models import Cluster, Medio, Noticia
from src.services.purga import purgar_cuerpos_vencidos


@pytest.fixture
def medio(session: Session) -> Medio:
    m = Medio(nombre="Medio Test", url_base="https://test.com", feeds_rss=["https://test.com/rss"])
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


EMBEDDING_DE_PRUEBA = [0.1] * 384


def crear_noticia(
    session: Session,
    medio: Medio,
    n: int,
    *,
    dias_atras: float = 0,
    cluster_id=None,
    contenido: str = None,
    purgado_en=None,
    # Vectorizada por default: la mayoría de los tests de esta clase quieren
    # una huérfana "normal", que ya pasó por `vectorizar_pendientes` antes de
    # llegar acá -- que es el caso real que la purga espera encontrar. El caso
    # sin vectorizar es la excepción y se pide explícito con `embedding=None`.
    embedding=EMBEDDING_DE_PRUEBA,
) -> Noticia:
    noticia = Noticia(
        medio_id=medio.id,
        cluster_id=cluster_id,
        titulo=f"Titulo {n}",
        url=f"https://test.com/noticia-{n}",
        guid=f"guid-{n}",
        contenido_limpio=contenido if contenido is not None else f"Cuerpo de la noticia {n}.",
        fecha_publicacion=datetime.utcnow() - timedelta(days=dias_atras),
        purgado_en=purgado_en,
        embedding=embedding,
    )
    session.add(noticia)
    session.commit()
    session.refresh(noticia)
    return noticia


class TestAlcance:
    """
    El alcance es deliberadamente chico: solo las noticias sin cluster que ya
    vencieron su ventana. Cada test de esta clase fija UNA variable a la vez
    para que, si el alcance se corrige mal, quede claro cuál mitad falló.
    """

    def test_huerfana_vencida_se_purga(self, session: Session, medio):
        vieja = crear_noticia(session, medio, 1, dias_atras=settings.DIAS_RETENCION_CUERPO + 1)

        resultado = purgar_cuerpos_vencidos(session)

        assert resultado["purgadas"] == 1
        session.refresh(vieja)
        assert vieja.contenido_limpio == ""
        assert vieja.purgado_en is not None

    def test_huerfana_dentro_de_la_ventana_no_se_purga(self, session: Session, medio):
        reciente = crear_noticia(session, medio, 1, dias_atras=1)

        resultado = purgar_cuerpos_vencidos(session)

        assert resultado["purgadas"] == 0
        session.refresh(reciente)
        assert reciente.contenido_limpio != ""
        assert reciente.purgado_en is None

    def test_justo_en_el_limite_no_se_purga(self, session: Session, medio):
        """
        El corte es estricto (`<`, no `<=`): exactamente en el borde sobrevive.

        Se fija `ahora_utc` en vez de restar `dias_atras` a mano, porque dos
        llamadas reales a `datetime.utcnow()` -- una acá, otra dentro de
        `purgar_cuerpos_vencidos` un instante después -- nunca caen en el mismo
        microsegundo: la fila terminaría siempre del lado "vencido" por unos
        microsegundos, y el test no probaría el borde sino la duración de su
        propio setup.
        """
        ahora_fija = datetime(2026, 9, 4, 12, 0, 0)
        limite_exacto = ahora_fija - timedelta(days=settings.DIAS_RETENCION_CUERPO)
        en_el_borde = crear_noticia(session, medio, 1, dias_atras=0)
        en_el_borde.fecha_publicacion = limite_exacto
        session.add(en_el_borde)
        session.commit()

        with patch("src.services.purga.ahora_utc", return_value=ahora_fija):
            purgar_cuerpos_vencidos(session)

        session.refresh(en_el_borde)
        assert en_el_borde.purgado_en is None

    def test_con_cluster_no_se_toca_nunca(self, session: Session, medio):
        """
        Vieja, con cluster, no importa cuánto haya pasado: la población agrupada
        queda afuera a propósito, aunque sería purgable por el mismo argumento
        de antigüedad. Es una decisión de alcance, no un descuido.
        """
        cluster = Cluster(titulo_evento="Evento", estado="procesado")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        agrupada = crear_noticia(
            session, medio, 1, dias_atras=365, cluster_id=cluster.id
        )

        resultado = purgar_cuerpos_vencidos(session)

        assert resultado["purgadas"] == 0
        session.refresh(agrupada)
        assert agrupada.contenido_limpio != ""
        assert agrupada.purgado_en is None

    def test_ya_purgada_no_se_recuenta(self, session: Session, medio):
        """`purgado_en IS NULL` en la condición es lo que vuelve esto idempotente."""
        antes = datetime.utcnow() - timedelta(days=1)
        crear_noticia(
            session, medio, 1,
            dias_atras=settings.DIAS_RETENCION_CUERPO + 1,
            contenido="", purgado_en=antes,
        )

        resultado = purgar_cuerpos_vencidos(session)

        assert resultado["evaluadas"] == 0
        assert resultado["purgadas"] == 0

    def test_sin_embedding_no_se_purga(self, session: Session, medio):
        """
        Destapado en revisión: si `vectorizar_pendientes` falla en alguna
        corrida, la noticia queda sin `embedding` y sin cluster posible para
        siempre (`agrupar_pendientes` exige el embedding). Sin esta guarda, a
        los 7 días se le habría borrado igual el único insumo del que sale ese
        embedding -- justo el que hace falta para reintentar la vectorización
        cuando el problema se resuelva.
        """
        sin_vectorizar = crear_noticia(
            session, medio, 1,
            dias_atras=settings.DIAS_RETENCION_CUERPO + 1,
            embedding=None,
        )

        resultado = purgar_cuerpos_vencidos(session)

        assert resultado["purgadas"] == 0
        session.refresh(sin_vectorizar)
        assert sin_vectorizar.contenido_limpio != ""
        assert sin_vectorizar.purgado_en is None


class TestElLimiteRespetaHorasClusterAbierto:
    """
    `_limite_de_purga` usa `max(DIAS_RETENCION_CUERPO * 24, HORAS_CLUSTER_ABIERTO)`
    en horas, y no `DIAS_RETENCION_CUERPO * 24` a secas. Si un operador sube
    `HORAS_CLUSTER_ABIERTO` por encima de los 7 días de default, un límite fijo
    purgaría noticias que `agrupar_pendientes` todavía considera candidatas a
    cluster.
    """

    def test_una_huerfana_dentro_de_la_ventana_ampliada_sobrevive(
        self, session: Session, medio, monkeypatch
    ):
        # 300 h > 168 h (7 días): la ventana de cluster quedó más ancha que el
        # default de retención. Sin el max(), esto se purgaría igual.
        monkeypatch.setattr(settings, "HORAS_CLUSTER_ABIERTO", 300)
        todavia_candidata = crear_noticia(session, medio, 1, dias_atras=200 / 24)

        purgar_cuerpos_vencidos(session)

        session.refresh(todavia_candidata)
        assert todavia_candidata.purgado_en is None

    def test_mas_vieja_que_la_ventana_ampliada_si_se_purga(
        self, session: Session, medio, monkeypatch
    ):
        monkeypatch.setattr(settings, "HORAS_CLUSTER_ABIERTO", 300)
        mas_vieja = crear_noticia(session, medio, 1, dias_atras=310 / 24)

        purgar_cuerpos_vencidos(session)

        session.refresh(mas_vieja)
        assert mas_vieja.purgado_en is not None


class TestCommitea:
    def test_hace_commit_cuando_purga_de_verdad(self, session: Session, medio):
        """
        Destapado por mutación: quitar el `session.commit()` no lo detectaba
        ningún test. La razón es la base SQLite en memoria de los tests —un
        solo `StaticPool`, una sola conexión— donde `session.refresh()` ve la
        escritura pendiente aunque nunca se haya confirmado, porque no hay una
        segunda conexión real contra la que distinguir "escrito" de
        "confirmado". El síntoma que SÍ importa (que la corrida siguiente del
        scheduler, con una sesión nueva, no vea el cambio) no es observable en
        este entorno; lo que sí se puede observar y es equivalente es que el
        método se haya llamado.
        """
        crear_noticia(session, medio, 1, dias_atras=settings.DIAS_RETENCION_CUERPO + 1)

        with patch.object(session, "commit", wraps=session.commit) as commit_espiado:
            purgar_cuerpos_vencidos(session)

        commit_espiado.assert_called_once()

    def test_no_hace_commit_con_solo_contar(self, session: Session, medio):
        crear_noticia(session, medio, 1, dias_atras=settings.DIAS_RETENCION_CUERPO + 1)

        with patch.object(session, "commit", wraps=session.commit) as commit_espiado:
            purgar_cuerpos_vencidos(session, solo_contar=True)

        commit_espiado.assert_not_called()

    def test_no_hace_commit_si_no_hay_nada_que_purgar(self, session: Session, medio):
        crear_noticia(session, medio, 1, dias_atras=1)

        with patch.object(session, "commit", wraps=session.commit) as commit_espiado:
            purgar_cuerpos_vencidos(session)

        commit_espiado.assert_not_called()


class TestSoloContar:
    def test_no_escribe_nada(self, session: Session, medio):
        vieja = crear_noticia(session, medio, 1, dias_atras=settings.DIAS_RETENCION_CUERPO + 1)

        resultado = purgar_cuerpos_vencidos(session, solo_contar=True)

        assert resultado["evaluadas"] == 1
        assert resultado["purgadas"] == 0
        assert resultado["bytes_liberados"] == 0
        session.refresh(vieja)
        assert vieja.contenido_limpio != ""
        assert vieja.purgado_en is None

    def test_mide_lo_mismo_que_purgaria_de_verdad(self, session: Session, medio):
        for n in range(3):
            crear_noticia(session, medio, n, dias_atras=settings.DIAS_RETENCION_CUERPO + 1)

        conteo = purgar_cuerpos_vencidos(session, solo_contar=True)
        real = purgar_cuerpos_vencidos(session, solo_contar=False)

        assert conteo["evaluadas"] == real["purgadas"] == 3
        assert conteo["bytes_evaluados"] == real["bytes_liberados"]


class TestSobreviveLoQueTieneQueSobrevivir:
    def test_titulo_url_fecha_y_embedding_intactos(self, session: Session, medio):
        """
        `GET /search` no puede probarse acá —usa `cosine_distance` de pgvector,
        que SQLite no soporta (ver `test_search.py`)— pero lo que necesita para
        seguir devolviendo la noticia es exactamente esto: que el embedding y
        los campos de exhibición sobrevivan a la purga sin tocarse.
        """
        vector = [0.1] * 384
        fecha = datetime.utcnow() - timedelta(days=settings.DIAS_RETENCION_CUERPO + 1)
        noticia = Noticia(
            medio_id=medio.id,
            titulo="Un título que sobrevive",
            url="https://test.com/sobrevive",
            guid="guid-sobrevive",
            contenido_limpio="Cuerpo que se va a purgar.",
            fecha_publicacion=fecha,
            embedding=vector,
        )
        session.add(noticia)
        session.commit()
        session.refresh(noticia)

        purgar_cuerpos_vencidos(session)

        session.refresh(noticia)
        assert noticia.titulo == "Un título que sobrevive"
        assert noticia.url == "https://test.com/sobrevive"
        assert noticia.guid == "guid-sobrevive"
        assert noticia.medio_id == medio.id
        assert noticia.fecha_publicacion == fecha
        assert noticia.embedding == vector
        assert noticia.contenido_limpio == ""

    def test_purgado_en_distingue_purgada_de_nunca_tuvo_cuerpo(
        self, session: Session, medio
    ):
        """
        `contenido_limpio == ''` sola es ambigua. Con la marca no lo es: `None`
        es "todavía lo tiene", una fecha es "se le sacó". `ingestion.py`
        garantiza que ninguna fila nace vacía, así que hoy la ambigüedad no
        existe en la práctica -- pero la columna no depende de ese invariante.
        """
        purgada = crear_noticia(
            session, medio, 1, dias_atras=settings.DIAS_RETENCION_CUERPO + 1
        )
        purgar_cuerpos_vencidos(session)
        session.refresh(purgada)

        con_cuerpo = crear_noticia(session, medio, 2, dias_atras=1)

        assert purgada.contenido_limpio == "" and purgada.purgado_en is not None
        assert con_cuerpo.contenido_limpio != "" and con_cuerpo.purgado_en is None


class TestBytesEvaluados:
    def test_coincide_con_el_largo_real_del_contenido(self, session: Session, medio):
        crear_noticia(
            session, medio, 1,
            dias_atras=settings.DIAS_RETENCION_CUERPO + 1,
            contenido="1234567890",
        )
        crear_noticia(
            session, medio, 2,
            dias_atras=settings.DIAS_RETENCION_CUERPO + 1,
            contenido="abcde",
        )

        resultado = purgar_cuerpos_vencidos(session, solo_contar=True)

        assert resultado["bytes_evaluados"] == 15

    def test_sin_candidatas_da_todo_en_cero(self, session: Session, medio):
        crear_noticia(session, medio, 1, dias_atras=1)

        resultado = purgar_cuerpos_vencidos(session)

        assert resultado == {
            "evaluadas": 0, "bytes_evaluados": 0,
            "purgadas": 0, "bytes_liberados": 0,
        }

    def test_cuenta_octetos_y_no_caracteres(self, session: Session, medio):
        """
        `length()` de Postgres sobre `text` cuenta CARACTERES, no bytes. Con
        acentos y eñes de sobra en español, eso subestima el texto real que se
        libera. "ñññ" son 3 caracteres pero 6 bytes en UTF-8 -- si esto diera 3,
        volvió a usarse `length` en vez de `octet_length`.
        """
        crear_noticia(
            session, medio, 1,
            dias_atras=settings.DIAS_RETENCION_CUERPO + 1,
            contenido="ñññ",
        )

        resultado = purgar_cuerpos_vencidos(session, solo_contar=True)

        assert resultado["bytes_evaluados"] == 6


class TestElEndpointDeVerdadNoEscribeConSoloContar:
    """
    Las tres pruebas de `TestPurgeEndpoint` en `test_api.py` mockean
    `purgar_cuerpos_vencidos` entero, así que prueban el cableado del endpoint
    pero no que la garantía de `solo_contar=true` se sostenga de punta a punta
    contra la base real. Esta va sin mock, por el camino HTTP completo.
    """

    def test_solo_contar_a_traves_del_endpoint_no_escribe_nada(
        self, client: TestClient, session: Session, medio
    ):
        vieja = crear_noticia(
            session, medio, 1, dias_atras=settings.DIAS_RETENCION_CUERPO + 1
        )

        respuesta = client.post("/purge?solo_contar=true")

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["evaluadas"] == 1
        assert cuerpo["purgadas"] == 0
        session.refresh(vieja)
        assert vieja.contenido_limpio != ""
        assert vieja.purgado_en is None

    def test_sin_el_flag_a_traves_del_endpoint_si_purga(
        self, client: TestClient, session: Session, medio
    ):
        vieja = crear_noticia(
            session, medio, 1, dias_atras=settings.DIAS_RETENCION_CUERPO + 1
        )

        respuesta = client.post("/purge")

        assert respuesta.status_code == 200
        assert respuesta.json()["purgadas"] == 1
        session.refresh(vieja)
        assert vieja.contenido_limpio == ""
        assert vieja.purgado_en is not None


class TestPurgadasReflejaElRowcountReal:
    """
    `purgadas` sale del `rowcount` del `UPDATE`, no del `SELECT COUNT` medido
    momentos antes -- son la misma consulta salvo por una ventana de tiempo
    entre las dos, y una purga concurrente sobre algunas de las mismas filas
    haría que el conteo previo sobrestime lo que esta corrida tocó de verdad.
    """

    def test_no_reusa_el_conteo_previo_como_purgadas(self, session: Session, medio):
        crear_noticia(session, medio, 1, dias_atras=settings.DIAS_RETENCION_CUERPO + 1)
        crear_noticia(session, medio, 2, dias_atras=settings.DIAS_RETENCION_CUERPO + 1)

        exec_real = session.exec

        def exec_espiado(statement, *args, **kwargs):
            resultado = exec_real(statement, *args, **kwargs)
            # Solo se simula sobre el UPDATE: el SELECT de conteo tiene que
            # seguir devolviendo el número real (2), para que el test
            # signifique "el codigo IGNORA ese 2 al armar `purgadas`" y no
            # "el conteo también dio distinto".
            if type(statement).__name__ == "Update":
                from types import SimpleNamespace
                return SimpleNamespace(rowcount=1)
            return resultado

        with patch.object(session, "exec", side_effect=exec_espiado):
            resultado = purgar_cuerpos_vencidos(session)

        assert resultado["evaluadas"] == 2
        assert resultado["purgadas"] == 1
