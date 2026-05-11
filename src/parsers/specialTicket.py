import httpx
from bs4 import BeautifulSoup


async def fetchSpecialTicket() -> list[str]:
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://www.specialticket.net/")
        soup = BeautifulSoup(resp.text, "lxml")
        links:list = [a.get("href") for a in soup.select("a") if a.get("href")]
    return links
