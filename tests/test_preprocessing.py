"""
Tests del preproceso de evidencia.

El modelo de spaCy y el TF-IDF se mockean donde hace falta: son la frontera con
librerías externas y cargarlos costaría cientos de MB por corrida. Lo que sí se
prueba de verdad es la lógica propia — unificación de entidades, selección de
representativas y armado del diff entre medios — que es donde están las
decisiones y los errores que ya se vieron con datos reales.
"""
from collections import Counter
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlmodel import Session

from src.models import Cluster, Medio, Noticia
from src.services import preprocessing


@pytest.fixture(autouse=True)
def _limpiar_cache():
    """El TF-IDF se cachea a nivel módulo: sin esto, un test hereda el del anterior."""
    preprocessing._vectorizador = None
    preprocessing._corpus_entrenado = 0
    yield
    preprocessing._vectorizador = None
    preprocessing._corpus_entrenado = 0


class EntidadFalsa:
    def __init__(self, text: str, label_: str):
        self.text = text
        self.label_ = label_


class DocFalso:
    def __init__(self, ents):
        self.ents = ents


def nlp_falso(mapa):
    """Devuelve un `nlp` que reconoce entidades según un mapa texto -> entidades."""

    def _nlp(texto):
        for clave, entidades in mapa.items():
            if clave in texto:
                return DocFalso([EntidadFalsa(t, e) for t, e in entidades])
        return DocFalso([])

    return _nlp


class TestMapaCanonico:
    def test_unifica_variantes_con_erratas(self):
        """El caso real: NER leyó 'Iara' donde decía 'Lara'."""
        mapa = preprocessing.mapa_canonico(
            Counter({"Lara Agustina Ledesma": 5, "Iara Agustina Ledesma": 2})
        )
        assert mapa["Iara Agustina Ledesma"] == mapa["Lara Agustina Ledesma"]

    def test_unifica_la_forma_abreviada(self):
        mapa = preprocessing.mapa_canonico(
            Counter({"Lara Agustina Ledesma": 4, "Ledesma": 3})
        )
        assert mapa["Ledesma"] == mapa["Lara Agustina Ledesma"]

    def test_no_fusiona_personas_del_mismo_apellido(self):
        """Un solo token compartido no alcanza: son dos personas distintas."""
        mapa = preprocessing.mapa_canonico(Counter({"Jorge Messi": 5, "Lionel Messi": 4}))
        assert mapa["Jorge Messi"] != mapa["Lionel Messi"]

    def test_la_canonica_es_la_mas_mencionada(self):
        """Los epígrafes dejan variantes basura que ganarían por longitud."""
        mapa = preprocessing.mapa_canonico(
            Counter({"Thiago Medina": 9, "Thiago Medina Y El": 1})
        )
        assert mapa["Thiago Medina Y El"] == "Thiago Medina"


class TestEsRuido:
    @pytest.mark.parametrize(
        "texto", ["Instagram", "WhatsApp", "negó", "tenía", "el", "TN"]
    )
    def test_descarta_ruido(self, texto):
        assert preprocessing._es_ruido(texto, {"tn", "la nación"}) is True

    def test_descarta_el_nombre_del_propio_medio(self):
        assert preprocessing._es_ruido("Ciudad Magazine", {"ciudad magazine"}) is True

    @pytest.mark.parametrize("texto", ["Thiago Medina", "Virrey del Pino"])
    def test_conserva_entidades_reales(self, texto):
        assert preprocessing._es_ruido(texto, {"tn"}) is False


@pytest.fixture
def medios(session: Session) -> list:
    creados = []
    for nombre in ["La Nación", "TN"]:
        m = Medio(
            nombre=nombre,
            url_base=f"https://{nombre[:3].lower()}.com",
            feed_rss=f"https://{nombre[:3].lower()}.com/rss",
        )
        session.add(m)
        creados.append(m)
    session.commit()
    for m in creados:
        session.refresh(m)
    return creados


def crear_noticia(session, medio, n, cluster_id=None, embedding=None, titulo=None):
    noticia = Noticia(
        medio_id=medio.id,
        cluster_id=cluster_id,
        titulo=titulo or f"Titulo {n}",
        url=f"https://test.com/n-{n}",
        guid=f"guid-{n}",
        contenido_limpio=f"Cuerpo de la noticia {n}.",
        fecha_publicacion=datetime.utcnow() - timedelta(hours=1),
        embedding=embedding,
    )
    session.add(noticia)
    session.commit()
    session.refresh(noticia)
    return noticia


