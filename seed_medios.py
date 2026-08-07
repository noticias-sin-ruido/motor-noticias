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
        "nombre": "Clarín",
        "url_base": "https://www.clarin.com",
        "feed_rss": "https://www.clarin.com/rss/lo-ultimo/",
    },
    {
        "nombre": "TN",
        "url_base": "https://tn.com.ar",
        "feed_rss": "https://tn.com.ar/feed/",
    },
]


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