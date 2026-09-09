//! La forma de lo que el motor devuelve.
//!
//! **Estos structs son el contrato, y son estrictos a propósito.** Un campo que
//! el motor renombre o borre hace fallar el parseo acá, en la frontera, con un
//! mensaje que dice cuál falta — en vez de llegar a la pantalla como un hueco
//! que hay que rastrear hasta el otro lado. Lo que sí se tolera es que el motor
//! **agregue** campos: no hay `deny_unknown_fields`, así que sumar uno no rompe
//! nada. Es la misma semántica de subconjunto que el test de contrato del lado
//! del motor va a afirmar.
//!
//! **Ninguna forma de acá se dedujo leyendo el código.** Salieron de respuestas
//! reales, congeladas en `fixtures/`, y los tests de abajo las deserializan.
//! La captura corrigió cuatro suposiciones — ver `fixtures/README.md`.
//!
//! Los `Option<T>` **no son por las dudas**: cada uno corresponde a un campo que
//! el motor puede emitir en `null`, verificado contra el modelo o contra la
//! función que arma el diccionario. Donde el fixture muestra un valor pero el
//! código admite `null`, manda el código.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Exige que la clave **esté**, aunque su valor sea `null`.
///
/// Serde trata todo `Option<T>` como opcional: la clave ausente da `None` sin
/// chistar. Eso está bien cuando `None` quiere decir "sin valor" y se ve como
/// un hueco en la pantalla — pero **no cuando `None` significa algo**:
///
/// - `siguiente: None` significa *"última página"*. Si el motor renombrara ese
///   campo, el feed se cortaría en la primera página y nada fallaría.
/// - `ultima: None` significa *"el motor nunca corrió"*.
/// - `fin: None` significa *"la corrida no terminó"*.
///
/// En los tres, la ausencia se leería como una afirmación falsa en vez de como
/// un dato faltante. Ahí se exige la clave; en los demás `Option` alcanza el
/// comportamiento normal de serde.
///
/// El truco es que un `deserialize_with` explícito le saca a serde el permiso
/// de usar un default, así que la clave ausente pasa a ser un error. `ts-rs` no
/// entiende ese atributo y avisa que lo ignora: está bien que lo ignore, no
/// cambia el tipo de TypeScript. Cada uso cuesta un warning en el build, y por
/// eso son tres y no ocho.
fn anulable<'de, D, T>(d: D) -> Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(d)
}

/// Los ids del motor están acotados a `MAX_ID = 2**31 - 1`, así que entran en
/// 32 bits. Importa: en 64 `ts-rs` generaría `bigint`, y `JSON.parse` no
/// devuelve `bigint` — el tipo de TypeScript estaría mintiendo.
pub type Id = u32;

// --- GET / -----------------------------------------------------------------

#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct Salud {
    pub status: String,
    pub database: String,
    pub environment: String,
    pub hora_local: String,
    /// Si el motor exige `Authorization` en el resto de sus endpoints.
    ///
    /// `API_TOKEN` es opcional del lado del motor: sin definir, la API queda
    /// abierta. Sin este campo la cabina pedía un token igual, y quien
    /// instalara contra un motor abierto tenía que inventar uno para pasar de
    /// la primera pantalla.
    pub exige_token: bool,
    /// Si hay un destino de entrega configurado.
    ///
    /// **Es un booleano y el motor no manda la URL por acá**: `GET /` es una
    /// ruta abierta. Sirve para no marcar síntesis como "sin entregar" cuando
    /// no hay a dónde entregarlas — sin él, 119 de 507 mentían.
    pub entrega_configurada: bool,
}

// --- GET /clusters ---------------------------------------------------------

#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct RespuestaClusters {
    pub status: String,
    pub cantidad: u32,
    pub clusters: Vec<Cluster>,
}

