"""
Tests del desacoplamiento del modelo de IA (backlog punto 2, etapa 1).

Ningún test sale a internet: la frontera que se mockea es `httpx.post`, igual
que `_descargar_feed` en la ingesta.
"""
import json
from contextlib import contextmanager
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from src.main import HISTORICO
from src.models import Adaptador, ModeloIA, ModoEstructura
from src.services import modelos, synthesis
from src.services.proveedores import (
    PREFIJO_API_KEY_ENV,
    ErrorDeProveedor,
    OpenAICompatible,
    ProveedorNoConfigurado,
    RespuestaBloqueada,
)
from src.services.proveedores.openai_compatible import TIMEOUT_SONDEO_SEGUNDOS

ENV_OK = f"{PREFIJO_API_KEY_ENV}TEST"

# Lo mínimo que `_valida_la_forma` acepta como "respetó el esquema".
FORMA_OK = json.dumps({"angulos": []})


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(ENV_OK, "clave-de-prueba")


def _modelo(**kwargs) -> ModeloIA:
    datos = {
        "nombre": "prueba",
        "adaptador": Adaptador.OPENAI_COMPATIBLE,
        "modelo": "un-modelo",
        "base_url": "https://proveedor.test/v1",
        "api_key_env": ENV_OK,
    }
    datos.update(kwargs)
    return ModeloIA(**datos)


