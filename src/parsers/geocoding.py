import os
import httpx
from dotenv import load_dotenv

load_dotenv()

geocodeApiKey = os.getenv("GEOCODING_API_KEY", "")

async def checkGeocoding(direccion: str) -> tuple[float, float]:
    if not geocodeApiKey:
        return (0.0, 0.0)
    
    # Formateo de la query para la API
    query = direccion.replace(" ", "+").replace(",", "%2C") + "+COSTA+RICA"
    
    url = f"https://api.opencagedata.com/geocode/v1/json?q={query}&key={geocodeApiKey}"
    print(url)
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        data = resp.json()
    
    resultado = data["results"][0]
    ubicacion = resultado["geometry"]
    return (ubicacion["lat"], ubicacion["lng"])