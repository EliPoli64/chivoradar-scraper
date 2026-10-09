import json
import re
from datetime import datetime
from typing import Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from bson import ObjectId
from dotenv import load_dotenv

from src.database import connectDb
from src.models import Evento, TierPrecio, Venue
from src.parsers.categorias import esEventoExcluido, inferirCategoria
from src.events import upsertEvento
from src.venues import searchAndUpsertVenue

load_dotenv()

# starticket.cr is a server-rendered site: the catalog comes from JSON-LD embedded in
# the HTML (no browser needed), and the live ticket tiers come from a public JSON API
# used by the in-page <ticket-selector> component. Previously the scraper drove a
# headless Chrome to render both, which kept timing out (heavy client JS + bot layers).
SITE_URL = "https://www.starticket.cr/es"
TICKETS_ENDPOINT = "https://secure.starticket.cr/checkout/{event_id}/tickets"
PAISES_JSONLD = {"costa rica", "cr"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json",
}


def extraerEventosJsonLd(soup: BeautifulSoup) -> list[dict]:
    eventosPorUrl: dict[str, dict] = {}

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (TypeError, json.JSONDecodeError):
            continue

        if isinstance(data, list):
            items = data
        else:
            items = [data]

        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "Event":
                continue
            url = (item.get("url") or "").strip()
            if url and url not in eventosPorUrl:
                eventosPorUrl[url] = item

    return list(eventosPorUrl.values())


def esEventoCostaRica(evento: dict) -> bool:
    direccion = evento.get("location", {}).get("address")
    if not direccion or not isinstance(direccion, dict):
        return True
    pais = (direccion.get("addressCountry") or "").strip().lower()
    return pais in PAISES_JSONLD


def filtrarEventos(eventos: list[dict]) -> list[dict]:
    return [e for e in eventos if esEventoCostaRica(e)]


def extraerIdEvento(url: str | None) -> Optional[str]:
    m = re.search(r"/(\d+)/?$", url or "")
    return m.group(1) if m else None


def detectarMoneda(precioTexto: Optional[str], currencyFallback: Optional[str] = None) -> str:
    texto = precioTexto or ""
    if "₡" in texto or "CRC" in texto.upper():
        return "CRC"
    if "$" in texto or "USD" in texto.upper():
        return "USD"
    if currencyFallback in ("CRC", "USD"):
        return currencyFallback
    return "USD"


def limpiarHtml(texto) -> Optional[str]:
    if not texto:
        return None
    sinTags = re.sub(r"<[^>]+>", " ", texto)
    limpio = " ".join(sinTags.split()).strip()
    return limpio or None


def monedaEventoJsonLd(evento: dict) -> Optional[str]:
    """Infer an event-wide currency from its embedded JSON-LD offers (if consistent)."""
    moneda = None
    for offer in evento.get("offers") or []:
        c = (offer.get("priceCurrency") or "").strip()
        if not c:
            continue
        if moneda is None:
            moneda = c
        elif moneda != c:
            return None
    return moneda


def extraerTiersApi(client: httpx.Client, evento: dict) -> list[dict]:
    """Live ticket tiers from the same endpoint the in-page <ticket-selector> uses."""
    eventId = extraerIdEvento(evento.get("url"))
    if not eventId:
        return []
    resp = client.get(
        TICKETS_ENDPOINT.format(event_id=eventId),
        headers={"Referer": evento.get("url") or SITE_URL},
    )
    resp.raise_for_status()
    data = resp.json()
    currencyFallback = monedaEventoJsonLd(evento)

    tiers = []
    for tk in data.get("tickets") or []:
        try:
            precio = float(tk.get("price"))
        except (TypeError, ValueError):
            continue
        if precio < 0:
            continue
        tiers.append({
            "nombre": (tk.get("name") or "").strip() or "General",
            "precio": precio,
            "precioBase": precio,
            "cargo": None,
            "moneda": detectarMoneda(tk.get("price_text"), currencyFallback),
            "zona": limpiarHtml(tk.get("description")),
        })
    return tiers


def extraerTiersJsonLd(evento: dict) -> list[dict]:
    """Fallback tiers from the event's embedded JSON-LD offers."""
    tiers = []
    for offer in evento.get("offers") or []:
        try:
            precio = float(offer.get("price"))
        except (TypeError, ValueError):
            continue
        if precio < 0:
            continue
        tiers.append({
            "nombre": (offer.get("name") or "General").strip(),
            "precio": precio,
            "precioBase": None,
            "cargo": None,
            "moneda": (offer.get("priceCurrency") or "USD"),
            "zona": (offer.get("description") or "").strip() or None,
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


async def extraerEventoStarTicket(eventosJsonLd: list[dict], client: httpx.Client) -> tuple[list[Evento], list[TierPrecio]]:
    eventosGuardados: list[Evento] = []
    tiersGuardados: list[TierPrecio] = []

    await connectDb()

    for idx, ev in enumerate(eventosJsonLd, 1):
        link = (ev.get("url") or "").split("?")[0].strip()
        titulo = (ev.get("name") or "").strip()

        print(f"\n[{idx}/{len(eventosJsonLd)}] Processing: {titulo} ({link})")

        if not titulo or not link:
            print("  No title/link found, skipping...")
            continue

        if esEventoExcluido(ev):
            print("  Event matched exclusion (transporte/parqueo), skipping...")
            continue

        try:
            eventDate = datetime.fromisoformat(ev["startDate"]).replace(tzinfo=None)
        except (KeyError, ValueError):
            print("  Invalid startDate, skipping...")
            continue

        ubicacion = ev.get("location") or {}
        venueNombre = (ubicacion.get("name") or "").strip()
        venue = None
        if venueNombre:
            direccionParte = (ubicacion.get("address") or {}).get("streetAddress") or ""
            venue = await searchAndUpsertVenue(nombre=venueNombre, direccion=direccionParte or None)

        imagen = ev.get("image") or []
        urlImagen = imagen[0] if imagen else None
        descripcion = (ev.get("description") or "").strip() or None

        foundTiers = []
        try:
            foundTiers = extraerTiersApi(client, ev)
        except Exception as e:
            print(f"  Error loading tickets from API: {str(e)}")
        if not foundTiers:
            foundTiers = extraerTiersJsonLd(ev)

        evento, created = await upsertEvento(
            titulo=titulo,
            categoria=inferirCategoria(ev),
            urlImagen=urlImagen,
            ubicacion=venue.id if venue else None,
            fechaHora=eventDate,
            descripcion=descripcion,
            link=link,
        )
        await TierPrecio.find({"evento": evento.id}).delete()
        eventosGuardados.append(evento)
        print(f"  Event {'saved to DB' if created else 'updated'} (ID: {evento.id})")

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
                    evento=evento.id,
                )
                await tier.insert()
                tiersGuardados.append(tier)
                print(f"    - {tier.nombre}: {tier.precio:,.2f} {tier.moneda}")
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


async def fetchStarTicket():
    await connectDb()

    print("Starting Star Ticket scraper...")

    try:
        with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
            print(f"Loading {SITE_URL} ...")
            resp = client.get(SITE_URL)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            eventosJsonLd = filtrarEventos(extraerEventosJsonLd(soup))
            print(f"Events after filtering: {len(eventosJsonLd)}")

            print("\nStarting extraction and database insertion...")
            eventos, tiers = await extraerEventoStarTicket(eventosJsonLd, client)
    except Exception as e:
        print(f"Error loading event list: {str(e)}")
        return [], []

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