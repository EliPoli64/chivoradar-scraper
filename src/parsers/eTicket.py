import dateparser
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import httpx
from src.parsers.geocoding import checkGeocoding
from dotenv import load_dotenv

from src.venues import encontrarVenue

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
            #print(paginas)
            eventosPorCategoria[nombreCategoria] = list(set(paginas))

    return eventosPorCategoria


meses = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
         "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}

def getHeadlessDriver():
    """Create a headless Chrome driver for CI/CD environments"""
    chromeOptions = Options()
    chromeOptions.add_argument("--headless=new")  # New headless mode
    chromeOptions.add_argument("--no-sandbox")
    chromeOptions.add_argument("--disable-dev-shm-usage")
    chromeOptions.add_argument("--disable-gpu")
    chromeOptions.add_argument("--window-size=1920,1080")
    chromeOptions.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
    
    # Additional options for stability
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

async def extraerEventoEticket(links: dict[str, list[str]]) -> list[dict]:
    infoEventos: list[dict] = []

    async with httpx.AsyncClient() as client:
        for categoria, listaLinks in links.items():
            for link in listaLinks:
              driver = getHeadlessDriver()
              try:
                  driver.get(link)
                  soup = esperarPaginaCompleta(driver, timeout=30)

                  resultadosUbicacion = [
                      el.get_text(strip=True)
                      for el in soup.select(".font16.mayusculas_primera div")
                      if el.get_text(strip=True)
                  ]

                  lat, lng = 0.0, 0.0
                  if resultadosUbicacion:
                      lat, lng = await checkGeocoding(
                          f"{resultadosUbicacion[0]}+{resultadosUbicacion[1]}"
                      )

                  tituloElements = [
                      el.get_text(strip=True) 
                      for el in soup.select("div.font22.boldear800")
                  ]
                  titulo = tituloElements[1] if len(tituloElements) > 1 else ""
                  
                  tiersPrecio = [el.get_text(strip=True) for el in soup.select(".col.tipoBoleto") if el.get_text(strip=True) != 'Numerado']

                  fechaEvento = soup.select_one(".font16.boldear600.borde_artista")
                  eventDate = None
                  if fechaEvento:
                      fechaEvento = fechaEvento.get_text(separator=", ", strip=True)
                      parts = fechaEvento.split(", ")
                      if len(parts) >= 3:
                          eventDate = dateparser.parse(f"{parts[1]}, {parts[2][:-3]}")

                  infoEventos.append({
                      "categoria": categoria,
                      "titulo": titulo,
                      "ubicacion": resultadosUbicacion,
                      "fecha": eventDate,
                      "latitud": lat,
                      "longitud": lng,
                      "tiersPrecio": tiersPrecio,
                      "link": link,
                  })
                  
              finally:
                  driver.quit()
    return infoEventos


async def fetchEticket():
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://www.eticket.cr/")
        soup = BeautifulSoup(resp.text, "lxml")
        links = [a.get("href") for a in soup.select("a") if a.get("href")]

    # print(links)

    categorias = list(set(link for link in links if link and "categoria" in link))
    # print(categorias)

    eventosPorCategoria = await categorizarEventosEticket(categorias)
    # print(eventosPorCategoria)

    infoEventos = await extraerEventoEticket(eventosPorCategoria)
    print(infoEventos)
    return infoEventos
