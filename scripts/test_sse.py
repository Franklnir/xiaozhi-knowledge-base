import asyncio
import json
from unittest.mock import MagicMock
from xiaozhi.database.postgres_store import PostgresStore
from xiaozhi.services.sse_service import stream_material_events

async def is_disc():
    return False

async def main():
    store = PostgresStore()
    req = MagicMock()
    req.is_disconnected = is_disc
    gen = stream_material_events(1, req, store)
    chunk = await gen.__anext__()
    print("FIRST CHUNK:")
    print(repr(chunk))

if __name__ == "__main__":
    asyncio.run(main())
