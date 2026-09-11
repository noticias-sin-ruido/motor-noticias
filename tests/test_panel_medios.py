"""
El panel de composición de clusters (bloque F1).

Responde qué medio conviene sumar: cuánto material produce cada uno que **no
llega a publicarse** porque nadie más cubrió el hecho, y de qué tema es.

Lo que estos tests vigilan de verdad son dos cosas que se pueden romper en
silencio: que los baldes se calculen contra `MIN_MEDIOS_CLUSTER` y no contra un
2 escrito a mano, y que el tópico de un cluster sin síntesis salga de la URL —
que es el único lugar de donde puede salir.
"""

from datetime import datetime

from sqlmodel import Session

from src.config import settings
from src.models.cluster import Cluster
from src.models.medio import Medio
from src.models.noticia import Noticia
from src.services.panel_medios import panel_de_medios


def _medio(session: Session, nombre: str, activo: bool = True) -> Medio:
    m = Medio(
        nombre=nombre,
        url_base=f"https://{nombre.lower()}.test",
        feeds_rss=[f"https://{nombre.lower()}.test/rss"],
        activo=activo,
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


def _cluster_con(session: Session, *noticias: tuple) -> Cluster:
    """Un cluster con una noticia por cada `(medio, url)` que se le pase."""
    cluster = Cluster(titulo_evento="Un hecho", estado="procesado")
    session.add(cluster)
    session.commit()
    session.refresh(cluster)

    for i, (medio, url) in enumerate(noticias):
        session.add(
            Noticia(
                medio_id=medio.id,
                cluster_id=cluster.id,
                titulo=f"Titular {cluster.id}-{i}",
                url=url,
                guid=f"g{cluster.id}-{i}",
                contenido_limpio="Cuerpo.",
                fecha_publicacion=datetime.utcnow(),
            )
        )
    session.commit()
    return cluster


class TestLosBaldes:
    def test_reparte_los_clusters_en_los_tres_grupos(self, session: Session):
        uno = _medio(session, "Uno")
        dos = _medio(session, "Dos")
        tres = _medio(session, "Tres")

        _cluster_con(session, (uno, "https://uno.test/deportes/a"))
        _cluster_con(
            session, (uno, "https://uno.test/b"), (dos, "https://dos.test/b")
        )
        _cluster_con(
            session,
            (uno, "https://uno.test/c"),
            (dos, "https://dos.test/c"),
            (tres, "https://tres.test/c"),
        )

        panel = panel_de_medios(session)
        assert panel["clusters"] == {
            "total": 3,
            "solo": 1,
            "con_el_minimo": 1,
            "sobre_el_minimo": 1,
        }

        fila = next(f for f in panel["medios"] if f["nombre"] == "Uno")
        assert (fila["solo"], fila["con_el_minimo"], fila["sobre_el_minimo"]) == (1, 1, 1)
        assert fila["clusters"] == 3

        # Tres sólo aparece en el cluster grande.
        otra = next(f for f in panel["medios"] if f["nombre"] == "Tres")
        assert (otra["solo"], otra["con_el_minimo"], otra["sobre_el_minimo"]) == (0, 0, 1)

    def test_dos_noticias_del_mismo_medio_siguen_siendo_un_medio(self, session: Session):
        """
        El balde cuenta **medios distintos**, no noticias. Un medio que publica
        dos veces el mismo hecho no lo vuelve sintetizable.
        """
        uno = _medio(session, "Uno")
        _cluster_con(
            session, (uno, "https://uno.test/a"), (uno, "https://uno.test/a-bis")
        )
        panel = panel_de_medios(session)
        assert panel["clusters"]["solo"] == 1
        assert panel["clusters"]["con_el_minimo"] == 0

    def test_informa_el_minimo_con_el_que_calculo(self, session: Session):
        """
        La app dice "esto no se puede sintetizar" sobre el balde `solo`, y esa
        frase sólo es cierta si el balde se calculó contra este número.
        """
        assert panel_de_medios(session)["minimo_para_sintetizar"] == (
            settings.MIN_MEDIOS_CLUSTER
        )

    def test_un_medio_apagado_sigue_contando(self, session: Session):
        """
        Su material está en la base y sigue diciendo qué aportaba. Un medio se
        apaga, entre otras cosas, después de mirar este panel.
        """
        apagado = _medio(session, "Apagado", activo=False)
        _cluster_con(session, (apagado, "https://apagado.test/a"))
        fila = next(
            f for f in panel_de_medios(session)["medios"] if f["nombre"] == "Apagado"
        )
        assert fila["activo"] is False
        assert fila["solo"] == 1


class TestElTopicoDeLosQueQuedanSolos:
    def test_sale_de_la_seccion_declarada_en_la_url(self, session: Session):
        """
        Un cluster de un solo medio **nunca se sintetizó**, así que no tiene
        `topicos`. El dato sólo puede salir de la URL.
        """
        uno = _medio(session, "Uno")
        _cluster_con(session, (uno, "https://uno.test/deportes/futbol/partido"))
        _cluster_con(session, (uno, "https://uno.test/deportes/otro-partido"))
        _cluster_con(session, (uno, "https://uno.test/economia/dolar"))

        fila = next(
            f for f in panel_de_medios(session)["medios"] if f["nombre"] == "Uno"
        )
        assert fila["topicos_cuando_esta_solo"] == [
            {"topico": "deportes", "clusters": 2},
            {"topico": "economia", "clusters": 1},
        ]

    def test_lo_que_no_tiene_seccion_se_informa_aparte(self, session: Session):
        """
        No se reparte en un "otros" que lo escondería adentro de un número: 19
        de los 130 clusters solos de la base real no tienen sección derivable.
        """
        uno = _medio(session, "Uno")
        _cluster_con(session, (uno, "https://uno.test/una-nota-cualquiera"))
        fila = next(
            f for f in panel_de_medios(session)["medios"] if f["nombre"] == "Uno"
        )
        assert fila["topicos_cuando_esta_solo"] == []
        assert fila["sin_topico_cuando_esta_solo"] == 1

    def test_no_calcula_topico_de_los_clusters_acompanados(self, session: Session):
        """
        Los clusters con dos medios sí se sintetizan, y ahí el tópico lo decide
        el modelo. Derivarlo de la URL para esos sería competir con el dato
        bueno — y es justo el caso donde los medios se contradicen.
        """
        uno = _medio(session, "Uno")
        dos = _medio(session, "Dos")
        _cluster_con(
            session,
            (uno, "https://uno.test/deportes/a"),
            (dos, "https://dos.test/teve/a"),
        )
        fila = next(
            f for f in panel_de_medios(session)["medios"] if f["nombre"] == "Uno"
        )
        assert fila["topicos_cuando_esta_solo"] == []
        assert fila["sin_topico_cuando_esta_solo"] == 0


class TestElEndpoint:
    def test_devuelve_el_panel(self, client, session: Session):
        uno = _medio(session, "Uno")
        _cluster_con(session, (uno, "https://uno.test/economia/a"))

        respuesta = client.get("/medios/panel")
        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["status"] == "ok"
        assert cuerpo["clusters"]["solo"] == 1
        assert cuerpo["medios"][0]["topicos_cuando_esta_solo"] == [
            {"topico": "economia", "clusters": 1}
        ]

    def test_panel_no_lo_atrapa_la_ruta_con_id(self, client):
        """
        `/medios/panel` está declarado antes que cualquier ruta con `{medio_id}`.
        Si alguien las reordena, `panel` pasaría a leerse como un id inválido.
        """
        assert client.get("/medios/panel").status_code == 200
