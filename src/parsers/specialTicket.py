from datetime import datetime
from typing import Optional, Tuple

import httpx
from bson import ObjectId
from dotenv import load_dotenv

from src.database import connectDb
from src.models import Evento, TierPrecio, Venue
from src.parsers.categorias import esCategoriaUtil, esEventoExcluido, inferirCategoria
from src.venues import searchAndUpsertVenue

load_dotenv()

API_BASE = "https://stsapi.specialticket.net"
SITE_BASE = "https://www.specialticket.net"
CATEGORIAS_EXCLUIDAS = {"parqueos", "ferry coonatramar"}


async def fetchEventosApi() -> list[dict]:
    eventosPorId: dict[int, dict] = {}

    async with httpx.AsyncClient(timeout=30) as client:
        for endpoint in ("/event/OtherEvents", "/event/UpcomingEvents"):
            try:
                resp = await client.get(f"{API_BASE}{endpoint}")
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"Error fetching {endpoint}: {e}")
                continue

            for evento in data.get("eventList", []):
                if evento.get("id") is not None:
                    eventosPorId[evento["id"]] = evento

    return list(eventosPorId.values())


def filtrarEventos(eventos: list[dict]) -> list[dict]:
    filtrados = []
    for evento in eventos:
        categoria = (evento.get("eventCategoryName") or "").strip().lower()
        if categoria in CATEGORIAS_EXCLUIDAS:
            continue
        if esEventoExcluido(evento):
            continue
        if evento.get("hidden") == 1:
            continue
        if not evento.get("startDateTime"):
            continue
        filtrados.append(evento)
    return filtrados


def obtenerDetalleEvento(eventId: int) -> dict:
    """Fetch the public event detail (same endpoint the site's ticket renderer uses).

    The event-details page is a heavy React SPA (crowdhandler queue, chatfuel, google,
    metricool, fb pixel...) that frequently never finishes loading under headless Chrome,
    which is why rendering the page in Selenium timed out. The ticket zones come from
    this public JSON API instead, so we call it directly.
    """
    url = f"{API_BASE}/event/EventDetail?id={eventId}&withSeatZones=true"
    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def extraerTiersDeDetalle(detalle: dict) -> list[dict[str, Optional[float]]]:
    """Map eventDetail seatZones into tier dicts: {zona, nombre, precio, precioBase, cargo, moneda}.

    The site displays, per zone, the label `seatZoneDetail` with the minimum price
    (a "+" suffix when the zone has several price tiers), so we keep that contract:
    one tier per zone at the minimum amount.
    """
    eventList = detalle.get("eventList") or []
    if not eventList:
        return []
    evento = eventList[0]
    moneda = "CRC" if evento.get("currency") == 1 else "USD"

    tiers: list[dict[str, Optional[float]]] = []
    for zona in evento.get("seatZones") or []:
        prices = [p for p in (zona.get("prices") or []) if (p.get("amount") or 0) > 0]
        if not prices:
            continue
        seatZoneName = zona.get("seatZoneName") or ""
        seatZoneDetail = zona.get("seatZoneDetail") or seatZoneName or "General"
        precioMin = min(prices, key=lambda p: p["amount"])
        tiers.append({
            "zona": seatZoneName,
            "nombre": seatZoneDetail,
            "precio": precioMin["amount"],
            "precioBase": precioMin["amount"],
            "cargo": precioMin.get("serviceFee") or 0.0,
            "moneda": moneda,
        })
    return tiers


async def verificarUbicacionEnDB(venueId: Optional[ObjectId]) -> Tuple[bool, Optional[Venue]]:
    if not venueId:
        return False, None

    venue = await Venue.get(venueId)
    if not venue:
        return False, None

    hasLocation = venue.ubicacion is not None and venue.ubicacion.get("coordinates")

    return hasLocation, venue


