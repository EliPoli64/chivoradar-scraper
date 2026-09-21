import json
from datetime import datetime
from typing import Optional, Tuple

from bs4 import BeautifulSoup
from bson import ObjectId
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from src.database import connectDb
from src.models import Evento, TierPrecio, Venue
from src.venues import searchAndUpsertVenue

load_dotenv()

SITE_URL = "https://www.starticket.cr/es"
PAISES_JSONLD = {"costa rica", "cr"}


def getHeadlessDriver():
    chromeOptions = Options()
    chromeOptions.add_argument("--headless=new")
    chromeOptions.add_argument("--no-sandbox")
    chromeOptions.add_argument("--disable-dev-shm-usage")
    chromeOptions.add_argument("--disable-gpu")
    chromeOptions.add_argument("--window-size=1920,1080")
    chromeOptions.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
    chromeOptions.add_argument("--disable-blink-features=AutomationControlled")
    chromeOptions.add_experimental_option("excludeSwitches", ["enable-automation"])
    chromeOptions.add_experimental_option('useAutomationExtension', False)

    driver = webdriver.Chrome(options=chromeOptions)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


def esperarListadoRenderizado(driver, timeout=45) -> BeautifulSoup:
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: len(d.find_elements(By.XPATH, "//script[@type='application/ld+json']")) > 0
        )
    except TimeoutException:
        print("  Timed out waiting for JSON-LD scripts")
    return BeautifulSoup(driver.page_source, "lxml")


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


async def verificarUbicacionEnDB(venueId: Optional[ObjectId]) -> Tuple[bool, Optional[Venue]]:
    if not venueId:
        return False, None

    venue = await Venue.get(venueId)
    if not venue:
        return False, None

    hasLocation = venue.ubicacion is not None and venue.ubicacion.get("coordinates")

    return hasLocation, venue


async def extraerEventoStarTicket(eventosJsonLd: list[dict]) -> tuple[list[Evento], list[TierPrecio]]:
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

        existingEvent = await Evento.find_one(Evento.link == link)
        if existingEvent:
            evento = existingEvent
            evento.titulo = titulo
            evento.categoria = "Sin Categoría"
            evento.urlImagen = urlImagen
            evento.ubicacion = venue.id if venue else None
            evento.fechaHora = eventDate
            evento.descripcion = descripcion
            await evento.save()
            await TierPrecio.find(TierPrecio.evento == evento.id).delete()
            eventosGuardados.append(evento)
            print(f"  Event updated (ID: {evento.id})")
        else:
            evento = Evento(
                titulo=titulo,
                categoria="Sin Categoría",
                urlImagen=urlImagen,
                ubicacion=venue.id if venue else None,
                fechaHora=eventDate,
                descripcion=descripcion,
                link=link,
            )
            await evento.insert()
            eventosGuardados.append(evento)
            print(f"  Event saved to DB (ID: {evento.id})")

        offers = ev.get("offers") or []
        if offers:
            print(f"  Price tiers found: {len(offers)}")
            for offer in offers:
                tierPrecio = offer.get("price")
                if tierPrecio is None:
                    continue
                tier = TierPrecio(
                    nombre=(offer.get("name") or "General").strip(),
                    precio=float(tierPrecio),
                    moneda=offer.get("priceCurrency") or "USD",
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

    driver = getHeadlessDriver()
    try:
        print(f"Loading {SITE_URL} ...")
        driver.get(SITE_URL)
        soup = esperarListadoRenderizado(driver, timeout=45)
    finally:
        driver.quit()

    eventosJsonLd = filtrarEventos(extraerEventosJsonLd(soup))
    print(f"Events after filtering: {len(eventosJsonLd)}")

    print("\nStarting extraction and database insertion...")
    eventos, tiers = await extraerEventoStarTicket(eventosJsonLd)

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