class TestSeleccionarRepresentativas:
    def test_conserva_a_todos_los_medios(self, session: Session, medios):
        """Un recorte global dejaría afuera al medio que publicó una sola nota."""
        notas = [crear_noticia(session, medios[0], i, embedding=[1.0] + [0.0] * 383) for i in range(5)]
        notas.append(crear_noticia(session, medios[1], 99, embedding=[0.9, 0.4] + [0.0] * 382))

        elegidas = preprocessing.seleccionar_representativas(notas, 2, maximo=3)

        assert {n.medio_id for n in elegidas} == {medios[0].id, medios[1].id}
        assert len(elegidas) == 3

    def test_completa_hasta_el_techo_con_el_resto(self, session: Session, medios):
        """
        El piso por medio no es el límite: con cupo libre se sigue sumando.
        Medido con datos reales, recortar acá recortaba ángulos publicables.
        """
        notas = [crear_noticia(session, medios[0], i, embedding=[1.0] + [0.0] * 383) for i in range(6)]

        assert len(preprocessing.seleccionar_representativas(notas, 2, maximo=5)) == 5
        assert len(preprocessing.seleccionar_representativas(notas, 2, maximo=99)) == 6

    def test_reparte_el_cupo_sobrante_entre_medios(self, session: Session, medios):
        """
        Por rondas y no por cercanía global: si no, el medio más prolífico se
        come el cupo restante y el material queda sesgado hacia él.
        """
        # El primero publicó 8 notas y el segundo 4, todas casi idénticas.
        notas = [
            crear_noticia(session, medios[0], i, embedding=[1.0] + [0.0] * 383)
            for i in range(8)
        ] + [
            crear_noticia(session, medios[1], i, embedding=[0.99, 0.1] + [0.0] * 382)
            for i in range(8, 12)
        ]

        elegidas = preprocessing.seleccionar_representativas(notas, 1, maximo=6)

        del_prolifico = sum(1 for n in elegidas if n.medio_id == medios[0].id)
        assert len(elegidas) == 6
        assert del_prolifico == 3  # repartido, no 5 y 1

    def test_el_piso_le_gana_al_techo(self, session: Session, medios):
        """Perder un medio cuesta más que unos tokens de más."""
        notas = [
            crear_noticia(session, medios[0], 1, embedding=[1.0] + [0.0] * 383),
            crear_noticia(session, medios[1], 2, embedding=[0.9, 0.4] + [0.0] * 382),
        ]

        elegidas = preprocessing.seleccionar_representativas(notas, 1, maximo=1)

        assert len({n.medio_id for n in elegidas}) == 2

    def test_sin_embeddings_no_falla(self, session: Session, medios):
        notas = [crear_noticia(session, medios[0], i) for i in range(3)]

        assert len(preprocessing.seleccionar_representativas(notas, 2, maximo=2)) == 2


class TestGetVectorizador:
    def test_no_falla_con_un_corpus_chico(self, session: Session, medios):
        """
        Con pocas noticias, `min_df=3` y `max_df=0.4` son insatisfacibles y
        sklearn levanta ValueError. Pasaba en una base recién sembrada.
        """
        for i in range(3):
            crear_noticia(session, medios[0], i)

        vectorizador = preprocessing.get_vectorizador(session)

        assert vectorizador.transform(["cuerpo de la noticia"]).shape[0] == 1

    def test_reutiliza_el_ajuste_mientras_el_corpus_no_crezca(self, session: Session, medios):
        crear_noticia(session, medios[0], 1)
        primero = preprocessing.get_vectorizador(session)

        assert preprocessing.get_vectorizador(session) is primero


class TestConstruirEvidencia:
    def test_cluster_vacio_devuelve_estructura_vacia(self, session: Session):
        cluster = Cluster(titulo_evento="Sin noticias")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        evidencia = preprocessing.construir_evidencia(session, cluster)

        assert evidencia["medios"] == []
        assert evidencia["por_medio"] == {}

    def test_separa_nucleo_comun_de_exclusivas_y_omitidas(self, session: Session, medios):
        cluster = Cluster(titulo_evento="Un hecho")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)

        crear_noticia(session, medios[0], 1, cluster_id=cluster.id, titulo="Nota A")
        crear_noticia(session, medios[1], 2, cluster_id=cluster.id, titulo="Nota B")

        # Ambos nombran a Fulano; solo TN nombra a la denunciante.
        mapa = {
            "Nota A": [("Fulano Detenido", "PER"), ("Fulano Detenido", "PER")],
            "Nota B": [
                ("Fulano Detenido", "PER"), ("Fulano Detenido", "PER"),
                ("Zulema Ledesma", "PER"), ("Zulema Ledesma", "PER"),
            ],
        }
        with patch.object(preprocessing, "get_nlp", return_value=nlp_falso(mapa)):
            evidencia = preprocessing.construir_evidencia(session, cluster)

        assert evidencia["nucleo_comun"]["entidades"] == ["Fulano Detenido"]
        assert evidencia["por_medio"]["TN"]["entidades_exclusivas"] == ["Zulema Ledesma"]
        assert evidencia["por_medio"]["La Nación"]["entidades_omitidas"] == ["Zulema Ledesma"]

    def test_descarta_entidades_de_una_sola_mencion(self, session: Session, medios):
        """Con una mención al pasar, lo que queda es sobre todo mal etiquetado."""
        cluster = Cluster(titulo_evento="Un hecho")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)
        crear_noticia(session, medios[0], 1, cluster_id=cluster.id, titulo="Nota A")
        crear_noticia(session, medios[1], 2, cluster_id=cluster.id, titulo="Nota B")

        mapa = {"Nota A": [("Mencion Unica", "PER")], "Nota B": []}
        with patch.object(preprocessing, "get_nlp", return_value=nlp_falso(mapa)):
            evidencia = preprocessing.construir_evidencia(session, cluster)

        assert evidencia["por_medio"]["La Nación"]["entidades_exclusivas"] == []
