#!/usr/bin/env python3
"""
Script de seed: carga los medios iniciales de la Fase 2 en la tabla `Medio`.
Es idempotente -- si un medio ya existe (por `nombre`), no lo duplica.

Requiere que el esquema ya este migrado:  python -m alembic upgrade head
Las tablas NO las crea la app al arrancar -- desde que entro Alembic, `init_db`
solo habilita pgvector y verifica que el esquema este al dia.

Ademas de crear los medios que faltan, **actualiza la lista de feeds** de los
que ya existen: es la unica forma de que un feed nuevo llegue a una base ya
cargada. El resto de los campos no se pisa.

Uso, desde la raíz del repo:
    python scripts/seed_medios.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from src.database import get_engine
from src.models import Medio

# Todos con UN solo feed, el general, y eso esta MEDIDO -- no es que no se
# hayan probado los de seccion.
#
# La intuicion era que el feed general de un diario grande deja afuera parte de
# la redaccion, y en una foto unica lo parece: La Nacion trae 89 items en el
# general contra 342 entre sus secciones. Pero esos 253 de diferencia son
# ARCHIVO, no cobertura fresca. El general es una ventana movil de las ultimas
# 7 h (TN, 23 h); las secciones guardan meses hacia atras.
#
# La prueba concluyente: dentro de la ventana temporal que cubre el general,
# las secciones de La Nacion aportan **0 items** que el general no tenga. TN
# muestra 41, pero todos de mas de 10 h de antiguedad -- con el polling cada 15
# minutos ya los habiamos visto cuando eran nuevos.
#
# Se probo en produccion: sumar 8 feeds de seccion a cada uno trajo 151
# noticias, con **antiguedad mediana de 25,5 h** (una de casi 4 anios), de las
# cuales solo 14 entraban en la ventana de agrupamiento y **ninguna formo un
# solo par**. Ocho veces mas requests por ciclo para traer archivo.
#
# El soporte de varios feeds queda igual en el modelo: no cuesta nada con listas
# de un elemento y sirve el dia que entre un medio con el feed general flaco.
MEDIOS = [
    {
        "nombre": "La Nación",
        "url_base": "https://www.lanacion.com.ar",
        "feeds_rss": ["https://www.lanacion.com.ar/arc/outboundfeeds/rss/"],
    },
    {
        "nombre": "TN",
        "url_base": "https://tn.com.ar",
        # OJO si alguna vez se agregan secciones: `tn.com.ar/feed/<seccion>/`
        # responde 200 pero IGNORA la seccion y devuelve el general. La que
        # filtra de verdad es la de Arc:
        # tn.com.ar/arc/outboundfeeds/rss/category/<seccion>/?outputType=xml
        "feeds_rss": ["https://tn.com.ar/feed/"],
    },
    {
        "nombre": "El Cronista",
        # No es Arc, tiene su propio esquema. Su general ya trae el 97% de lo
        # alcanzable entre sus secciones.
        "url_base": "https://www.cronista.com",
        "feeds_rss": ["https://www.cronista.com/files/rss/news.xml"],
    },
    # --- Farandula / espectaculos ---
    {
        "nombre": "Revista Gente",
        # El feed se sirve desde gente.com.ar pero los articulos viven en
        # revistagente.com (redireccion del propio medio).
        "url_base": "https://www.revistagente.com",
        "feeds_rss": ["https://www.gente.com.ar/feed/"],
    },
    {
        "nombre": "Revista Paparazzi",
        "url_base": "https://www.paparazzi.com.ar",
        "feeds_rss": ["https://www.paparazzi.com.ar/feed/"],
    },
    {
        "nombre": "Ciudad Magazine",
        # Arc XP: el parametro ?outputType=xml es obligatorio, sin el da 404.
        # Su general ya trae el 100% de lo que publica (25 items).
        "url_base": "https://www.ciudad.com.ar",
        "feeds_rss": ["https://www.ciudad.com.ar/arc/outboundfeeds/rss/?outputType=xml"],
    },
    {
        "nombre": "Perfil",
        # Su RSS no trae content:encoded (0/438 medido el 18/08): la licencia de
        # sus terminos cubre "el contenido" y no lo retiene por descuido, asi
        # que entra por la segunda via -- extraer_por_url va en True.
        #
        # Un solo feed, el general, y por el mismo motivo medido para los
        # demas: es una ventana movil de 7,1 h que cubre el 94% de lo fresco,
        # las 4 secciones probadas aportan apenas 2 items que el general no
        # tenga. `/feed/internacionales` da 404 pese a que Perfil lo publica
        # en su propia pagina de RSS -- no se siembra.
        "url_base": "https://www.perfil.com",
        "feeds_rss": ["https://www.perfil.com/feed"],
        "extraer_por_url": True,
    },
]

# Campos que este script mantiene sincronizados contra una base ya cargada:
# los que declaran COMO se ingiere un medio. El resto se sigue respetando para
# no pisar ajustes manuales, igual que antes.
#
# `activo` queda afuera a proposito: dar de baja un medio es una decision
# operativa que se toma en la base, y volver a correr el seed no debe revivirlo.
# `url_base` tambien: nunca se piso hasta ahora y no hay motivo para empezar.
CAMPOS_SINCRONIZADOS = ["feeds_rss", "extraer_por_url"]

# Clarin queda FUERA del roster, y no por falta de herramienta: su RSS no trae
# content:encoded (verificado el 18/08, 0 de 438 items) y sus terminos de uso
# licencian explicitamente "titulos y/o links", nada mas -- retienen el cuerpo
# a proposito. La segunda via de ingesta (extraer_por_url) existe desde la
# etapa 2 y podria traerlo igual, pero seria cruzar una linea que el medio
# trazo. Ver specs/change_logs.md, "Backlog punto 1".
#
# Ambito y La Izquierda Diario quedan postergados por el mismo tipo de
# revision: Ambito no tiene contrato de reuso pero tampoco se evaluo a fondo
# todavia; La Izquierda Diario bloquea crawlers de IA y reserva TDM en su
# robots.txt (Directiva UE 2019/790 art. 4) pese a no tener terminos propios.
#
# Cadena 3 tiene el tag pero con el copete adentro, y ademas su feed esta
# congelado desde 2018. Diario Cronica trae el tag vacio y su agenda es de
# Chubut, que casi no se cruza con la nacional.
# Ver specs/change_logs.md, Fase 2 y Fase 4.


def main() -> int:
    engine = get_engine()

    with Session(engine) as session:
        creados = 0
        existentes = 0

        actualizados = 0

        for datos in MEDIOS:
            ya_existe = session.exec(
                select(Medio).where(Medio.nombre == datos["nombre"])
            ).first()

            if ya_existe:
                # Los campos de CONFIGURACION si se actualizan. Es la unica
                # forma de que un cambio declarado aca llegue a una base que ya
                # esta cargada. `activo` y el resto no se tocan, para no pisar
                # ajustes manuales.
                #
                # Se recorre la lista en vez de comparar solo `feeds_rss` como
                # antes: cuando se agrego `extraer_por_url` quedo claro que un
                # campo nuevo nunca habria llegado a los medios existentes.
                #
                # El estado deseado se lee de un `Medio` armado con los mismos
                # datos y NO del dict: asi un campo que la entrada no declara
                # toma el default del modelo en vez de quedar sin tocar. Sin
                # eso la bandera solo se encenderia y nunca se apagaria, que es
                # una sincronizacion a medias -- y es exactamente lo que hacia
                # la primera version de este bloque.
                declarado = Medio(**datos)
                cambios = []
                for campo in CAMPOS_SINCRONIZADOS:
                    actual, deseado = getattr(ya_existe, campo), getattr(declarado, campo)
                    if actual != deseado:
                        setattr(ya_existe, campo, deseado)
                        cambios.append(f"{campo}: {actual!r} -> {deseado!r}")

                if cambios:
                    session.add(ya_existe)
                    session.commit()
                    print(f"  ~ {datos['nombre']}: " + "; ".join(cambios))
                    actualizados += 1
                else:
                    print(f"  = {datos['nombre']} ya existe (id={ya_existe.id}), sin cambios.")
                    existentes += 1
                continue

            medio = Medio(**datos)
            session.add(medio)
            session.commit()
            session.refresh(medio)
            print(f"  + {datos['nombre']} creado (id={medio.id}, "
                  f"{len(medio.feeds_rss)} feeds).")
            creados += 1

        print(f"\nListo: {creados} creados, {actualizados} actualizados, "
              f"{existentes} sin cambios.")

    return 0


if __name__ == "__main__":
    sys.exit(main())