#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct Cluster {
    pub id: Id,
    pub titulo_evento: String,
    /// `abierto` | `procesado` | `descartado`. Se deja como texto y no como
    /// enum: el motor lo guarda como string libre en la columna, así que un
    /// enum cerrado acá inventaría una garantía que la base no da.
    pub estado: String,
    pub fecha_creacion: String,
    pub cantidad_noticias: u32,
    /// Cuántas síntesis tiene el cluster. **Sin `Option`**: el motor lo
    /// devuelve siempre, con `0` cuando no hay ninguna, justamente para que la
    /// ventana no tenga que distinguir "no tiene" de "no vino el campo".
    ///
    /// Este campo forma parte del contrato con el motor
    /// (`tests/test_contrato_api.py`), así que tocarlo hace correr las dos
    /// CI: la de la app por el archivo, y la del motor por el binding que se
    /// regenera.
    ///
    /// Existe porque sin él la lista de trabajo tenía que paginar
    /// `GET /sintesis` entera para saber qué cluster ya estaba resuelto.
    /// Medido el 07/09/2026 desde esta misma app: 5 páginas y 201 ms antes de
    /// dibujar una fila, creciendo a ~28 síntesis por día.
    pub cantidad_sintesis: u32,
    pub medios: Vec<String>,
    pub noticias: Vec<NoticiaBreve>,
}

#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct NoticiaBreve {
    pub id: Id,
    pub titulo: String,
    pub url: String,
    /// Sin `Option`, al revés que en `Fuente`: `listar_clusters` accede a
    /// `n.medio.nombre` sin guarda, así que si fuera `None` el motor devolvería
    /// un 500 y nunca llegaría un `null` hasta acá.
    pub medio: String,
}

// --- GET /sintesis ---------------------------------------------------------

#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct RespuestaSintesis {
    pub status: String,
    pub cantidad: u32,
    pub sintesis: Vec<ResumenSintesis>,
    /// El cursor de la página siguiente. `None` es el final de la lista, no un
    /// error: se manda tal cual vino en el próximo pedido.
    #[serde(deserialize_with = "anulable")]
    pub siguiente: Option<String>,
}

#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct ResumenSintesis {
    pub id: Id,
    pub cluster_id: Id,
    pub titulo_angulo: String,
    pub topicos: Vec<String>,
    pub subtopicos: Vec<String>,
    pub medios: Vec<String>,
    pub cantidad_notas: u32,
    pub fecha_generacion: String,
    /// `Sintesis.modelo_usado` es nullable en el modelo. El fixture lo trae
    /// lleno; el modelo manda.
    pub modelo_usado: Option<String>,
    pub enviado_backend: bool,
}

// --- GET /sintesis/{id} ----------------------------------------------------

#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct RespuestaDetalle {
    pub status: String,
    pub sintesis: DetalleSintesis,
}

/// Una síntesis entera.
///
/// El motor la arma como `{**_resumen_de_sintesis(s), ...}`, así que acá es un
/// `flatten` sobre el resumen y no una copia de sus diez campos. Copiarlos
/// habría dejado dos definiciones del mismo dato que hay que cambiar juntas.
#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct DetalleSintesis {
    #[serde(flatten)]
    #[ts(flatten)]
    pub resumen: ResumenSintesis,
    /// `None` si la síntesis quedó sin cluster: `detalle_de_sintesis` lo emite
    /// con una guarda explícita.
    pub titulo_evento: Option<String>,
    pub resumen_neutro: String,
    pub puntos_clave: Vec<String>,
    /// **Indexada por nombre de medio**, no una lista con un campo `medio`.
    /// Es lo primero que la captura corrigió: un `Vec<_>` no deserializa esto.
    pub comparativa_enfoques: HashMap<String, EnfoqueDeMedio>,
    pub fuentes: Vec<Fuente>,
}

/// Cómo cubrió un medio el evento.
///
/// Las tres claves están siempre: no vienen del modelo tal cual, las arma
/// `_validar_enfoques` construyendo el diccionario campo por campo.
#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct EnfoqueDeMedio {
    pub destaco: String,
    pub omitio: String,
    /// Frase textual del cuerpo que respalda lo anterior.
    pub cita: String,
}

#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct Fuente {
    pub id: Id,
    /// Acá sí es opcional: `detalle_de_sintesis` lo emite como
    /// `n.medio.nombre if n.medio else None`.
    pub medio: Option<String>,
    pub titulo: String,
    pub url: String,
    pub fecha_publicacion: String,
}

// --- GET /pipeline ---------------------------------------------------------

#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct RespuestaPipeline {
    pub status: String,
    pub corriendo: bool,
    /// Una corrida que quedó abierta de un proceso que murió. Se informa
    /// aparte en vez de mentir `corriendo: true` para siempre.
    pub huerfana: bool,
    pub intervalo_minutos: u32,
    /// `None` en un motor recién instalado, que todavía no corrió nunca.
    #[serde(deserialize_with = "anulable")]
    pub ultima: Option<Corrida>,
    pub anteriores: Vec<Corrida>,
}

