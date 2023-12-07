import asyncio
from collections import deque
import os
from pathlib import Path
import sys
from typing import Sequence
import aiohttp
import databases
import httpx
import orjson
from tenacity import retry, stop_after_attempt

sys.path.insert(0, os.path.abspath(os.pardir))
os.chdir(os.path.abspath(os.pardir))

from app import logging, state
from app.objects import collections
from app.objects.beatmap import BeatmapSet, ensure_local_osu_file

BEATMAPS_PATH = Path.cwd() / ".data/osu"

prod_database = databases.Database("mysql://ppysb:@localhost:3306/banchopy_migration")
dev_database = databases.Database("mysql://ppysb:@localhost:3306/banchopy_prod")

async def prepare_ctx():
    state.loop = asyncio.get_running_loop()
    state.services.http_client = httpx.AsyncClient()
    
    await state.services.database.connect()
    await state.services.create_db_and_tables()
    await state.services.redis.initialize()
    
    async with state.services.database.connection() as db_conn:
        await collections.initialize_ram_caches(db_conn)
        
        
async def retrieve_data(query: str):
    prod_data = await prod_database.fetch_all(query)
    prod_queue = deque(prod_data)
    del(prod_data)
    return prod_queue
    
        

@retry(reraise=True, stop=stop_after_attempt(3))
async def api_get_beatmaps(set_id: int):
    beatmap_set = await BeatmapSet._from_bsid_osuapi(set_id)
    if beatmap_set is not None:
        for map in beatmap_set.maps:
            beatmap_path = BEATMAPS_PATH / f"{str(map.id)}.osu"
            await ensure_local_osu_file(beatmap_path, map.id, map.md5)
    
    
async def handle_beatmaps():
    # we read all maps in one role
    beatmaps_queue = await retrieve_data("select distinct set_id from maps order by set_id desc")
    
    counter = 0
    
    while True:
        for _ in range(1000):
            try:
                record = beatmaps_queue.pop()
                await api_get_beatmaps(record['set_id'])
                counter += 1
            except Exception:
                logging.log(f"Caught exception at {str(record['set_id'])}")
        
        logging.log(f"Cursor at {str(record['set_id'])}, Counter at {str(counter)}", logging.Ansi.GREEN)
        
        
async def handle_frozen_status():
    beatmaps_queue = await retrieve_data("select id, status from maps where frozen=1")
    while True:
        record = beatmaps_queue.pop()
        await dev_database.execute("update maps set status=:status where id=:id", {'status': record['status'], 'id': record['id']})
        
    
async def handle_plays_passes():
    beatmaps_queue = await retrieve_data("select id, passes, plays from maps where passes != 0 and plays !=0 order by set_id")
    while True:
        for _ in range(1000):
            record = beatmaps_queue.pop()
            await dev_database.execute("update maps set passes=:passes, plays=:plays where id=:id", {'passes': record['passes'], 'plays': record['plays'], 'id': record['id']})
        await asyncio.sleep(0.1)
        

async def main(argv: Sequence[str] | None = None):
    argv = argv if argv is not None else sys.argv[1:]
    
    await prepare_ctx()
    await prod_database.connect()
    await dev_database.connect()
    
    try:
        if argv[0] == "base":
            await handle_beatmaps()
        if argv[0] == "frozen":
            await handle_frozen_status()
        if argv[0] in ("plays", "passes") :
            await handle_plays_passes()
    except IndexError:
        logging.log("Mission Complete!", logging.Ansi.LBLUE)
        
    await state.services.http_client.aclose()
    await state.services.database.disconnect()
    await prod_database.disconnect()
    await dev_database.disconnect()
        
        
if __name__ == "__main__":
    asyncio.run(main())