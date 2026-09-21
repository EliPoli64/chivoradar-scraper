import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

geocodeApiKey = os.getenv("GEOCODING_API_KEY", "")

PAISES = {"costa rica", "cr"}
GENERICOS = {"saprissa"}  # barrios/distritos genéricos a descartar
PLUS_CODE_RE = re.compile(r"\b([A-Z0-9]{4,8}\+[A-Z0-9]{2,4})\b")


def construirTerminoBusqueda(nombre: str, direccion: str | None = None) -> str:
    """Reduce dirección de venue a un término de búsqueda simple y preciso."""
    if not direccion:
        return nombre.strip()

    plusCode = PLUS_CODE_RE.search(direccion)
    if plusCode:
        return plusCode.group(1)

    nombreTokens = {t.lower() for t in nombre.split() if t}
    partes = [p.strip() for p in direccion.split(",") if p.strip()]
    limpio = []
    for parte in partes:
        if not parte:
            continue
        if parte.lower() in PAISES or parte.lower() in GENERICOS:
            continue
        limpio.append(parte)
    if not limpio:
        return nombre.strip()

    nombreEnLimpio = [p for p in limpio if p.lower() not in nombreTokens]
    resto = nombreEnLimpio or limpio

    # quedarse con el último componente (distrito/ciudad) cuando hay varios
    return f"{nombre} {resto[-1]}".strip()


async def checkGeocoding(nombre: str, direccion: str | None = None) -> tuple[float, float]:
    if not geocodeApiKey:
        return (0.0, 0.0)

    q = construirTerminoBusqueda(nombre, direccion)
    print(f"Geocoding query: {q}")

    params = {
        "q": q,
        "key": geocodeApiKey,
        "countrycode": "cr",
        "limit": 1,
        "no_annotations": 1,
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.opencagedata.com/geocode/v1/json", params=params)
        resp.raise_for_status()
        data = resp.json()
    
    if not data.get("results"):
        print("Geocoding returned no results, creating venue without location")
        return (0.0, 0.0)
    
    ubicacion = data["results"][0]["geometry"]
    return (ubicacion["lat"], ubicacion["lng"])