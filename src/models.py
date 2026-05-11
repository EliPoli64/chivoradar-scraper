from datetime import datetime
from typing import Optional
from beanie import Document
from bson import ObjectId


class Venue(Document):
    nombre: str
    slug: str
    ubicacion: dict | None = None
    direccion: str | None = None
    redesSociales: dict | None = None

    class Settings:
        name = "venues"


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


class TierPrecio(Document):
    nombre: str
    precio: float
    evento: ObjectId

    class Settings:
        name = "tiersPrecio"