async def extraerEventoSpecialTicket(eventosApi: list[dict]) -> tuple[list[Evento], list[TierPrecio]]:
    eventosGuardados: list[Evento] = []
    tiersGuardados: list[TierPrecio] = []

    await connectDb()

    for idx, ev in enumerate(eventosApi, 1):
        link = f"{SITE_BASE}/event-details/{ev['id']}"
        titulo = (ev.get("eventName") or ev.get("performerName") or "").strip()
        categoria = (ev.get("eventCategoryName") or "Sin Categoría").split(";")[0].strip()
        if not esCategoriaUtil(categoria):
            categoria = inferirCategoria(ev)

        print(f"\n[{idx}/{len(eventosApi)}] Processing: {titulo} ({link})")

        if not titulo:
            print("  No title found, skipping...")
            continue

        try:
            eventDate = datetime.fromisoformat(ev["startDateTime"])
        except ValueError:
            print("  Invalid startDateTime, skipping...")
            continue

        venue = None
        venueNombre = (ev.get("venueName") or "").strip()
        if venueNombre:
            direccionPartes = [ev.get("city"), ev.get("state"), ev.get("country")]
            venueDireccion = ", ".join(p for p in direccionPartes if p) or None
            venue = await searchAndUpsertVenue(nombre=venueNombre, direccion=venueDireccion)

        descripcion = (ev.get("eventDetail") or "").strip() or None
        urlImagen = ev.get("imageName") or None

        try:
            detalle = obtenerDetalleEvento(ev["id"])
            foundTiers = extraerTiersDeDetalle(detalle)
        except Exception as e:
            print(f"  Error loading event detail: {str(e)}")
            foundTiers = []

        existingEvent = await Evento.find_one({"link": link})
        if existingEvent:
            evento = existingEvent
            evento.titulo = titulo
            evento.categoria = categoria
            evento.urlImagen = urlImagen
            evento.ubicacion = venue.id if venue else None
            evento.fechaHora = eventDate
            evento.descripcion = descripcion
            await evento.save()
            await TierPrecio.find({"evento": evento.id}).delete()
            eventosGuardados.append(evento)
            print(f"  Event updated (ID: {evento.id})")
        else:
            evento = Evento(
                titulo=titulo,
                categoria=categoria,
                urlImagen=urlImagen,
                ubicacion=venue.id if venue else None,
                fechaHora=eventDate,
                descripcion=descripcion,
                link=link,
            )
            await evento.insert()
            eventosGuardados.append(evento)
            print(f"  Event saved to DB (ID: {evento.id})")

        if foundTiers:
            print(f"  Price tiers found: {len(foundTiers)}")
            for tierInfo in foundTiers:
                tier = TierPrecio(
                    nombre=tierInfo["nombre"],
                    precio=tierInfo["precio"],
                    moneda=tierInfo["moneda"],
                    zona=tierInfo["zona"],
                    precioBase=tierInfo["precioBase"],
                    cargo=tierInfo["cargo"],
                    evento=evento.id
                )
                await tier.insert()
                tiersGuardados.append(tier)
                print(f"    - {tierInfo['zona']} / {tierInfo['nombre']}: {tierInfo['precio']:,.2f}")
        else:
            print("  No price tiers found")

        if venue:
            hasLocation, venueObj = await verificarUbicacionEnDB(venue.id)
            if hasLocation:
                coords = venueObj.ubicacion.get("coordinates")
                print(f"  Venue location: OK (lat: {coords[1]}, lng: {coords[0]})")
            else:
                print("  Venue missing location coordinates")

        print(f"  Successfully processed: {titulo}")

    return eventosGuardados, tiersGuardados


async def fetchSpecialTicket():
    await connectDb()

    print("Starting Special Ticket scraper...")

    print("Fetching events from API...")
    eventosApi = filtrarEventos(await fetchEventosApi())
    print(f"Events after filtering: {len(eventosApi)}")

    print("\nStarting extraction and database insertion...")
    eventos, tiers = await extraerEventoSpecialTicket(eventosApi)

    print(f"Events saved to DB: {len(eventos)}")
    print(f"Price tiers saved to DB: {len(tiers)}")

    venuesWithoutLocation = []
    for evento in eventos:
        if evento.ubicacion:
            hasLocation, venue = await verificarUbicacionEnDB(evento.ubicacion)
            if not hasLocation:
                venuesWithoutLocation.append(venue.nombre if venue else "Unknown")

    if venuesWithoutLocation:
        print(f"Warning: {len(set(venuesWithoutLocation))} venues missing location coordinates:")
        for venueName in set(venuesWithoutLocation):
            print(f"  - {venueName}")
    else:
        print("All venues have location coordinates in database!")

    return eventos, tiers
