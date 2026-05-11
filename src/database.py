import os

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from src.models import Evento, TierPrecio, Venue

connString = os.getenv("CONN_STRING", "mongodb://localhost:27017/loudmap")

_connected = False


async def connectDb():
    global _connected
    if _connected:
        return
    try:
        client = AsyncIOMotorClient(connString)
        await init_beanie(
            database=client.get_default_database(),
            document_models=[Evento, TierPrecio, Venue],
        )
        _connected = True
    except Exception as e:
        print("Error al conectar a la DB:", e)
