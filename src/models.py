from datetime import datetime
from typing import Optional
from beanie import Document
from bson import ObjectId
from pydantic import ConfigDict


class Venue(Document):
    nombre: str
    slug: str
    ubicacion: dict | None = None
    direccion: str | None = None
    redesSociales: dict | None = None

    class Settings:
        name = "venues"
    
    model_config = ConfigDict(arbitrary_types_allowed=True)


class Evento(Document):
    titulo: str
    categoria: str
    urlImagen: str | None = None
    ubicacion: ObjectId | None = None
    fechaHora: datetime
    descripcion: str | None = None
    link: str

    class Settings:
        name = "eventos"
    
    model_config = ConfigDict(arbitrary_types_allowed=True)


class TierPrecio(Document):
    nombre: str
    precio: float
    evento: ObjectId

    class Settings:
        name = "tiersPrecio"
    
    model_config = ConfigDict(arbitrary_types_allowed=True)