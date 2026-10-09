from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId

from src.models import Evento


async def upsertEvento(
    *,
    titulo: str,
    categoria: str,
    urlImagen: Optional[str],
    ubicacion: Optional[ObjectId],
    fechaHora: datetime,
    descripcion: Optional[str],
    link: str,
) -> tuple[Evento, bool]:
    """Upsert an event keyed by `link`.

    `createdAt` (first-seen time, UTC) is written with `$setOnInsert`, so it is
    set only when the document is first created and never changes on re-scrapes.
    Returns the stored document and whether it was newly inserted.
    """
    result = await Evento.get_pymongo_collection().update_one(
        {"link": link},
        {
            "$set": {
                "titulo": titulo,
                "categoria": categoria,
                "urlImagen": urlImagen,
                "ubicacion": ubicacion,
                "fechaHora": fechaHora,
                "descripcion": descripcion,
            },
            "$setOnInsert": {"createdAt": datetime.now(timezone.utc)},
        },
        upsert=True,
    )
    evento = await Evento.find_one({"link": link})
    return evento, result.upserted_id is not None
