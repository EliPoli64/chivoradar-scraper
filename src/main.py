import asyncio
from src.parsers.eTicket import fetchEticket


async def getAll():
    # specialTicket = fetchSpecialTicket()  # TODO
    eticket = await fetchEticket()
    # starTicket = fetchStarTicket()        # TODO
    return {"message": "Success", "data": eticket}


async def main():
    # await connect_db()
    result = await getAll()
    # print(result)


if __name__ == "__main__":
    asyncio.run(main())
