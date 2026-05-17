import os
from beanie import init_beanie
from pymongo import AsyncMongoClient
from src.models import Evento, TierPrecio, Venue
from dotenv import load_dotenv

load_dotenv()
connString = os.getenv("CONN_STRING", "mongodb://localhost:27017")

_connected = False

async def connectDb():
    global _connected
    if _connected:
        return
    try:
        client = AsyncMongoClient(connString)
        db = client["chivoradar"]
        print(db.name)
        await init_beanie(
            database=db,
            document_models=[Evento, TierPrecio, Venue],
        )
        _connected = True
    except Exception as e:
        print("Error al conectar a la DB:", e)