#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct Corrida {
    pub id: Id,
    pub inicio: String,
    /// Los tres son `None` juntos mientras la corrida no cerró. No es un caso
    /// hipotético: está capturado en `pipeline.json`, en `anteriores[0]`.
    #[serde(deserialize_with = "anulable")]
    pub fin: Option<String>,
    pub duracion_segundos: Option<f64>,
    pub utilizacion: Option<f64>,
    /// **Un mapa y no un struct.** Las claves las decide `_correr_paso` en
    /// tiempo de ejecución y van en español con acentos y espacios
    /// (`síntesis`, `purga de cuerpos`, `entrega al backend`), así que fijarlas
    /// acá sería atarse a que nadie agregue un paso nunca.
    ///
    /// El tipo de TypeScript va escrito a mano y no derivado: sin esto, `ts-rs`
    /// genera un módulo `serde_json/JsonValue` **fuera de `src/bindings/`**,
    /// que es el único directorio que el guardián `bindings:check` vigila.
    #[ts(type = "Record<string, unknown>")]
    pub pasos: HashMap<String, serde_json::Value>,
}

// --- GET /modelos ----------------------------------------------------------

#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct RespuestaModelos {
    pub status: String,
    /// Nunca es `null`: `_nombre_en_uso` devuelve un centinela cuando no hay
    /// ninguno activo, justamente para no obligar a distinguir dos formas de
    /// decir lo mismo.
    pub en_uso: String,
    pub modelos: Vec<ModeloPublico>,
}

/// Un modelo, **con los campos que la app usa y no más**.
///
/// La excepción documentada a la estrictez del módulo, y tiene un motivo
/// concreto: `_vista_publica` devuelve `modelo.model_dump(exclude={...})`, o
/// sea que esta respuesta **es la tabla** menos dos columnas. Pedir todos sus
/// campos ataría la app a que nadie agregue una columna nunca — un cambio que
/// del lado del motor no se piensa como cambio de API.
///
/// Lo que queda afuera a propósito: `temperatura`, `max_tokens`,
/// `modo_estructura` y `opciones`. Son cómo se llama al proveedor, no algo que
/// la ventana muestre o decida.
#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct ModeloPublico {
    pub id: Id,
    pub nombre: String,
    pub modelo: String,
    pub adaptador: String,
    pub activo: bool,
    pub prioridad: i32,
    /// Si la variable de entorno con la credencial está seteada. **No dice
    /// cuál es**: el nombre de la variable no sale del motor desde que un
    /// mensaje de error lo filtró.
    pub credencial_configurada: bool,
}

// --- PATCH /modelos/{id} y POST /modelos -----------------------------------

/// Lo que devuelve prender o apagar un modelo.
///
/// **`modelo` es el nombre y no un objeto**, a diferencia del alta. No es un
/// descuido del motor: los dos endpoints contestan cosas distintas porque
/// responden preguntas distintas -- el alta devuelve la fila que acaba de
/// crear, y el PATCH devuelve con que quedo trabajando el motor.
#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct RespuestaActivarModelo {
    pub status: String,
    /// El nombre del modelo que se toco.
    pub modelo: String,
    pub activo: bool,
    /// Con cual esta sintetizando el motor **ahora**, que no siempre es el que
    /// se acaba de tocar: prender uno apaga a los demas, y apagar el ultimo deja
    /// al motor sin sintetizar.
    pub en_uso: String,
}

/// Lo que devuelve dar de alta un modelo.
#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct RespuestaAltaModelo {
    pub status: String,
    pub modelo: ModeloPublico,
    pub en_uso: String,
}

// --- GET /entrega y PATCH /entrega -----------------------------------------

