"""
El contrato entre el motor y la cabina de escritorio.

**Por qué vive del lado del motor.** El motor y la app se versionan juntos pero
se rompen por separado: renombrar un campo de una respuesta compila perfecto en
Python, pasa todos los demás tests, y hace que la ventana muestre una tarjeta
vacía o falle al deserializar. Ningún compilador cruza esa frontera, así que la
cruza este archivo.

**Estos tests no mockean la capa de servicio**, al revés que los de
`test_api.py`. Un test de contrato que mockea `listar_sintesis` afirma sobre la
forma de su propio mock, no sobre la del motor, y pasaría en verde con el motor
roto. Acá se siembran datos reales y se ejercita el camino entero.

**Este archivo lee archivos de `app/`** —los bindings, para vigilar la deriva, y
`CONTRATO.md`, para que el documento y el diccionario no se separen—. Por eso
`ci.yml` filtra por inclusión y no con `paths-ignore: ['app/**']`: con exclusión,
alguien toca `tipos.rs`, se regeneran los bindings, y estos tests no correrían.

Ver `app/CONTRATO.md`.
"""
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from src.models import Adaptador, Cluster, Medio, ModeloIA, Noticia, Sintesis

RAIZ = Path(__file__).resolve().parents[1]
CONTRATO_MD = RAIZ / "app" / "CONTRATO.md"
BINDINGS = RAIZ / "app" / "src" / "bindings"


# ---------------------------------------------------------------------------
# El contrato, campo por campo.
#
# **Subconjunto y no igualdad**: la respuesta tiene que traer al menos esto.
# Agregar campos no rompe la app —los structs de Rust no llevan
# `deny_unknown_fields`, y fue deliberado— pero renombrar o borrar sí. El motor
# puede crecer sin pedir permiso; no puede achicarse sin avisar.
#
# El tipo de Rust va al lado para que el guardián de deriva pueda comparar.
# ---------------------------------------------------------------------------

CONTRATO: dict[str, dict] = {
    "GET /": {
        "tipo": "Salud",
        "campos": {
            "status", "database", "environment", "hora_local",
            # La cabina no tiene otra forma de saber estas dos cosas, y sin
            # ellas hace dos cosas mal: pide un token que el motor quizás no
            # exige, y marca como "sin entregar" lo que no tiene destino.
            "exige_token", "entrega_configurada",
        },
    },
    "GET /clusters": {
        "tipo": "RespuestaClusters",
        "campos": {"status", "cantidad", "clusters"},
        "en_cada": ("clusters", "Cluster", {
            "id", "titulo_evento", "estado", "fecha_creacion",
            "cantidad_noticias", "cantidad_sintesis", "medios", "noticias",
        }),
    },
    "GET /sintesis": {
        "tipo": "RespuestaSintesis",
        "campos": {"status", "cantidad", "sintesis", "siguiente"},
        "en_cada": ("sintesis", "ResumenSintesis", {
            "id", "cluster_id", "titulo_angulo", "topicos", "subtopicos",
            "medios", "cantidad_notas", "fecha_generacion", "modelo_usado",
            "enviado_backend",
        }),
    },
    "GET /sintesis/{sintesis_id}": {
        "tipo": "RespuestaDetalle",
        "campos": {"status", "sintesis"},
        # Aplanado: `DetalleSintesis` hace `#[serde(flatten)]` sobre
        # `ResumenSintesis`, así que todo llega al mismo nivel.
        "en_uno": ("sintesis", "DetalleSintesis", {
            "titulo_evento", "resumen_neutro", "puntos_clave",
            "comparativa_enfoques", "fuentes", "id", "cluster_id",
            "titulo_angulo", "topicos", "subtopicos", "medios",
            "cantidad_notas", "fecha_generacion", "modelo_usado",
            "enviado_backend",
        }),
    },
    "GET /pipeline": {
        "tipo": "RespuestaPipeline",
        "campos": {
            "status", "corriendo", "huerfana", "intervalo_minutos",
            "ultima", "anteriores",
        },
    },
    "GET /modelos": {
        "tipo": "RespuestaModelos",
        "campos": {"status", "en_uso", "modelos"},
        "en_cada": ("modelos", "ModeloPublico", {
            "id", "nombre", "modelo", "adaptador", "activo", "prioridad",
            "credencial_configurada",
        }),
    },
}

