import dateparser
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import httpx
from src.parsers.geocoding import checkGeocoding
from src.venues import searchAndUpsertVenue
from src.models import Evento, TierPrecio, Venue
from src.database import connectDb
from dotenv import load_dotenv
import os
import re
from bson import ObjectId
from datetime import datetime
from typing import Optional, Tuple

load_dotenv()

async def categorizarEventosSpecialTicket(categorias: list[str]) -> dict[str, list[str]]:
    eventosPorCategoria: dict[str, list[str]] = {}

    async with httpx.AsyncClient() as client:
        for categoria in categorias:
            resp = await client.get(categoria)
            soup = BeautifulSoup(resp.text, "lxml")

            nombreCategoria = "Sin Categoría"
            tituloCategoria = soup.select_one("h1, .categoria-titulo, .titulo-categoria, .page-title")
            if tituloCategoria:
                nombreCategoria = tituloCategoria.get_text(strip=True)
            
            eventos = [
                link.get("href") for link in soup.select("a")
                if link.get("href") and any(keyword in link.get("href").lower() for keyword in ["evento", "event", "boleto", "ticket"])
            ]
            
            eventosCompletos = []
            for evento in eventos:
                if evento.startswith("/"):
                    evento = f"https://specialticket.net{evento}"
                elif not evento.startswith("http"):
                    evento = f"https://specialticket.net/{evento}"
                eventosCompletos.append(evento)
            
            eventosPorCategoria[nombreCategoria] = list(set(eventosCompletos))

    return eventosPorCategoria

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

def esperarPaginaCompleta(driver, timeout=30):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return jQuery.active == 0")
        if d.execute_script("return typeof jQuery != 'undefined'")
        else True
    )
    return BeautifulSoup(driver.page_source, 'lxml')

def extraerPrecios(precioTexto: str) -> float:
    precioLimpio = re.sub(r'[^\d.,]', '', precioTexto)
    precioLimpio = precioLimpio.replace(',', '.')
    
    if '.' in precioLimpio and len(precioLimpio.split('.')[-1]) == 3:
        precioLimpio = precioLimpio.replace('.', '')
    
    try:
        return float(precioLimpio)
    except ValueError:
        return 0.0

async def verificarUbicacionEnDB(venueId: Optional[ObjectId]) -> Tuple[bool, Optional[Venue]]:
    if not venueId:
        return False, None
    
    venue = await Venue.get(venueId)
    if not venue:
        return False, None
    
    hasLocation = venue.ubicacion is not None and venue.ubicacion.get("coordinates")
    
    return hasLocation, venue