def _respuesta(contenido: str, status: int = 200, finish: str = "stop") -> httpx.Response:
    cuerpo = {
        "choices": [{"finish_reason": finish, "message": {"content": contenido}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    return httpx.Response(
        status_code=status, json=cuerpo, request=httpx.Request("POST", "https://x.test/")
    )


class TestCredenciales:
    """
    La key sale del entorno y nunca de la base, y el nombre de la variable no
    es libre.
    """

    def test_lee_la_key_del_entorno(self, key):
        assert OpenAICompatible(_modelo()).api_key == "clave-de-prueba"

    @pytest.mark.parametrize(
        "nombre",
        [
            # **La vía de exfiltración que el prefijo existe para cerrar.** Sin
            # él, una fila con `base_url` apuntando a un servidor propio y esto
            # en `api_key_env` haría que el motor mande el secreto como Bearer.
            "WEBHOOK_SECRET",
            "DATABASE_URL",
            "GEMINI_API_KEY",
            "PATH",
            "",
            # Los adversariales, y son los que importan: los de arriba fallan
            # bajo *cualquier* chequeo concebible, así que no discriminan entre
            # una comprobación correcta y una rota. Estos tres distinguen
            # `startswith` de `in`, de un `find` sin anclar y de una comparación
            # sin distinguir mayúsculas.
            f"X_{PREFIJO_API_KEY_ENV}Y",
            PREFIJO_API_KEY_ENV.lower() + "x",
            PREFIJO_API_KEY_ENV.rstrip("_") + "X",
        ],
    )
    def test_no_deja_nombrar_cualquier_variable_del_entorno(self, monkeypatch, nombre):
        monkeypatch.setenv(nombre or "VACIO", "secreto-que-no-debe-salir")
        with pytest.raises(ProveedorNoConfigurado, match=PREFIJO_API_KEY_ENV):
            OpenAICompatible(_modelo(api_key_env=nombre))

    def test_avisa_si_la_variable_no_esta_definida(self, monkeypatch):
        monkeypatch.delenv(ENV_OK, raising=False)
        with pytest.raises(ProveedorNoConfigurado, match="no está definida"):
            OpenAICompatible(_modelo())

    def test_una_variable_vacia_cuenta_como_no_definida(self, monkeypatch):
        monkeypatch.setenv(ENV_OK, "")
        with pytest.raises(ProveedorNoConfigurado):
            OpenAICompatible(_modelo())

    def test_tambien_la_lee_del_env(self, monkeypatch, tmp_path):
        """
        **El `.env` tiene que funcionar**, porque es donde cualquiera va a poner
        la key y donde `.env.example` dice que va.

        `pydantic-settings` lee ese archivo para llenar los campos que `Settings`
        declara, pero **no lo vuelca a `os.environ`**. Y los nombres
        `MODELO_API_KEY_*` los inventa el operador, así que no hay campo que los
        declare: sin la lectura explícita, ponerla ahí no hacía nada y el error
        era desconcertante.
        """
        monkeypatch.delenv(ENV_OK, raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(f"{ENV_OK}=desde-el-archivo\n", encoding="utf-8")

        assert OpenAICompatible(_modelo()).api_key == "desde-el-archivo"

    def test_el_entorno_real_le_gana_al_env(self, monkeypatch, tmp_path):
        """
        La precedencia que espera cualquiera: en producción la credencial la
        inyecta Docker o el orquestador, y un `.env` olvidado en la imagen no
        tiene que pisarla.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(f"{ENV_OK}=la-del-archivo\n", encoding="utf-8")
        monkeypatch.setenv(ENV_OK, "la-del-entorno")

        assert OpenAICompatible(_modelo()).api_key == "la-del-entorno"

    def test_la_key_viaja_en_el_header(self, key):
        """
        Sin esto se podía mandar el header sin la key y ningún test protestaba:
        todo lo demás está mockeado, así que la petición "funcionaba" igual.
        """
        with patch.object(httpx, "post", return_value=_respuesta(FORMA_OK)) as post:
            OpenAICompatible(_modelo()).probar("hola", _EsquemaChico)

        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer clave-de-prueba"


class TestValidacionDeBaseUrl:
    """
    `base_url` es una URL arbitraria que el motor va a pedir, desde un endpoint
    sin autenticación. Es el primer SSRF del repo.
    """

    def test_base_url_es_obligatorio(self, key):
        with pytest.raises(ErrorDeProveedor, match="base_url"):
            OpenAICompatible(_modelo(base_url=None))

    @pytest.mark.parametrize(
        "url, motivo",
        [
            ("file:///etc/passwd", "esquema no http, la vía clásica a archivos"),
            ("gopher://x.test/1", "esquema exótico"),
            ("proveedor.test/v1", "sin esquema"),
            ("https://user:hunter2@proveedor.test/v1", "credencial embebida"),
            ("http://169.254.169.254/latest", "metadata de nube"),
        ],
    )
    def test_rechaza_lo_que_no_puede_alcanzar(self, key, url, motivo):
        with pytest.raises(ErrorDeProveedor):
            OpenAICompatible(_modelo(base_url=url)), motivo

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434/v1",
            "http://127.0.0.1:8000/v1",
            "http://192.168.1.50:8000/v1",
        ],
    )
    def test_permite_modelos_locales_y_de_red_interna(self, key, url):
        """
        **El caso que motiva todo este backlog.** Bloquear todas las direcciones
        privadas sería lo habitual contra SSRF y dejaría afuera justamente el
        escenario donde los cuerpos de los artículos no salen de la máquina.
        """
        assert OpenAICompatible(_modelo(base_url=url)).url.endswith("/chat/completions")


class TestAdaptadorCompatible:
    def test_pide_estructura_por_response_format(self, key):
        with patch.object(httpx, "post", return_value=_respuesta(FORMA_OK)) as post:
            OpenAICompatible(_modelo(temperatura=0.7)).probar("hola", _EsquemaChico)

        cuerpo = post.call_args.kwargs["json"]
        assert cuerpo["response_format"]["type"] == "json_schema"
        assert "tools" not in cuerpo
        # El esquema y la temperatura tienen que VIAJAR, no solo estar. Antes se
        # los podía vaciar y ningún test se enteraba, porque lo único que se
        # miraba era la forma del sobre.
        assert cuerpo["response_format"]["json_schema"]["schema"] == (
            _EsquemaChico.model_json_schema()
        )
        assert cuerpo["temperature"] == 0.7
        assert cuerpo["model"] == "un-modelo"

    def test_sin_max_tokens_no_se_manda_techo(self, key):
        """El default es sin límite: un techo arbitrario trunca la síntesis."""
        with patch.object(httpx, "post", return_value=_respuesta(FORMA_OK)) as post:
            OpenAICompatible(_modelo()).probar("hola", _EsquemaChico)
        assert "max_tokens" not in post.call_args.kwargs["json"]

    def test_con_max_tokens_se_manda(self, key):
        with patch.object(httpx, "post", return_value=_respuesta(FORMA_OK)) as post:
            OpenAICompatible(_modelo(max_tokens=500)).probar("hola", _EsquemaChico)
        assert post.call_args.kwargs["json"]["max_tokens"] == 500

    def test_el_corte_por_longitud_se_dice_con_todas_las_letras(self, key):
        """
        Sin detectarlo, el JSON llega partido, falla el parseo y se reintenta
        tres veces **lo mismo**: la causa no es transitoria, es el techo puesto.
        """
        with patch.object(
            httpx, "post", return_value=_respuesta('{"angulos": [{"tit', finish="length")
        ):
            with pytest.raises(ErrorDeProveedor, match="max_tokens"):
                OpenAICompatible(_modelo(max_tokens=10)).probar("hola", _EsquemaChico)

    def test_content_como_lista_de_bloques(self, key):
        """
        El formato nativo de Anthropic, que varios gateways dejan pasar sin
        aplanar. Antes llegaba tal cual a `json.loads`, que con una lista tira
        `TypeError` —no `JSONDecodeError`—, se escapaba de todos los `except` y
        salía como HTTP 500.
        """
        cuerpo = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": [{"type": "text", "text": FORMA_OK}]},
            }]
        }
        respuesta = httpx.Response(
            200, json=cuerpo, request=httpx.Request("POST", "https://x.test/")
        )
        with patch.object(httpx, "post", return_value=respuesta):
            assert OpenAICompatible(_modelo()).probar("hola", _EsquemaChico) == FORMA_OK

    def test_pela_la_cerca_de_markdown(self, key):
        """
        Los modelos chicos —los que uno corre localmente— devuelven el JSON
        entre backticks aunque se les pida puro. Rechazarlos sería correcto en
        sentido estricto y dejaría afuera el caso de uso que motiva el backlog.
        """
        with patch.object(
            httpx, "post", return_value=_respuesta(f"```json\n{FORMA_OK}\n```")
        ):
            assert OpenAICompatible(_modelo()).probar("hola", _EsquemaChico) == FORMA_OK

    def test_en_modo_tools_manda_el_esquema_como_herramienta_forzada(self, key):
        modelo = _modelo(modo_estructura=ModoEstructura.TOOLS)
        with patch.object(httpx, "post", return_value=_respuesta(FORMA_OK)) as post:
            OpenAICompatible(modelo).probar("hola", _EsquemaChico)

        cuerpo = post.call_args.kwargs["json"]
        assert "response_format" not in cuerpo
        assert cuerpo["tools"][0]["function"]["parameters"]["type"] == "object"
        # Forzado: si no, el modelo puede contestar en prosa y perdemos la
        # estructura, que es lo único que veníamos a buscar.
        assert cuerpo["tool_choice"]["function"]["name"] == cuerpo["tools"][0]["function"]["name"]

    def test_lee_la_respuesta_venga_por_tool_call(self, key):
        cuerpo = {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {"tool_calls": [{"function": {"arguments": FORMA_OK}}]},
            }]
        }
        respuesta = httpx.Response(
            200, json=cuerpo, request=httpx.Request("POST", "https://x.test/")
        )
        with patch.object(httpx, "post", return_value=respuesta):
            assert OpenAICompatible(_modelo()).probar("hola", _EsquemaChico) == FORMA_OK

    def test_normaliza_el_bloqueo_por_filtros(self, key):
        """
        Cada proveedor lo dice a su manera y río arriba solo se entiende
        `RespuestaBloqueada`; traducirlo es responsabilidad del adaptador.
        """
        with patch.object(
            httpx, "post", return_value=_respuesta("", finish="content_filter")
        ), pytest.raises(RespuestaBloqueada):
            OpenAICompatible(_modelo()).probar("hola", _EsquemaChico)

    def test_conserva_el_mensaje_de_error_del_proveedor(self, key):
        """
        Lo lee quien está dando de alta un modelo: necesita "modelo inexistente"
        dicho por quien sabe, no una interpretación nuestra.
        """
        error = httpx.Response(
            404,
            json={"error": {"message": "model 'inventado' not found"}},
            request=httpx.Request("POST", "https://x.test/"),
        )
        with patch.object(httpx, "post", return_value=error):
            with pytest.raises(ErrorDeProveedor, match="not found"):
                OpenAICompatible(_modelo()).probar("hola", _EsquemaChico)

    def test_un_fallo_de_red_no_escapa_como_httpx(self, key):
        with patch.object(httpx, "post", side_effect=httpx.ConnectError("sin red")):
            with pytest.raises(ErrorDeProveedor, match="No se pudo hablar"):
                OpenAICompatible(_modelo()).probar("hola", _EsquemaChico)

    def test_no_refleja_el_cuerpo_de_una_respuesta_ajena(self, key):
        """
        **Lo que convierte un SSRF a ciegas en lectura de servicios internos.**
        Este texto termina en la respuesta de `POST /modelos`, que no tiene
        autenticación y acepta cualquier `base_url`: apuntabas a un servicio de
        la red interna y el 422 te devolvía lo que ese servicio contestó.

        Solo se refleja `error.message` cuando la respuesta trae la forma de
        error de OpenAI; un servicio que no la hable no filtra nada.
        """
        secreto = "token=abc123 host=vault.interno"
        interna = httpx.Response(
            403, text=secreto, request=httpx.Request("POST", "https://x.test/")
        )
        with patch.object(httpx, "post", return_value=interna):
            with pytest.raises(ErrorDeProveedor) as capturado:
                OpenAICompatible(_modelo()).probar("hola", _EsquemaChico)

        assert secreto not in str(capturado.value)
        assert "403" in str(capturado.value)


class TestSondeo:
    """
    El alta comprueba que el proveedor **respete el esquema**, no que responda.

    Es la lección medida de la capa de compatibilidad de Anthropic: responde
    200, devuelve texto correcto e ignora `response_format` en silencio.
    """

    def test_acepta_al_que_respeta_el_esquema(self, key):
        with patch.object(httpx, "post", return_value=_respuesta(FORMA_OK)):
            modo, _ = modelos.sondear(_modelo())
        assert modo == ModoEstructura.RESPONSE_FORMAT

    def test_cae_a_tools_si_response_format_no_da_la_forma(self, key):
        """El proveedor contesta bien pero ignora el esquema; el otro modo sí."""
        respuestas = [_respuesta("Claro, aquí tienes:"), _respuesta(FORMA_OK)]
        with patch.object(httpx, "post", side_effect=respuestas):
            modo, _ = modelos.sondear(_modelo())
        assert modo == ModoEstructura.TOOLS

    def test_rechaza_al_que_ignora_el_esquema_por_los_dos_caminos(self, key):
        """
        **El caso Anthropic.** Responde perfecto y nunca devuelve la forma: si
        el alta lo aceptara, el fallo aparecería recién en la síntesis.
        """
        prosa = _respuesta("Por supuesto, con gusto te ayudo.")
        with patch.object(httpx, "post", side_effect=[prosa, prosa]):
            with pytest.raises(ErrorDeProveedor, match="sin la forma pedida"):
                modelos.sondear(_modelo())

    def test_un_adaptador_inexistente_no_se_reporta_como_falta_de_esquema(self, key):
        """
        Guarda contra un diagnóstico inventado. Antes el `construir` iba dentro
        del bucle, así que un adaptador no implementado se contaba como "este
        modo no dio la forma" y salía como "el proveedor ignora el esquema" —
        una causa falsa, que es justo lo que manda a depurar al lugar equivocado.

        **La primera versión de este test no lo atrapaba**: el mensaje genérico
        incluía el detalle de cada intento, así que igual contenía "todavía no
        está" y el `match` pasaba. Por eso ahora se afirma que el mensaje
        genérico NO aparece y que no se salió a la red.
        """
        with patch.object(httpx, "post") as post:
            with pytest.raises(ErrorDeProveedor) as capturado:
                modelos.sondear(_modelo(adaptador=Adaptador.GEMINI))

        assert "todavía no está" in str(capturado.value)
        assert "No se pudo obtener la estructura" not in str(capturado.value)
        post.assert_not_called()

    def test_no_deja_pendiente_el_modo_probado_en_el_objeto_recibido(self, key):
        """
        El bucle prueba los dos modos, pero **sobre una copia**. Si mutara el
        objeto recibido y ése estuviera adjunto a una sesión, el cambio quedaría
        pendiente y se commitearía con el próximo commit aunque el sondeo
        hubiera fallado.
        """
        original = _modelo(modo_estructura=ModoEstructura.RESPONSE_FORMAT)
        prosa = _respuesta("no es JSON")
        with patch.object(httpx, "post", side_effect=[prosa, prosa]):
            with pytest.raises(ErrorDeProveedor):
                modelos.sondear(original)

        assert original.modo_estructura == ModoEstructura.RESPONSE_FORMAT

    @pytest.mark.parametrize(
        "cuerpo",
        [
            '{"angulos": null}',
            '{"angulos": "no entendí"}',
            '{"otra_cosa": []}',
            '{"angulos": {"a": 1}}',
            "[]",
            '"un string suelto"',
        ],
    )
    def test_rechaza_json_valido_con_la_forma_equivocada(self, key, cuerpo):
        """
        **El escenario más probable de un proveedor que ignora el esquema**: no
        devuelve prosa, devuelve *algún* JSON. La primera versión solo miraba
        que existiera la clave `angulos`, así que `{"angulos": null}` pasaba
        como "respetó el esquema" — el falso positivo más caro posible, porque
        es justo lo que este sondeo existe para detectar.
        """
        respuesta = _respuesta(cuerpo)
        with patch.object(httpx, "post", side_effect=[respuesta, respuesta]):
            with pytest.raises(ErrorDeProveedor):
                modelos.sondear(_modelo())

    def test_el_sondeo_usa_su_propio_timeout_corto(self, key):
        """
        Sin esto eran 120 s × 2 modos = 240 s por request, desde un endpoint sin
        autenticación y sincrónico: cuarenta pedidos a un host que no contesta
        dejaban toda la API sin responder.
        """
        with patch.object(httpx, "post", return_value=_respuesta(FORMA_OK)) as post:
            modelos.sondear(_modelo())
        assert post.call_args.kwargs["timeout"] == TIMEOUT_SONDEO_SEGUNDOS

    def test_el_error_del_proveedor_llega_entero(self, key):
        """Un modelo inexistente tiene que leerse como tal, no como otra cosa."""
        error = httpx.Response(
            404,
            json={"error": {"message": "model 'inventado' not found"}},
            request=httpx.Request("POST", "https://x.test/"),
        )
        with patch.object(httpx, "post", side_effect=[error, error]):
            with pytest.raises(ErrorDeProveedor, match="not found"):
                modelos.sondear(_modelo())

    def test_no_insiste_si_el_proveedor_bloquea(self, key):
        """Un bloqueo no dice nada del esquema, así que no se prueba el otro modo."""
        with patch.object(
            httpx, "post", return_value=_respuesta("", finish="content_filter")
        ) as post:
            with pytest.raises(ErrorDeProveedor, match="bloqueó"):
                modelos.sondear(_modelo())
        assert post.call_count == 1


class TestSeleccion:
    def test_sin_filas_activas_usa_el_camino_historico(self, session: Session):
        """
        `None` es la red de seguridad del desacoplamiento: una base que no
        configuró nada se comporta como antes de que la tabla existiera.
        """
        assert modelos.modelo_activo(session) is None

    def test_una_fila_inactiva_no_cuenta(self, session: Session):
        session.add(_modelo(activo=False))
        session.commit()
        assert modelos.modelo_activo(session) is None

    def test_con_varias_activas_gana_la_de_menor_prioridad(self, session: Session):
        session.add(_modelo(nombre="segundo", activo=True, prioridad=50))
        session.add(_modelo(nombre="primero", activo=True, prioridad=10))
        session.commit()
        assert modelos.modelo_activo(session).nombre == "primero"

    def test_un_adaptador_todavia_no_implementado_avisa(self, key):
        """
        `Adaptador` ya declara los tres, pero en la etapa 1 solo existe uno.
        Quien dé de alta un `gemini` nativo merece leer por qué no se puede.
        """
        with pytest.raises(ErrorDeProveedor, match="todavía no está"):
            modelos.construir(_modelo(adaptador=Adaptador.ANTHROPIC))


class TestFronteraConLaSintesis:
    """
    La traducción de excepciones en `llamar_modelo`.

    Los adaptadores hablan su propio vocabulario para no depender de
    `synthesis`; de este lado se traduce al que ya entienden el `retry` y
    `sintetizar_pendientes`. Que esa traducción sea correcta decide **si se
    reintenta o no**, y reintentar lo que no se arregla cuesta minutos por
    corrida.
    """

    def test_un_bloqueo_del_proveedor_llega_como_sintesis_bloqueada(self, key):
        with patch.object(
            httpx, "post", return_value=_respuesta("", finish="content_filter")
        ):
            with pytest.raises(synthesis.SintesisBloqueada):
                synthesis.llamar_modelo("prompt", _modelo())

    def test_una_credencial_faltante_no_se_reintenta(self, monkeypatch):
        """
        Simetría con el camino histórico, que excluye `SintesisSinConfigurar`
        del retry por el mismo motivo: una key que falta no aparece sola en el
        intento siguiente. Sin esto eran 3 intentos con espera creciente **por
        cluster** — con 37 clusters, minutos de sleeps puros por corrida.
        """
        monkeypatch.delenv(ENV_OK, raising=False)
        llamadas = {"n": 0}

        def contar(*a, **k):
            llamadas["n"] += 1
            raise AssertionError("no debería salir a la red")

        with patch.object(httpx, "post", side_effect=contar):
            with pytest.raises(synthesis.SintesisSinConfigurar):
                synthesis.llamar_modelo("prompt", _modelo())
        assert llamadas["n"] == 0

    def test_un_adaptador_no_implementado_tampoco_se_reintenta(self, key):
        with pytest.raises(synthesis.SintesisSinConfigurar):
            synthesis.llamar_modelo("prompt", _modelo(adaptador=Adaptador.ANTHROPIC))

    def test_un_error_transitorio_si_se_reintenta(self, key):
        """La contracara: un 429 o un JSON roto sí se arreglan solos."""
        roto = _respuesta("no es json")
        with patch.object(httpx, "post", side_effect=[roto, roto, roto]) as post:
            with pytest.raises(Exception):
                synthesis.llamar_modelo("prompt", _modelo())
        assert post.call_count == 3

    def test_sin_modelo_va_por_el_camino_historico(self):
        """
        La red de seguridad: `None` no es un caso de error, es el default de una
        base que no configuró nada.
        """
        with patch.object(synthesis, "_llamar_gemini", return_value="historico") as gemini:
            assert synthesis.llamar_modelo("prompt") == "historico"
        gemini.assert_called_once_with("prompt")


class TestEtiquetaDelModelo:
    def test_guarda_el_nombre_del_operador_y_no_el_id_del_modelo(self):
        """
        `ModeloIA` permite dos filas del mismo modelo con distinta cuenta o
        temperatura. Guardando el id del proveedor quedan indistinguibles en la
        serie histórica — justo la comparación que la columna existe para
        habilitar.
        """
        assert synthesis._etiqueta_del_modelo(_modelo(nombre="el-mio")) == "el-mio"

    def test_el_camino_historico_se_atribuye_y_no_queda_en_none(self):
        """
        `None` ya significa "síntesis anterior a que existiera la columna".
        Mezclar "no sé" con "Gemini de siempre" arruinaría cualquier consulta.
        """
        etiqueta = synthesis._etiqueta_del_modelo(None)
        assert etiqueta.startswith("historico:")
        assert etiqueta != "None"


@contextmanager
def contar_lecturas_de(session: Session, tabla: str):
    """
    Cuenta los SELECT contra una tabla puntual.

    `conftest.contar_queries` cuenta todo, y acá eso no sirve: la síntesis hace
    decenas de queries legítimas por cluster y el número que importa es cuántas
    veces se lee **una** tabla.
    """
    engine = session.get_bind()
    contador = {"n": 0}

    def _contar(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and tabla in statement:
            contador["n"] += 1

    event.listen(engine, "before_cursor_execute", _contar)
    try:
        yield contador
    finally:
        event.remove(engine, "before_cursor_execute", _contar)


class TestNoEscalaConLosClusters:
    def test_resolver_el_modelo_no_cuesta_una_query_por_cluster(self, session: Session):
        """
        `expire_on_commit` está en `True` y `sintetizar_cluster` commitea al
        final de cada cluster, así que un `ModeloIA` adjunto se expira y el
        primer acceso a un atributo suyo en el cluster siguiente lo recarga:
        **una query por cluster**. El `expunge` de `sintetizar_pendientes` es lo
        que lo evita.

        Es la misma trampa que el repo ya documentó en
        `descartar_vencidos_sin_sintetizar`.

        **Va por `sintetizar_pendientes` de verdad, no por una simulación.** La
        primera versión de este test hacía su propio `expunge` y contaba: o sea
        que probaba que `expunge` funciona —cosa de SQLAlchemy, no nuestra— y
        no que la función lo llame. Sacando el `expunge` del código, pasaba
        igual.
        """
        from src.models import Medio
        from tests.test_synthesis import crear_cluster, crear_noticia

        # Dos medios, que es el mínimo para que un cluster sea publicable.
        medios = []
        for nombre in ("Uno", "Dos"):
            m = Medio(nombre=nombre, url_base=f"https://{nombre.lower()}.test",
                      feeds_rss=[f"https://{nombre.lower()}.test/rss"])
            session.add(m)
            medios.append(m)
        session.commit()

        session.add(_modelo(activo=True))
        n = 0
        for _ in range(3):
            cluster = crear_cluster(session)
            for medio in medios:
                # `n` tiene que ser único en todo el test: de él sale la URL,
                # que tiene índice único.
                crear_noticia(session, medio, n, cluster)
                n += 1
        session.commit()

        respuesta = synthesis.RespuestaSintesis(angulos=[])
        with patch.object(synthesis, "llamar_modelo", return_value=respuesta):
            with contar_lecturas_de(session, "modelo_ia") as lecturas:
                stats = synthesis.sintetizar_pendientes(session)

        assert stats["sintetizados"] == 3, "el test no está ejercitando el bucle"
        # Una sola lectura de `modelo_ia` para toda la corrida, no una por cluster.
        assert lecturas["n"] == 1, (
            f"el modelo se recarga en cada commit: {lecturas['n']} lecturas "
            f"para 3 clusters"
        )


class TestEndpoints:
    """
    Los tres endpoints, que son **toda la superficie de seguridad nueva** y
    hasta la revisión no tenían un solo test.
    """

    def _alta(self, **kwargs) -> dict:
        datos = {
            "nombre": "prueba",
            "adaptador": "openai_compatible",
            "modelo": "un-modelo",
            "base_url": "https://proveedor.test/v1",
            "api_key_env": ENV_OK,
        }
        datos.update(kwargs)
        return datos

    def test_el_listado_no_publica_api_key_env_ni_base_url(self, client, session, key):
        """
        **La cadena de exfiltración que esto corta.** El listado le decía a
        cualquiera qué variable nombrar; con eso más un `base_url` propio, el
        motor le entregaba la key del operador en el sondeo mismo.
        """
        session.add(_modelo(activo=True))
        session.commit()

        respuesta = client.get("/modelos")
        assert respuesta.status_code == 200
        fila = respuesta.json()["modelos"][0]

        assert "api_key_env" not in fila
        assert "base_url" not in fila
        assert ENV_OK not in respuesta.text
        assert "proveedor.test" not in respuesta.text
        # Lo que el operador sí necesita saber, sin decir cuál es la variable.
        assert fila["credencial_configurada"] is True

    def test_sin_modelos_activos_informa_el_camino_historico(self, client, session):
        assert client.get("/modelos").json()["en_uso"] == HISTORICO

    def test_el_alta_sondea_antes_de_guardar(self, client, session, key):
        with patch.object(httpx, "post", return_value=_respuesta(FORMA_OK)):
            respuesta = client.post("/modelos", json=self._alta())

        assert respuesta.status_code == 200
        assert respuesta.json()["modelo"]["modo_estructura"] == "response_format"
        assert session.exec(select(ModeloIA)).one().nombre == "prueba"

    def test_el_alta_rechaza_al_que_no_respeta_el_esquema(self, client, session, key):
        prosa = _respuesta("Claro, con gusto.")
        with patch.object(httpx, "post", side_effect=[prosa, prosa]):
            respuesta = client.post("/modelos", json=self._alta())

        assert respuesta.status_code == 422
        # Y no se guardó nada: un modelo que no sirve no queda dando vueltas.
        assert session.exec(select(ModeloIA)).all() == []

    @pytest.mark.parametrize(
        "campo, valor",
        [
            ("api_key_env", "WEBHOOK_SECRET"),
            ("base_url", "http://169.254.169.254/latest"),
            ("base_url", "file:///etc/passwd"),
            ("base_url", "https://user:hunter2@proveedor.test/v1"),
        ],
    )
    def test_el_alta_rechaza_configuraciones_peligrosas(
        self, client, session, key, campo, valor
    ):
        with patch.object(httpx, "post") as post:
            respuesta = client.post("/modelos", json=self._alta(**{campo: valor}))

        assert respuesta.status_code == 422
        post.assert_not_called()

    @pytest.mark.parametrize(
        "campo, valor", [("temperatura", 9999.0), ("temperatura", -1), ("max_tokens", 0)]
    )
    def test_el_alta_valida_los_rangos(self, client, session, key, campo, valor):
        respuesta = client.post("/modelos", json=self._alta(**{campo: valor}))
        assert respuesta.status_code == 422

    def test_el_alta_duplicada_da_409(self, client, session, key):
        session.add(_modelo(nombre="prueba"))
        session.commit()
        with patch.object(httpx, "post") as post:
            respuesta = client.post("/modelos", json=self._alta())
        assert respuesta.status_code == 409
        post.assert_not_called()

    def test_activar_sin_credencial_no_deja_el_motor_roto(
        self, client, session, monkeypatch
    ):
        """
        El sondeo pasó alguna vez, pero la variable pudo desaparecer después —
        un redeploy, otra máquina. Activar a ciegas dejaba la síntesis fallando
        cada 15 minutos.
        """
        monkeypatch.delenv(ENV_OK, raising=False)
        session.add(_modelo())
        session.commit()
        fila = session.exec(select(ModeloIA)).one()

        respuesta = client.patch(f"/modelos/{fila.id}?activo=true")
        assert respuesta.status_code == 422
        session.refresh(fila)
        assert fila.activo is False

    def test_apagar_todos_vuelve_al_camino_historico(self, client, session, key):
        session.add(_modelo(activo=True))
        session.commit()
        fila = session.exec(select(ModeloIA)).one()

        respuesta = client.patch(f"/modelos/{fila.id}?activo=false")
        assert respuesta.status_code == 200
        assert respuesta.json()["en_uso"] == HISTORICO

    def test_patch_a_un_id_inexistente(self, client, session):
        assert client.patch("/modelos/9999?activo=true").status_code == 404


# Esquema chico para no arrastrar `RespuestaSintesis` a los tests del adaptador:
# lo que se prueba acá es el sobre, no el contenido.
from pydantic import BaseModel  # noqa: E402


class _EsquemaChico(BaseModel):
    angulos: list[str] = []