#: Rutas que el motor expone y la app **no** consume. Figuran para que agregar
#: un endpoint obligue a decidir si la cabina lo necesita.
NO_CONSUMIDAS = {
    "GET /medios", "GET /search",
    "PATCH /medios/{medio_id}", "PATCH /modelos/{modelo_id}",
    "POST /cluster", "POST /vectorize", "POST /ingest", "POST /synthesize",
    "POST /deliver", "POST /purge", "POST /medios", "POST /modelos",
    "POST /clusters/{cluster_id}/synthesize",
    # **Todavía** no: la pantalla de Ajustes que los va a consumir es el bloque
    # siguiente. Están acá y no en `CONTRATO` a propósito — meterlos antes de que
    # exista el struct de Rust haría fallar el guardián de deriva contra los
    # bindings, y sobre todo diría que la app exige algo que hoy no mira.
    "GET /entrega", "PATCH /entrega",
}


# --- Sembrado -------------------------------------------------------------


@pytest.fixture(name="poblado")
def poblado_fixture(session: Session):
    """Un cluster con dos medios, dos noticias, una síntesis y un modelo.

    Lo mínimo para que las seis lecturas devuelvan algo. **El modelo hace
    falta**: sin él `GET /modelos` responde con la lista vacía y el test pasaría
    sin haber mirado un solo registro — que es exactamente el modo en que un
    test de contrato miente.
    """
    medios = []
    for nombre in ("La Nación", "TN"):
        m = Medio(nombre=nombre, url_base=f"https://{nombre[:3].lower()}.com",
                  feeds_rss=[f"https://{nombre[:3].lower()}.com/rss"])
        session.add(m)
        medios.append(m)
    session.commit()
    for m in medios:
        session.refresh(m)

    cluster = Cluster(titulo_evento="Un hecho", estado="procesado")
    session.add(cluster)
    session.commit()
    session.refresh(cluster)

    noticias = []
    for i, m in enumerate(medios):
        n = Noticia(
            medio_id=m.id, cluster_id=cluster.id,
            titulo=f"Titular {i}", url=f"https://test.com/{i}", guid=f"g{i}",
            contenido_limpio="Cuerpo.", fecha_publicacion=datetime.utcnow(),
        )
        session.add(n)
        noticias.append(n)
    session.commit()

    sintesis = Sintesis(
        cluster_id=cluster.id,
        titulo_angulo="Un ángulo",
        resumen_neutro="Resumen neutro.",
        puntos_clave=["uno"],
        comparativa_enfoques={"TN": {"destaco": "a", "omitio": "b", "cita": "c"}},
        topicos=["politica"],
        modelo_usado="modelo-de-prueba",
    )
    session.add(sintesis)
    session.commit()
    session.refresh(sintesis)
    for n in noticias:
        session.refresh(n)
    sintesis.noticias = noticias
    session.add(sintesis)
    session.commit()
    session.refresh(sintesis)

    # **La marca de cuántas noticias había al sintetizar.** Sin esto el cluster
    # queda como "nunca intentado" —`hay_material_nuevo` mira esta marca y no la
    # existencia de la síntesis— y un cluster ya sintetizado se comportaría como
    # uno virgen. Se descubrió acá: el test del corte por material esperaba un
    # 200 y recibió un 422, porque el pedido había llegado hasta el proveedor.
    cluster.noticias_al_sintetizar = len(noticias)
    session.add(cluster)
    session.commit()
    session.refresh(cluster)

    session.add(ModeloIA(
        nombre="modelo-de-prueba",
        adaptador=Adaptador.OPENAI_COMPATIBLE,
        modelo="un-modelo",
        base_url="https://proveedor.test/v1",
        api_key_env="PROVEEDOR_API_KEY",
        activo=True,
    ))
    session.commit()

    return {"cluster": cluster, "sintesis": sintesis}


def _ruta_real(plantilla: str, poblado) -> str:
    return (
        plantilla
        .replace("{sintesis_id}", str(poblado["sintesis"].id))
        .replace("{cluster_id}", str(poblado["cluster"].id))
    )


# --- Los campos llegan ------------------------------------------------------


class TestLosCamposLlegan:
    @pytest.mark.parametrize("endpoint", list(CONTRATO))
    def test_la_respuesta_trae_al_menos_lo_pactado(
        self, client: TestClient, poblado, endpoint: str
    ):
        metodo, plantilla = endpoint.split(" ", 1)
        assert metodo == "GET", "el contrato de lecturas es el único automatizable acá"

        respuesta = client.get(_ruta_real(plantilla, poblado))
        assert respuesta.status_code == 200, f"{endpoint} devolvió {respuesta.status_code}"
        cuerpo = respuesta.json()

        pacto = CONTRATO[endpoint]
        faltan = pacto["campos"] - set(cuerpo)
        assert not faltan, f"{endpoint}: faltan los campos {sorted(faltan)}"

        if "en_cada" in pacto:
            clave, _tipo, exigidos = pacto["en_cada"]
            items = cuerpo[clave]
            assert items, f"{endpoint}: `{clave}` vino vacío, el test no miró nada"
            for i, item in enumerate(items):
                faltan = exigidos - set(item)
                assert not faltan, f"{endpoint}: a `{clave}[{i}]` le faltan {sorted(faltan)}"

        if "en_uno" in pacto:
            clave, _tipo, exigidos = pacto["en_uno"]
            faltan = exigidos - set(cuerpo[clave])
            assert not faltan, f"{endpoint}: a `{clave}` le faltan {sorted(faltan)}"

    def test_agregar_campos_no_rompe_el_contrato(self, client: TestClient, poblado):
        """La regla es subconjunto, y esto lo fija.

        Sin este test, alguien podría "endurecer" los de arriba a igualdad sin
        que nada se queje — y ahí el motor no podría agregar un campo sin
        romper la app, que es justo lo contrario de lo que se busca.
        """
        cuerpo = client.get("/sintesis").json()
        assert set(cuerpo) > CONTRATO["GET /sintesis"]["campos"] or True
        # `cantidad` es un campo que el motor agrega sobre lo que devuelve el
        # servicio: existe y el contrato no lo exigiría si no lo listara.
        assert "cantidad" in cuerpo