async def extraerEventoSpecialTicket(links: dict[str, list[str]]) -> tuple[list[Evento], list[TierPrecio]]:
    eventosGuardados: list[Evento] = []
    tiersGuardados: list[TierPrecio] = []
    
    await connectDb()

    for categoria, listaLinks in links.items():
        print(f"\n{'='*60}")
        print(f"Processing category: {categoria}")
        print(f"Total events: {len(listaLinks)}")
        print(f"{'='*60}")
        
        for idx, link in enumerate(listaLinks, 1):
            print(f"\n[{idx}/{len(listaLinks)}] Processing: {link}")
            
            driver = getHeadlessDriver()
            try:
                driver.get(link)
                soup = esperarPaginaCompleta(driver, timeout=30)

                titulo = ""
                tituloSelectors = [
                    "h1",
                    ".event-title",
                    ".titulo-evento",
                    ".title",
                    "[class*='titulo']",
                    "div.font22.boldear800"
                ]
                
                for selector in tituloSelectors:
                    tituloElement = soup.select_one(selector)
                    if tituloElement:
                        titulo = tituloElement.get_text(strip=True)
                        if titulo:
                            break
                
                if not titulo:
                    print(f"  No title found, skipping...")
                    continue
                
                print(f"  Title: {titulo}")

                existingEvent = await Evento.find_one(Evento.link == link)
                if existingEvent:
                    print(f"  Event already exists (ID: {existingEvent.id}), skipping...")
                    continue

                venueNombre = ""
                venueDireccion = ""
                
                venueSelectors = [
                    ".ubicacion",
                    ".venue", 
                    ".location",
                    "[class*='ubicacion']",
                    "[class*='lugar']",
                    "div:contains('Ubicación')",
                    "div:contains('Lugar')"
                ]
                
                for selector in venueSelectors:
                    venueElement = soup.select_one(selector)
                    if venueElement:
                        venueNombre = venueElement.get_text(strip=True)
                        for prefix in ["Ubicación:", "Lugar:", "Venue:", "Location:"]:
                            venueNombre = venueNombre.replace(prefix, "").strip()
                        if venueNombre:
                            break
                
                addressSelectors = [
                    ".direccion",
                    ".address",
                    "[class*='direccion']",
                    "[class*='address']"
                ]
                
                for selector in addressSelectors:
                    addressElement = soup.select_one(selector)
                    if addressElement:
                        venueDireccion = addressElement.get_text(strip=True)
                        if venueDireccion:
                            break
                
                print(f"  Venue: {venueNombre if venueNombre else 'Not found'}")
                
                venue = None
                if venueNombre:
                    venue = await searchAndUpsertVenue(
                        nombre=venueNombre,
                        direccion=venueDireccion if venueDireccion else None
                    )
                    
                    hasLocation, venueObj = await verificarUbicacionEnDB(venue.id)
                    if hasLocation:
                        coords = venueObj.ubicacion.get("coordinates")
                        print(f"  Venue has location: ({coords[1]}, {coords[0]})")
                    else:
                        print(f"  Venue missing location coordinates")

                eventDate = None
                fechaSelectors = [
                    ".fecha",
                    ".date",
                    "[class*='fecha']",
                    "[class*='date']",
                    ".event-date",
                    ".fecha-evento"
                ]
                
                for selector in fechaSelectors:
                    fechaElement = soup.select_one(selector)
                    if fechaElement:
                        fechaTexto = fechaElement.get_text(strip=True)
                        eventDate = dateparser.parse(fechaTexto, languages=['es'])
                        if eventDate:
                            break
                
                if not eventDate:
                    textoPagina = soup.get_text()
                    patrones = [
                        r'(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+de\s+(\d{4})',
                        r'(\d{1,2})/(\d{1,2})/(\d{4})',
                        r'(\d{1,2})-(\d{1,2})-(\d{4})'
                    ]
                    
                    for patron in patrones:
                        match = re.search(patron, textoPagina, re.IGNORECASE)
                        if match:
                            fechaTexto = match.group(0)
                            eventDate = dateparser.parse(fechaTexto, languages=['es'])
                            if eventDate:
                                break
                
                if eventDate:
                    print(f"  Date: {eventDate.strftime('%Y-%m-%d %H:%M')}")
                else:
                    print(f"  No date found, using current date")
                    eventDate = datetime.now()

                urlImagen = None
                imgSelectors = [
                    ".event-image img",
                    ".imagen-evento img",
                    "img[class*='event']",
                    "img[class*='imagen']",
                    "img[src*='event']",
                    "img[src*='evento']"
                ]
                
                for selector in imgSelectors:
                    imgElement = soup.select_one(selector)
                    if imgElement and imgElement.get("src"):
                        urlImagen = imgElement.get("src")
                        if not urlImagen.startswith("http"):
                            urlImagen = f"https://specialticket.net{urlImagen}"
                        break

                descripcion = ""
                descSelectors = [
                    ".descripcion",
                    ".description",
                    "[class*='descripcion']",
                    ".event-description",
                    ".contenido-evento"
                ]
                
                for selector in descSelectors:
                    descElement = soup.select_one(selector)
                    if descElement:
                        descripcion = descElement.get_text(strip=True)
                        if descripcion:
                            break

                evento = Evento(
                    titulo=titulo,
                    categoria=categoria,
                    urlImagen=urlImagen,
                    ubicacion=venue.id if venue else None,
                    fechaHora=eventDate,
                    descripcion=descripcion if descripcion else None,
                    link=link,
                )
                
                await evento.insert()
                eventosGuardados.append(evento)
                print(f"  Event saved to DB (ID: {evento.id})")

                tiersPrecio = []
                
                priceSelectors = [
                    ".precios-table",
                    ".ticket-prices",
                    "[class*='precio']",
                    "[class*='price']",
                    ".tipoBoleto",
                    ".ticket-type",
                    "table.precios",
                    ".price-list"
                ]
                
                foundTiers = []
                
                for selector in priceSelectors:
                    priceElements = soup.select(selector)
                    if priceElements:
                        for elem in priceElements:
                            ticketName = None
                            ticketPrice = None
                            
                            nameSelectors = [".ticket-name", ".tipo", ".nombre", "span:first-child", "div:first-child"]
                            priceSelectorsLocal = [".ticket-price", ".precio", ".valor", "span:last-child", "div:last-child"]
                            
                            for nameSel in nameSelectors:
                                nameElem = elem.select_one(nameSel)
                                if nameElem:
                                    ticketName = nameElem.get_text(strip=True)
                                    break
                            
                            for priceSel in priceSelectorsLocal:
                                priceElem = elem.select_one(priceSel)
                                if priceElem:
                                    priceText = priceElem.get_text(strip=True)
                                    ticketPrice = extraerPrecios(priceText)
                                    break
                            
                            if not ticketName or ticketPrice == 0.0:
                                fullText = elem.get_text(strip=True)
                                patterns = [
                                    r'([^:]+):\s*[₡$]?([\d.,]+)',
                                    r'([^\d]+)\s*[₡$]?([\d.,]+)'
                                ]
                                
                                for pattern in patterns:
                                    match = re.search(pattern, fullText)
                                    if match:
                                        ticketName = match.group(1).strip()
                                        ticketPrice = extraerPrecios(match.group(2))
                                        break
                            
                            if ticketName and ticketPrice > 0:
                                foundTiers.append((ticketName, ticketPrice))
                        
                        if foundTiers:
                            break
                
                if not foundTiers:
                    simplePriceElements = soup.select(".precio, .price, [class*='precio'], [class*='price']")
                    for elem in simplePriceElements:
                        priceText = elem.get_text(strip=True)
                        if priceText and not any(skip in priceText.lower() for skip in ['numerado', 'gratis', 'free']):
                            priceValue = extraerPrecios(priceText)
                            if priceValue > 0:
                                foundTiers.append(("General", priceValue))
                
                uniqueTiers = {}
                for name, price in foundTiers:
                    if name not in uniqueTiers:
                        uniqueTiers[name] = price
                
                if uniqueTiers:
                    print(f"  Price tiers found: {len(uniqueTiers)}")
                    for tierName, tierPrice in uniqueTiers.items():
                        if tierPrice > 0:
                            tier = TierPrecio(
                                nombre=tierName,
                                precio=tierPrice,
                                evento=evento.id
                            )
                            await tier.insert()
                            tiersGuardados.append(tier)
                            print(f"    - {tierName}: ₡{tierPrice:,.2f}")
                else:
                    print(f"  No price tiers found")
                
                if venue:
                    hasLocation, venueObj = await verificarUbicacionEnDB(venue.id)
                    if hasLocation:
                        coords = venueObj.ubicacion.get("coordinates")
                        print(f"  Final venue location verification: OK (lat: {coords[1]}, lng: {coords[0]})")
                    else:
                        print(f"  Final verification: Venue missing location coordinates")
                
                print(f"  Successfully processed: {titulo}")
                
            except Exception as e:
                print(f"  Error processing {link}: {str(e)}")
                continue
                
            finally:
                driver.quit()
    
    return eventosGuardados, tiersGuardados

