import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client("http://127.0.0.1:8000/mcp") as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Tools:", [t.name for t in tools.tools])
            print(await session.call_tool("add_expense", {"amount": 800, "note": "foodpanda"}))
            print(await session.call_tool("add_expense", {"amount": 500, "note": "petrol"}))
            print(await session.call_tool("get_total", {"period": "today"}))
            print(await session.call_tool("top_category", {"period": "month"}))

asyncio.run(main())