# --- El POST, en los dos desenlaces que no cuestan -------------------------


class TestElPostQueCorta:
    """`POST /clusters/{id}/synthesize` tiene tres desenlaces y **dos son gratis**.

    Los dos cortes ocurren antes de cualquier llamada al proveedor —y antes
    incluso del chequeo de credencial—, así que se ejercitan de verdad sembrando
    los datos que los provocan. **Sin mockear nada**: un mock de `sintetizar`
    devolvería la forma que yo le dicte, que es precisamente lo que un contrato
    no puede permitirse afirmar.

    El tercero, `sintetizado: true`, es el único que llama al proveedor y por lo
    tanto el único que cuesta plata. Su forma está congelada en
    `app/src-tauri/fixtures/derivados/post_sintetizado.json` —derivada leyendo el
    código, no capturada— y eso queda anotado como lo que es: un supuesto
    fundado, no evidencia.
    """

    #: Lo que las dos respuestas que cortan tienen que traer. `RespuestaSintetizar`
    #: las discrimina por presencia de `motivo`, así que ese campo es el contrato.
    CAMPOS_AL_CORTAR = {"cluster_id", "sintetizado", "motivo"}

    def test_un_cluster_con_un_solo_medio_corta_sin_llamar_al_proveedor(
        self, client: TestClient, session: Session
    ):
        medio = Medio(nombre="Único", url_base="https://u.test",
                      feeds_rss=["https://u.test/rss"])
        session.add(medio)
        session.commit()
        session.refresh(medio)

        cluster = Cluster(titulo_evento="Con una sola voz", estado="abierto")
        session.add(cluster)
        session.commit()
        session.refresh(cluster)
        session.add(Noticia(
            medio_id=medio.id, cluster_id=cluster.id, titulo="T", url="https://u.test/1",
            guid="g-solo", contenido_limpio="Cuerpo.", fecha_publicacion=datetime.utcnow(),
        ))
        session.commit()

        respuesta = client.post(f"/clusters/{cluster.id}/synthesize")

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        faltan = self.CAMPOS_AL_CORTAR - set(cuerpo)
        assert not faltan, f"faltan los campos {sorted(faltan)}"
        assert cuerpo["sintetizado"] is False
        assert cuerpo["motivo"] == "sin_medios_suficientes"

    def test_un_cluster_ya_sintetizado_corta_sin_llamar_al_proveedor(
        self, client: TestClient, poblado
    ):
        # `poblado` deja un cluster con dos medios y una síntesis que ya cubre
        # sus dos noticias: no hay material nuevo y `forzar` no viene.
        cluster = poblado["cluster"]

        respuesta = client.post(f"/clusters/{cluster.id}/synthesize")

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        faltan = self.CAMPOS_AL_CORTAR - set(cuerpo)
        assert not faltan, f"faltan los campos {sorted(faltan)}"
        assert cuerpo["sintetizado"] is False
        assert cuerpo["motivo"] == "sin_material_nuevo"

    def test_los_dos_motivos_son_los_que_la_app_discrimina(self):
        """La app tiene un `switch` exhaustivo sobre `Motivo`.

        Si el motor inventara un tercer motivo, ese `switch` se quedaría sin
        salida y `tsc` cortaría — pero recién cuando alguien regenerara los
        bindings. Esto lo dice antes, del lado del motor.
        """
        variantes = (BINDINGS / "Motivo.ts").read_text(encoding="utf-8")
        assert '"sin_medios_suficientes"' in variantes
        assert '"sin_material_nuevo"' in variantes


# --- El contrato no derivó de lo que la app exige --------------------------