/// A donde el motor entrega las sintesis.
///
/// **Aca si viaja la URL**, a diferencia de `Salud`: estas dos rutas exigen
/// token siempre -- incluso en un despliegue con la API abierta -- y el operador
/// no puede corregir un destino que no ve. Lo que no viaja nunca es el secreto
/// compartido; de el solo se informa si existe.
#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct Entrega {
    /// `null` cuando no hay destino: la entrega no corre y las sintesis se
    /// acumulan entregables.
    pub url: Option<String>,
    /// Hay una URL guardada.
    pub configurado: bool,
    /// El motor la va a aceptar cuando entregue. **No es lo mismo que
    /// `configurado`**: la fila puede venir sembrada por la migracion desde un
    /// `.env` viejo, o editada a mano en la base. Sin este campo el operador
    /// veia "configurado" mientras el barrido la descartaba en silencio.
    pub valido: bool,
    /// Por que no sirve, con el mensaje del validador. `null` si sirve.
    pub problema: Option<String>,
    /// Si `WEBHOOK_SECRET` esta seteado en el entorno del motor. Sin el la
    /// entrega no corre aunque haya destino, asi que es lo que explica un
    /// "configurado pero no entrega".
    pub secreto_configurado: bool,
    pub actualizado_en: Option<String>,
}

#[derive(Debug, Deserialize, Serialize, TS)]
#[ts(export, export_to = "../../src/bindings/")]
pub struct RespuestaEntrega {
    pub status: String,
    pub entrega: Entrega,
}

// --- POST /clusters/{id}/synthesize ----------------------------------------

/// Lo que llega por el cable, tal cual.
///
/// `untagged` funciona acá porque las dos formas tienen un campo obligatorio
/// que la otra no: la que corta trae `motivo`, la que hizo trabajo trae
/// `creados`. No se elige por `sintetizado`, que está en las dos.
#[derive(Debug, Deserialize)]
#[serde(untagged)]
pub enum RespuestaSintetizar {
    Cortada {
        cluster_id: Id,
        motivo: Motivo,
    },
    Hecha {
        cluster_id: Id,
        creados: u32,
        actualizados: u32,
        descartados: u32,
    },
}

/// Por qué el motor no llamó al proveedor. Categoría cerrada del lado del
/// motor también — ver el docstring de `synthesize_cluster`.
#[derive(Debug, Deserialize, Serialize, PartialEq, Eq, TS)]
#[ts(export, export_to = "../../src/bindings/")]
#[serde(rename_all = "snake_case")]
pub enum Motivo {
    /// El cluster no llega al mínimo de medios distintos, así que no podría
    /// producir ningún ángulo publicable. Ni con `forzar`.
    SinMediosSuficientes,
    /// No entraron noticias nuevas desde la última síntesis. Se sale con
    /// `forzar=true`, que es un acto explícito.
    SinMaterialNuevo,
}

/// Lo que cruza a la ventana.
///
/// **Es un enum etiquetado y no la respuesta cruda**, por lo mismo que
/// `ErrorDeApi`: un `200` que a veces significa "no hice nada" obliga a quien
/// dibuja a leer un booleano y después adivinar qué otros campos vinieron. Con
/// la etiqueta, TypeScript lo discrimina y el `switch` queda exhaustivo.
#[derive(Debug, Serialize, PartialEq, Eq, TS)]
#[ts(export, export_to = "../../src/bindings/")]
#[serde(tag = "tipo", rename_all = "snake_case")]
pub enum Sintetizado {
    Hecha {
        cluster_id: Id,
        creados: u32,
        actualizados: u32,
        descartados: u32,
    },
    Cortada {
        cluster_id: Id,
        motivo: Motivo,
    },
}

impl From<RespuestaSintetizar> for Sintetizado {
    fn from(cruda: RespuestaSintetizar) -> Self {
        match cruda {
            RespuestaSintetizar::Cortada { cluster_id, motivo } => {
                Self::Cortada { cluster_id, motivo }
            }
            RespuestaSintetizar::Hecha {
                cluster_id,
                creados,
                actualizados,
                descartados,
            } => Self::Hecha {
                cluster_id,
                creados,
                actualizados,
                descartados,
            },
        }
    }
}

#[cfg(test)]
mod pruebas {
    use super::*;

    /// Los fixtures se incrustan en el binario en vez de leerse del disco: así
    /// el test no depende del directorio desde el que se corra `cargo test`,
    /// que en este proyecto ya causó un problema una vez.
    macro_rules! capturado {
        ($nombre:literal) => {
            include_str!(concat!("../fixtures/capturados/", $nombre, ".json"))
        };
    }
    macro_rules! derivado {
        ($nombre:literal) => {
            include_str!(concat!("../fixtures/derivados/", $nombre, ".json"))
        };
    }

