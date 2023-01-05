import asyncio
import datetime
import os
import sys
import time
import aiohttp
import orjson

sys.path.insert(0, os.path.abspath(os.pardir))
os.chdir(os.path.abspath(os.pardir))

import databases
import app.settings
import app.state

from app.objects import beatmap
from app.logging import Ansi, log
from app.objects.beatmap import Beatmap, BeatmapSet
from pathlib import Path

osu_folder_path = Path.cwd() / ".data/osu"

async def fetch_beatmaps():
    async with databases.Database(app.settings.DB_DSN) as db:
        async with (
            db.connection() as select_conn,
            db.connection() as update_conn,
        ):
            # In order to avoid too many requests, we operate five maps in a turn.
            # Order by rand to avoid being stuck in some beatmaps (some issues with mirror maybe)
            for row in await select_conn.fetch_all(f"SELECT *,(rand()*timestamp(now())) AS rid FROM maps_lack where fixed=0 order by rid limit 5"):
                map_md5 = row["md5"]
                lack_type = row["lack_type"]
                if (lack_type == "entry"):
                    if (await select_conn.fetch_val(f"SELECT COUNT(id) FROM maps where md5=:md5", {"md5": map_md5}) > 0):
                        # The map is existed in database, just skip it and marked fixed
                        log_green("Map is already exists. Skipped!")
                        await mark_fixed(update_conn, map_md5)
                        continue
                    # The map is not existed. Fetch the whole set
                    api_data = await beatmap.api_get_beatmaps(h=map_md5)
                    beatmap_set = BeatmapSet.__new__(BeatmapSet)
                    if api_data is None:
                        # Bancho also don't have this map, maybe map is updated, skipped
                        await mark_fixed(update_conn, map_md5)
                        continue
                    beatmap_set.id = int(api_data[0]["beatmapset_id"])
                    beatmap_set.maps = []
                    # Build each beatmap in the set
                    for api_bmap in api_data:
                        bmap: Beatmap = Beatmap.__new__(Beatmap)
                        bmap.id = int(api_bmap["beatmap_id"])
                        bmap._parse_from_osuapi_resp(api_bmap)
                        # All maps 
                        bmap.frozen = False
                        bmap.passes = 0
                        bmap.plays = 0
                        beatmap_set.maps.append(bmap)
                        # Ensure we have the file or we download it 
                        await ensure_osu_file(bmap.id)
                    # Save all maps into sql (if existed, we will replace it without any exception)
                    await beatmap_set._save_to_sql()
                    log_green(f"Inserted: {beatmap_set.id}[{len(beatmap_set.maps)}]")
                    # Update beatmap set api check information
                    await update_conn.execute(
                        "REPLACE INTO mapsets "
                        "(server, id, last_osuapi_check) "
                        'VALUES ("osu!", :id, :last_osuapi_check)',
                        {"id": beatmap_set.id, "last_osuapi_check": datetime.datetime.now()},
                    )
                if (lack_type == "file"):
                    api_data = await beatmap.api_get_beatmaps(h=map_md5)
                    if api_data is None:
                        # Bancho also don't have this map, maybe map is updated, skipped
                        await mark_fixed(update_conn, map_md5)
                        continue
                    # Ensure each map in the beatmap set and download if necessary
                    for api_bmap in api_data:
                        beatmap_id = int(api_bmap["beatmap_id"])
                        await ensure_osu_file(beatmap_id)
                    await mark_fixed(update_conn, map_md5)
                
                
async def mark_fixed(session, md5):
    await session.execute(f"UPDATE maps_lack SET fixed=1 where md5=:md5", {"md5": md5})
                
async def ensure_osu_file(bid):
    beatmap_path = Path(osu_folder_path) / f"{str(bid)}.osu"
    if (not beatmap_path.exists()):
        url = f"https://old.ppy.sh/osu/{bid}"
        log_green(f"Downloading: {url}")
        async with app.state.services.http_client.get(url) as resp:
            beatmap_path.write_bytes(await resp.read())
            
def log_green(message: str):
    log(message, Ansi.GREEN)
    
def log_red(message: str):
    log(message, Ansi.RED)
    
async def main():
    await app.state.services.database.connect()
    app.state.services.http_client = aiohttp.ClientSession(
            json_serialize=lambda x: orjson.dumps(x).decode(),
        )
    
    while True:
        await fetch_beatmaps()
        time.sleep(60) # Five maps in a minute

if __name__ == '__main__':
    loop =  asyncio.get_event_loop()
    loop.run_until_complete(main())
    
    