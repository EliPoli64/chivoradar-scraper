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

async def categorizarEventosEticket(categorias: list[str]) -> dict[str, list[str]]:
    eventosPorCategoria: dict[str, list[str]] = {}

    async with httpx.AsyncClient() as client:
        for categoria in categorias:
            resp = await client.get(categoria)
            soup = BeautifulSoup(resp.text, "lxml")

            divs = [div.get_text(strip=True) for div in soup.select("div.titulo_centrado.font50.blanco.boldear800")]
            nombreCategoria = "Sin Categoría"
            for div in divs:
                if "en Costa Rica" in div:
                    nombreCategoria = div.split(" ")[0]
                    break

            paginas = [
                'https://www.eticket.cr/' + a.get("href")
                for a in soup.select("a")
                if a.get("href") and "masinformacion" in a.get("href")
            ]
            eventosPorCategoria[nombreCategoria] = list(set(paginas))

    return eventosPorCategoria

meses = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
         "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}

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

async def extraerEventoEticket(links: dict[str, list[str]]) -> tuple[list[Evento], list[TierPrecio]]:
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

                resultadosUbicacion = [
                    el.get_text(strip=True)
                    for el in soup.select(".font16.mayusculas_primera div")
                    if el.get_text(strip=True)
                ]

                venueNombre = ""
                venueDireccion = ""
                if resultadosUbicacion:
                    venueNombre = resultadosUbicacion[0] if len(resultadosUbicacion) > 0 else ""
                    venueDireccion = resultadosUbicacion[1] if len(resultadosUbicacion) > 1 else ""

                print(f"  Venue: {venueNombre}")
                print(f"  Address: {venueDireccion}")

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

                tituloElements = [
                    el.get_text(strip=True) 
                    for el in soup.select("div.font22.boldear800")
                ]
                titulo = tituloElements[1] if len(tituloElements) > 1 else ""
                
                if not titulo:
                    print(f"  No title found, skipping...")
                    continue
                
                print(f"  Title: {titulo}")

                existingEvent = await Evento.find_one(Evento.link == link)
                if existingEvent:
                    print(f"  Event already exists (ID: {existingEvent.id}), skipping...")
                    continue

                fechaEvento = soup.select_one(".font16.boldear600.borde_artista")
                eventDate = None
                if fechaEvento:
                    fechaEvento = fechaEvento.get_text(separator=", ", strip=True)
                    parts = fechaEvento.split(", ")
                    if len(parts) >= 3:
                        eventDate = dateparser.parse(f"{parts[1]}, {parts[2][:-3]}")
                
                if eventDate:
                    print(f"  Date: {eventDate.strftime('%Y-%m-%d %H:%M')}")
                else:
                    print(f"  No date found, using current date")
                    eventDate = datetime.now()

                urlImagen = None
                imgSelectors = [
                    "img[src*='evento']",
                    "img[src*='event']",
                    ".event-image img",
                    "img"
                ]
                
                for selector in imgSelectors:
                    imgElement = soup.select_one(selector)
                    if imgElement and imgElement.get("src"):
                        urlImagen = imgElement.get("src")
                        if not urlImagen.startswith("http"):
                            urlImagen = f"https://www.eticket.cr/{urlImagen}"
                        break

                descripcion = ""
                descSelectors = [
                    ".descripcion",
                    ".description",
                    ".event-description",
                    "div.font16"
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

                tiersPrecioText = [el.get_text(strip=True) for el in soup.select(".col.tipoBoleto") if el.get_text(strip=True) != 'Numerado']
                
                foundTiers = []
                for tierText in tiersPrecioText:
                    match = re.search(r'([^:]+):?\s*[₡$]?([\d.,]+)', tierText)
                    if match:
                        tierName = match.group(1).strip()
                        tierPrice = extraerPrecios(match.group(2))
                        if tierPrice > 0:
                            foundTiers.append((tierName, tierPrice))
                    else:
                        priceValue = extraerPrecios(tierText)
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

async def fetchEticket():
    await connectDb()
    
    print("Starting eTicket scraper...")
    
    async with httpx.AsyncClient() as client:
        print("Fetching main page...")
        resp = await client.get("https://www.eticket.cr/")
        soup = BeautifulSoup(resp.text, "lxml")
        links = [a.get("href") for a in soup.select("a") if a.get("href")]

    categorias = list(set(link for link in links if link and "categoria" in link))
    
    print(f"Found {len(categorias)} categories")

    print("Categorizing events...")
    eventosPorCategoria = await categorizarEventosEticket(categorias)
    
    totalEventsFound = sum(len(events) for events in eventosPorCategoria.values())
    print(f"Total events found: {totalEventsFound}")
    
    print("\nStarting extraction and database insertion...")
    eventos, tiers = await extraerEventoEticket(eventosPorCategoria)

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
    
    print("="*60)
    
    return eventos, tiers