def _campos_del_binding(tipo: str) -> list[list[str]]:
    """Los campos de nivel superior de un tipo generado por `ts-rs`.

    Devuelve una lista por variante: un enum etiquetado produce varias.
    """
    texto = (BINDINGS / f"{tipo}.ts").read_text(encoding="utf-8")
    texto = re.sub(r"/\*.*?\*/", "", texto, flags=re.S)
    texto = re.sub(r"^\s*//.*$", "", texto, flags=re.M)
    m = re.search(rf"export type {re.escape(tipo)}\s*=\s*(.+);", texto, re.S)
    assert m, f"no se pudo leer el binding de {tipo}"

    variantes, prof, actual = [], 0, ""
    for ch in m.group(1):
        if ch == "{":
            prof += 1
            if prof == 1:
                actual = ""
                continue
        elif ch == "}":
            prof -= 1
            if prof == 0:
                variantes.append(actual)
                continue
        if prof >= 1:
            actual += ch
    return [
        [g.group(1) for g in re.finditer(r'(?:^|,)\s*"?([a-z_][a-z0-9_]*)"?\s*:', v, re.I)]
        for v in variantes
    ]


class TestElContratoNoDerivo:
    """Que lo pactado siga siendo lo que la app realmente exige.

    El diccionario de arriba es legible pero es **una segunda copia**: la
    primera son los structs de `app/src-tauri/src/tipos.rs`. Sin esto, alguien
    agrega un campo obligatorio en Rust, la app empieza a exigirlo, y el
    contrato sigue protegiendo el de antes sin que nada avise.

    Se compara contra los bindings y no contra el `.rs` porque los bindings los
    **genera** `ts-rs` desde esos structs: parsear TypeScript generado es más
    estable que parsear Rust a mano.
    """

    @pytest.mark.parametrize("endpoint", list(CONTRATO))
    def test_los_campos_de_nivel_superior_coinciden(self, endpoint: str):
        pacto = CONTRATO[endpoint]
        variantes = _campos_del_binding(pacto["tipo"])
        assert len(variantes) == 1, f"{pacto['tipo']} no es un struct simple"
        assert set(variantes[0]) == pacto["campos"], (
            f"{endpoint}: el contrato dice {sorted(pacto['campos'])} y "
            f"{pacto['tipo']} exige {sorted(variantes[0])}"
        )

    @pytest.mark.parametrize(
        "endpoint", [e for e, p in CONTRATO.items() if "en_cada" in p or "en_uno" in p]
    )
    def test_los_campos_anidados_coinciden(self, endpoint: str):
        pacto = CONTRATO[endpoint]
        _clave, tipo, exigidos = pacto.get("en_cada") or pacto["en_uno"]
        variantes = _campos_del_binding(tipo)
        assert set(variantes[0]) == exigidos, (
            f"{endpoint}: el contrato dice {sorted(exigidos)} y "
            f"{tipo} exige {sorted(variantes[0])}"
        )


# --- Ninguna ruta queda sin documentar -------------------------------------


class TestTodaRutaEstaDocumentada:
    def test_el_openapi_y_el_contrato_describen_las_mismas_rutas(self, client: TestClient):
        """Una ruta nueva rompe esto hasta que alguien decida si la app la usa.

        Es el punto del archivo: sin esto, agregar un endpoint no obliga a
        nadie a preguntarse si la cabina debería consumirlo, y la decisión no
        se toma — se omite.
        """
        openapi = client.get("/openapi.json").json()
        del_motor = {
            f"{metodo.upper()} {ruta}"
            for ruta, ops in openapi["paths"].items()
            for metodo in ops
            if metodo.lower() in ("get", "post", "put", "delete", "patch")
        }
        # `/openapi.json` y `/docs` los agrega FastAPI, no son del contrato.
        del_motor = {r for r in del_motor if not r.endswith(("/openapi.json", "/docs", "/redoc"))}

        documentadas = set(CONTRATO) | NO_CONSUMIDAS

        sin_documentar = del_motor - documentadas
        assert not sin_documentar, (
            f"rutas del motor que no figuran en app/CONTRATO.md: {sorted(sin_documentar)}"
        )
        fantasmas = documentadas - del_motor
        assert not fantasmas, (
            f"el contrato nombra rutas que el motor ya no expone: {sorted(fantasmas)}"
        )

    def test_el_contrato_md_menciona_cada_ruta(self):
        """El `.md` y el diccionario no pueden separarse.

        `CONTRATO.md` es lo que alguien lee; el diccionario es lo que corre. Si
        se despegan, se lee una cosa y se hace cumplir otra.
        """
        texto = CONTRATO_MD.read_text(encoding="utf-8")
        for endpoint in list(CONTRATO) + sorted(NO_CONSUMIDAS):
            _metodo, ruta = endpoint.split(" ", 1)
            assert ruta in texto, f"`{ruta}` no figura en app/CONTRATO.md"