    #[test]
    fn salud() {
        let s: Salud = serde_json::from_str(capturado!("salud")).unwrap();
        assert_eq!(s.status, "ok");
        assert_eq!(s.database, "ok");
    }

    #[test]
    fn clusters() {
        let r: RespuestaClusters = serde_json::from_str(capturado!("clusters")).unwrap();
        assert_eq!(r.cantidad as usize, r.clusters.len());
        let primero = &r.clusters[0];
        assert!(!primero.medios.is_empty());
        // `cantidad_noticias` y el largo de `noticias` describen lo mismo: si
        // se separan, uno de los dos está mintiendo.
        assert_eq!(primero.cantidad_noticias as usize, primero.noticias.len());

        // **La captura cubre los dos casos del campo nuevo a propósito.** Un
        // fixture donde todos los clusters tuvieran síntesis no probaría que un
        // cluster sin ninguna llega como `0` y no como campo ausente, que es la
        // distinción por la que el campo existe. Si una recaptura pierde alguno
        // de los dos casos, este test lo dice.
        assert!(
            r.clusters.iter().any(|c| c.cantidad_sintesis == 0),
            "la captura tiene que incluir un cluster sin sintesis"
        );
        assert!(
            r.clusters.iter().any(|c| c.cantidad_sintesis > 0),
            "la captura tiene que incluir un cluster ya sintetizado"
        );
    }

    #[test]
    fn sintesis_con_mas_paginas() {
        let r: RespuestaSintesis = serde_json::from_str(capturado!("sintesis")).unwrap();
        assert!(r.siguiente.is_some(), "esta captura tiene mas paginas");
        assert!(!r.sintesis[0].topicos.is_empty());
    }

    #[test]
    fn la_ultima_pagina_trae_el_cursor_en_null() {
        let r: RespuestaSintesis =
            serde_json::from_str(capturado!("sintesis_ultima_pagina")).unwrap();
        assert!(r.siguiente.is_none(), "sin `siguiente` no hay mas paginas");
    }

    #[test]
    fn detalle_con_su_comparativa() {
        let r: RespuestaDetalle = serde_json::from_str(capturado!("sintesis_detalle")).unwrap();
        let d = r.sintesis;
        // El `flatten` tiene que haber traído los campos del resumen.
        assert!(!d.resumen.titulo_angulo.is_empty());
        // Y la comparativa tiene una entrada por medio que cubrió el evento.
        assert_eq!(d.comparativa_enfoques.len(), d.resumen.medios.len());
        for medio in &d.resumen.medios {
            let enfoque = d
                .comparativa_enfoques
                .get(medio)
                .unwrap_or_else(|| panic!("falta el enfoque de {medio}"));
            assert!(!enfoque.cita.is_empty());
        }
    }

    #[test]
    fn pipeline_con_una_corrida_sin_cerrar() {
        let r: RespuestaPipeline = serde_json::from_str(capturado!("pipeline")).unwrap();
        let ultima = r.ultima.expect("la captura tiene una ultima corrida");
        assert!(ultima.fin.is_some());
        assert!(ultima.pasos.contains_key("ingesta"));
        // Las claves van en español y con acentos: es lo que obliga a que
        // `pasos` sea un mapa y no un struct.
        assert!(ultima.pasos.contains_key("síntesis"));

        let sin_cerrar = &r.anteriores[0];
        assert!(sin_cerrar.fin.is_none());
        assert!(sin_cerrar.duracion_segundos.is_none());
        assert!(sin_cerrar.utilizacion.is_none());
    }

    /// La red de `anulable`, que hasta ahora no tenía ninguna.
    ///
    /// Los tres campos donde `None` **afirma algo** tienen que exigir que la
    /// clave esté. Sin la guarda, serde da `None` por defecto y la ausencia se
    /// leería como la afirmación: "no hay más páginas", "el motor nunca corrió",
    /// "la corrida no terminó". Eso estaba comprobado con una mutación de una
    /// sola vez; acá queda comprobado siempre.
    #[test]
    fn una_clave_ausente_no_se_lee_como_una_afirmacion() {
        // `siguiente` ausente NO puede significar "última página".
        let sin_siguiente = r#"{"status":"ok","cantidad":0,"sintesis":[]}"#;
        assert!(serde_json::from_str::<RespuestaSintesis>(sin_siguiente).is_err());

        // Con la clave presente y en null, sí: eso es el motor diciéndolo.
        let con_null = r#"{"status":"ok","cantidad":0,"sintesis":[],"siguiente":null}"#;
        let r: RespuestaSintesis = serde_json::from_str(con_null).unwrap();
        assert!(r.siguiente.is_none());

        // `ultima` ausente NO puede significar "el motor nunca corrió".
        let sin_ultima = r#"{"status":"ok","corriendo":false,"huerfana":false,
                             "intervalo_minutos":15,"anteriores":[]}"#;
        assert!(serde_json::from_str::<RespuestaPipeline>(sin_ultima).is_err());

