import httpx
from bs4 import BeautifulSoup
from src.parsers.geocoding import checkGeocoding


async def categorizarEventosEticket(categorias: list[str]) -> dict[str, list[str]]:
    eventosPorCategoria: dict[str, list[str]] = {}

    async with httpx.AsyncClient() as client:
        for categoria in categorias:
            resp = await client.get(categoria)
            soup = BeautifulSoup(resp.text, "lxml")

            divs = [div.get_text(strip=True) for div in soup.select("div")]
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


async def extraerEventoEticket(links: dict[str, list[str]]) -> list[dict]:
    infoEventos: list[dict] = []

    async with httpx.AsyncClient() as client:
        for categoria, listaLinks in links.items():
            for link in listaLinks:
                resp = await client.get(link)
                soup = BeautifulSoup(resp.text, "lxml")

                resultadosUbicacion = [
                    el.get_text(strip=True)
                    for el in soup.select(".font16.mayusculas_primera div")
                    if el.get_text(strip=True)
                ]

                lat, lng = await checkGeocoding(
                    f"{resultadosUbicacion[0]}+{resultadosUbicacion[1]}"
                )

                titulo = [
                    el.get_text(strip=True)
                    for el in soup.select(
                        ".font16.boldear600.borde_artista.mayusculas_primera div"
                    )
                    if el.get_text(strip=True)
                ]

                tiersPrecio = [
                    el.get_text(strip=True)
                    for el in soup.select("#infoPrecios div")
                ]

                infoEventos.append(
                    {
                        "categoria": categoria,
                        "titulo": titulo,
                        "ubicacion": resultadosUbicacion,
                        "latitud": lat,
                        "longitud": lng,
                        "tiersPrecio": tiersPrecio,
                        "link": link,
                    }
                )

    return infoEventos


async def fetchEticket():
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://www.eticket.cr/")
        soup = BeautifulSoup(resp.text, "lxml")
        links = [a.get("href") for a in soup.select("a") if a.get("href")]

    # print(links)

    categorias = list(set(link for link in links if link and "categoria" in link))
    print(categorias)

    eventosPorCategoria = await categorizarEventosEticket(categorias)
    print(eventosPorCategoria)

    infoEventos = await extraerEventoEticket(eventosPorCategoria)
    print(infoEventos)
    return infoEventos
