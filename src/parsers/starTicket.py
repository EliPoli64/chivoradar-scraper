import httpx
from bs4 import BeautifulSoup


async def fetchStarTicket() -> list[str]:
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://www.starticket.cr/")
        soup = BeautifulSoup(resp.text, "lxml")
        links:list = [a.get("href") for a in soup.select("a") if a.get("href")]
        linksLimpios = list(
            set(link for link in links if link.startswith("https://starticket.cr/e/"))
        )
    return linksLimpios