        // `fin` ausente NO puede significar "la corrida no terminó".
        let sin_fin = r#"{"status":"ok","corriendo":false,"huerfana":false,
                          "intervalo_minutos":15,"anteriores":[],
                          "ultima":{"id":1,"inicio":"2026-09-06T19:50:57-03:00",
                                    "duracion_segundos":null,"utilizacion":null,
                                    "pasos":{}}}"#;
        assert!(serde_json::from_str::<RespuestaPipeline>(sin_fin).is_err());
    }

    #[test]
    fn pipeline_de_un_motor_que_nunca_corrio() {
        let r: RespuestaPipeline =
            serde_json::from_str(derivado!("pipeline_sin_corridas")).unwrap();
        assert!(r.ultima.is_none());
        assert!(r.anteriores.is_empty());
        assert!(!r.corriendo && !r.huerfana);
    }

    #[test]
    fn modelos_sin_filtrar_la_credencial() {
        let r: RespuestaModelos = serde_json::from_str(capturado!("modelos")).unwrap();
        assert!(!r.modelos.is_empty());
        assert!(!r.en_uso.is_empty(), "`en_uso` nunca viene vacio ni null");

        // Esto no prueba nuestro struct: prueba el fixture, o sea la respuesta
        // real del motor. `_vista_publica` tiene que seguir dejando afuera
        // `api_key_env` y `base_url`, y si algún día no lo hace, el fixture
        // recapturado hace fallar esto.
        let crudo = capturado!("modelos");
        assert!(!crudo.contains("api_key_env"));
        assert!(!crudo.contains("base_url"));
    }

    #[test]
    fn el_post_que_corta_por_falta_de_medios() {
        let cruda: RespuestaSintetizar =
            serde_json::from_str(capturado!("post_sin_medios")).unwrap();
        assert_eq!(
            Sintetizado::from(cruda),
            Sintetizado::Cortada {
                cluster_id: 16,
                motivo: Motivo::SinMediosSuficientes,
            }
        );
    }

    #[test]
    fn el_post_que_corta_por_falta_de_material() {
        let cruda: RespuestaSintetizar =
            serde_json::from_str(capturado!("post_sin_material")).unwrap();
        assert_eq!(
            Sintetizado::from(cruda),
            Sintetizado::Cortada {
                cluster_id: 8,
                motivo: Motivo::SinMaterialNuevo,
            }
        );
    }

    #[test]
    fn el_post_que_sintetizo() {
        let cruda: RespuestaSintetizar =
            serde_json::from_str(derivado!("post_sintetizado")).unwrap();
        assert_eq!(
            Sintetizado::from(cruda),
            Sintetizado::Hecha {
                cluster_id: 8,
                creados: 2,
                actualizados: 1,
                descartados: 1,
            }
        );
    }

    #[test]
    fn los_desenlaces_cruzan_etiquetados() {
        // El front discrimina por `tipo`, así que los nombres son contrato.
        let json = serde_json::to_string(&Sintetizado::Cortada {
            cluster_id: 8,
            motivo: Motivo::SinMaterialNuevo,
        })
        .unwrap();
        assert_eq!(
            json,
            r#"{"tipo":"cortada","cluster_id":8,"motivo":"sin_material_nuevo"}"#
        );
    }

    #[test]
    fn un_campo_de_mas_no_rompe_nada() {
        // La otra mitad de la decisión: el motor puede agregar campos sin que
        // la app se caiga. Solo renombrar o borrar tiene que doler.
        let con_extra = r#"{"status":"ok","database":"ok","environment":"development",
                            "hora_local":"2026-09-06T20:48:32-03:00",
                            "exige_token":true,"entrega_configurada":false,
                            "campo_nuevo":42}"#;
        let s: Salud = serde_json::from_str(con_extra).unwrap();
        assert_eq!(s.environment, "development");
    }
}
