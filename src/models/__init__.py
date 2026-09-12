"""
Este módulo importa todos los modelos para que:
  1. Queden registrados en `SQLModel.metadata` (necesario para crear las tablas).
  2. Las relaciones (`Relationship`) entre modelos puedan resolverse entre sí,
     sin importar el orden en que cada archivo fue definido.
"""

from .medio import Medio
from .noticia import Noticia
from .cluster import Cluster
from .corrida import Corrida
from .sintesis import Sintesis, SintesisNoticia
from .publicacion_redes import PublicacionRedes
from .modelo_ia import Adaptador, ModeloIA, ModoEstructura
from .entrega import ConfiguracionEntrega
from .alertas import ConfiguracionAlertas

__all__ = [
    "Medio",
    "Noticia",
    "Cluster",
    "Corrida",
    "Sintesis",
    "SintesisNoticia",
    "PublicacionRedes",
    "ModeloIA",
    "Adaptador",
    "ModoEstructura",
    "ConfiguracionEntrega",
    "ConfiguracionAlertas",
]