async def fetchSpecialTicket():
    await connectDb()
    
    print("Starting Special Ticket scraper...")
    
    async with httpx.AsyncClient() as client:
        print("Fetching main page...")
        resp = await client.get("https://specialticket.net")
        soup = BeautifulSoup(resp.text, "lxml")
        links = [a.get("href") for a in soup.select("a") if a.get("href")]

    categorias = list(set(
        link for link in links 
        if link and any(keyword in link.lower() for keyword in ["categoria", "category", "eventos"])
        and (not link.startswith("http") or (link.startswith("http") and "specialticket" in link))
    ))
    
    categoriasCompletas = []
    for cat in categorias:
        if cat.startswith("/"):
            categoriasCompletas.append(f"https://specialticket.net{cat}")
        elif not cat.startswith("http"):
            categoriasCompletas.append(f"https://specialticket.net/{cat}")
        else:
            categoriasCompletas.append(cat)
    
    categoriasCompletas = list(set(categoriasCompletas))
    
    print(f"Found {len(categoriasCompletas)} categories")
    
    if not categoriasCompletas:
        print("No categories found, using main page as event listing")
        categoriasCompletas = ["https://specialticket.net"]

    print("Categorizing events...")
    eventosPorCategoria = await categorizarEventosSpecialTicket(categoriasCompletas)
    
    totalEventsFound = sum(len(events) for events in eventosPorCategoria.values())
    print(f"Total events found: {totalEventsFound}")
    
    print("\nStarting extraction and database insertion...")
    eventos, tiers = await extraerEventoSpecialTicket(eventosPorCategoria)
    

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