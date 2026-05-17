import asyncio
from src.database import connectDb
from src.parsers.eTicket import fetchEticket
from src.parsers.specialTicket import fetchSpecialTicket


async def getAll():
    specialTicket = await fetchSpecialTicket()
    eticket = await fetchEticket()
    # starTicket = fetchStarTicket()        # TODO
    return {"message": "Success", "data": eticket + specialTicket}


async def main():
    await connectDb()
    result = await getAll()
    # print(result)


if __name__ == "__main__":
    asyncio.run(main())
