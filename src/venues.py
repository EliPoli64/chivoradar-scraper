import re
from typing import Optional
from src.models import Venue
from src.parsers.geocoding import checkGeocoding

async def searchAndUpsertVenue(
    nombre: str,
    direccion: Optional[str] = None,
) -> Venue:
    existingVenue = await encontrarVenue(nombre)
    
    if existingVenue:
        print(f"Found existing venue in DB: {existingVenue.nombre}")
        updated = False
        if direccion and not existingVenue.direccion:
            existingVenue.direccion = direccion
            updated = True
            print(f"Updated address for: {existingVenue.nombre}")
        
        if updated:
            await existingVenue.save()
            print(f"Saved updates to DB")
        
        return existingVenue

    print(f"Venue not found in DB: {nombre}")
    print(f"Getting coordinates via geocoding...")
    
    # intentar obtener coordenadas
    lat, lng = await checkGeocoding(nombre + " " + direccion if direccion else "")
    
    if lat != 0.0 or lng != 0.0:
        print(f"Got coordinates: ({lat}, {lng})")
    else:
        print(f"Could not get coordinates, creating venue without location")
    
    # crear nueva
    newVenue = await createVenue(
        nombre=nombre,
        direccion=direccion,
        latitud=lat if lat != 0.0 else None,
        longitud=lng if lng != 0.0 else None
    )
    
    print(f"Created new venue: {newVenue.nombre} (ID: {newVenue.id})")
    return newVenue

async def createVenue(
        nombre: str,
        direccion: Optional[str] = None,
        latitud: Optional[float] = None,
        longitud: Optional[float] = None,
        redesSociales: Optional[dict] = None
    ) -> Venue:
        
        # crear slug
        slug = nombre.lower().replace(" ", "-")
        slug = re.sub(r'[^a-z0-9-]', '', slug)
        
        # ubicacion
        ubicacion = None
        if latitud and longitud:
            ubicacion = {
                "type": "Point",
                "coordinates": [longitud, latitud]
            }
        
        newVenue = Venue(
            nombre=nombre,
            slug=slug,
            direccion=direccion,
            ubicacion=ubicacion,
            redesSociales=redesSociales or {}
        )
        
        await newVenue.insert()
        
        return newVenue

async def encontrarVenue(nombre: str) -> Optional[Venue]:
    pattern = re.compile(f"^{re.escape(nombre)}$", re.IGNORECASE)
    venue = await Venue.find_one(Venue.nombre == pattern)
    return venue