import asyncio
from src.database import connectDb
from src.parsers.eTicket import fetchEticket
from src.parsers.specialTicket import fetchSpecialTicket
from src.parsers.starTicket import fetchStarTicket


async def getAll():
    specialTicket = await fetchSpecialTicket()
    eticket = await fetchEticket()
    starTicket = await fetchStarTicket()
    return {"message": "Success", "data": eticket + specialTicket + starTicket}


async def main():
    await connectDb()
    result = await getAll()
    # print(result)


if __name__ == "__main__":
    asyncio.run(main())
