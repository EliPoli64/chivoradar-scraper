import re
from datetime import datetime
from typing import Optional, Tuple

import httpx
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
from src.parsers.categorias import esCategoriaUtil, inferirCategoria
from src.venues import searchAndUpsertVenue

load_dotenv()

API_BASE = "https://stsapi.specialticket.net"
SITE_BASE = "https://www.specialticket.net"
CATEGORIAS_EXCLUIDAS = {"parqueos", "ferry coonatramar"}
PRICE_RE = re.compile(r"^[₡$]\s?[\d.,]+$")


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
        if evento.get("hidden") == 1:
            continue
        if not evento.get("startDateTime"):
            continue
        filtrados.append(evento)
    return filtrados


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


def esperarDetalleRenderizado(driver, timeout=30) -> BeautifulSoup:
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: len(d.find_elements(By.TAG_NAME, "h5")) > 0
        )
    except TimeoutException:
        print("  Timed out waiting for page sections to render")
    return BeautifulSoup(driver.page_source, "lxml")


def extraerPrecios(precioTexto: str) -> float:
    precioLimpio = re.sub(r'[^\d.,]', '', precioTexto)
    precioLimpio = precioLimpio.replace(',', '.')

    if '.' in precioLimpio and len(precioLimpio.split('.')[-1]) == 3:
        precioLimpio = precioLimpio.replace('.', '')

    try:
        return float(precioLimpio)
    except ValueError:
        return 0.0


def extraerTiers(soup: BeautifulSoup) -> list[tuple[str, float]]:
    tiers: dict[str, float] = {}

    for h5 in soup.find_all("h5"):
        if "zonas" not in h5.get_text(strip=True).lower():
            continue

        section = h5.parent
        if not section:
            continue

        paragraphs = section.find_all("p")
        for i in range(len(paragraphs) - 1):
            nombre = paragraphs[i].get_text(strip=True)
            precioTexto = paragraphs[i + 1].get_text(strip=True)
            if not nombre or PRICE_RE.match(nombre):
                continue
            if not PRICE_RE.match(precioTexto):
                continue
            precio = extraerPrecios(precioTexto)
            if precio > 0 and nombre not in tiers:
                tiers[nombre] = precio

    return list(tiers.items())


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

        driver = getHeadlessDriver()
        try:
            driver.get(link)
            soup = esperarDetalleRenderizado(driver, timeout=30)
            foundTiers = extraerTiers(soup)
        except Exception as e:
            print(f"  Error loading detail page: {str(e)}")
            foundTiers = []
        finally:
            driver.quit()

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
            for tierName, tierPrice in foundTiers:
                tier = TierPrecio(
                    nombre=tierName,
                    precio=tierPrice,
                    moneda="CRC",
                    evento=evento.id
                )
                await tier.insert()
                tiersGuardados.append(tier)
                print(f"    - {tierName}: ₡{tierPrice:,.2f}")
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
