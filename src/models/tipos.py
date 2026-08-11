"""Tipos de columna compartidos por los modelos."""
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# JSONB en PostgreSQL (producción); JSON genérico en SQLite (tests en memoria),
# ya que SQLite no soporta el tipo JSONB.
#
# Vive acá y no dentro de un modelo porque lo usan varios, y una definición de
# tipo duplicada es de las cosas que se desincronizan sin que nadie lo note.
JSONVariant = JSONB().with_variant(JSON(), "sqlite")
