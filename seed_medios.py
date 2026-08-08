#!/usr/bin/env python3
"""
Script de seed: carga los medios iniciales de la Fase 2 en la tabla `Medio`.
Es idempotente -- si un medio ya existe (por `nombre`), no lo duplica.

Requiere que la base de datos ya este inicializada (tablas creadas), por
ejemplo habiendo levantado la API una vez (init_db corre en el lifespan).

Uso:
    python seed_medios.py
"""
import sys

from sqlmodel import Session, select

from src.database import get_engine
from src.models import Medio

MEDIOS = [
    {
        "nombre": "La Nación",
        "url_base": "https://www.lanacion.com.ar",
        "feed_rss": "https://www.lanacion.com.ar/arc/outboundfeeds/rss/",
    },
    {
        "nombre": "El Cronista",
        "url_base": "https://www.cronista.com",
        "feed_rss": "https://www.cronista.com/files/rss/news.xml",
    },
    {
        "nombre": "TN",
        "url_base": "https://tn.com.ar",
        "feed_rss": "https://tn.com.ar/feed/",
    },
    # --- Farandula / espectaculos ---
    {
        "nombre": "Revista Gente",
        # El feed se sirve desde gente.com.ar pero los articulos viven en
        # revistagente.com (redireccion del propio medio).
        "url_base": "https://www.revistagente.com",
        "feed_rss": "https://www.gente.com.ar/feed/",
    },
    {
        "nombre": "Revista Paparazzi",
        "url_base": "https://www.paparazzi.com.ar",
        "feed_rss": "https://www.paparazzi.com.ar/feed/",
    },
    {
        "nombre": "Ciudad Magazine",
        # Arc XP: el parametro ?outputType=xml es obligatorio, sin el da 404.
        "url_base": "https://www.ciudad.com.ar",
        "feed_rss": "https://www.ciudad.com.ar/arc/outboundfeeds/rss/?outputType=xml",
    },
]

# Clarin quedo fuera del line-up: su RSS (feed general y los 5 feeds por
# seccion probados) no trae content:encoded, solo description corta -- ver
# specs/change_logs.md, Fase 2.


def main() -> int:
    engine = get_engine()

    with Session(engine) as session:
        creados = 0
        existentes = 0

        for datos in MEDIOS:
            ya_existe = session.exec(
                select(Medio).where(Medio.nombre == datos["nombre"])
            ).first()

            if ya_existe:
                print(f"  = {datos['nombre']} ya existe (id={ya_existe.id}), se omite.")
                existentes += 1
                continue

            medio = Medio(**datos)
            session.add(medio)
            session.commit()
            session.refresh(medio)
            print(f"  + {datos['nombre']} creado (id={medio.id}).")
            creados += 1

        print(f"\nListo: {creados} creados, {existentes} ya existían.")

    return 0


if __name__ == "__main__":
    sys.exit(main())