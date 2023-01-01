import asyncio
import datetime
import os
import sys
import time

sys.path.insert(1, os.path.abspath(os.pardir))
os.chdir(os.path.abspath(os.pardir))

import databases
import app.settings

from app.objects import beatmap
from app.objects.beatmap import Beatmap, BeatmapSet

osu_folder_path = ""

async def fetch_beatmaps():
    async with databases.Database(app.settings.DB_DSN) as db:
        async with (
            db.connection() as select_conn,
            db.connection() as update_conn,
        ):
            # In order to avoid too many requests, we operate five maps in a turn.
            # Order by rand to avoid being stuck in some beatmaps (some issues with mirror maybe)
            for row in await select_conn.fetch_all(f"SELECT * FROM maps_lack where fixed=0 limit 5 order by RAND()"):
                map_md5 = row["md5"]
                lack_type = row["lack_type"]
                if (lack_type == "entry"):
                    if (await select_conn.fetch_val(f"SELECT COUNT(id) FROM maps where md5=:md5", {"md5": map_md5}) > 0):
                        # The map is existed in database, just skip it and marked fixed
                        await mark_fixed(update_conn, map_md5)
                        continue
                    # The map is not existed. Fetch the whole set
                    api_data = await beatmap.api_get_beatmaps(h=map_md5)
                    beatmap_set = BeatmapSet.__new__(BeatmapSet)
                    beatmap_set.id = int(api_data[0]["beatmapset_id"])
                    # Build each beatmap in the set
                    for api_bmap in api_data:
                        bmap: Beatmap = Beatmap.__new__(Beatmap)
                        bmap.id = int(api_bmap["beatmap_id"])
                        bmap._parse_from_osuapi_resp(api_bmap)
                        beatmap_set.maps.append(bmap)
                        # Ensure we have the file or we download it 
                        await ensure_osu_file(bmap.id)
                    # Save all maps into sql (if existed, we will replace it without any exception)
                    await beatmap_set._save_to_sql()
                    log_green(f"[{str(beatmap_set.id)}]Saving {beatmap_set.maps.count()} maps into the database")
                    # Update beatmap set api check information
                    await update_conn.execute(
                        "REPLACE INTO mapsets "
                        "(server, id, last_osuapi_check) "
                        'VALUES ("osu!", :id, :last_osuapi_check)',
                        {"id": beatmap_set.id, "last_osuapi_check": datetime.now()},
                    )
                if (lack_type == "file"):
                    api_data = await beatmap.api_get_beatmaps(h=map_md5)
                    # Ensure each map in the beatmap set and download if necessary
                    for api_bmap in api_data:
                        beatmap_id = int(api_bmap["beatmap_id"])
                        await ensure_osu_file(beatmap_id)
                    await mark_fixed(update_conn, map_md5)
                
                
                
async def mark_fixed(session, md5):
    await session.execute(f"UPDATE maps SET fixed=1 where md5=:md5", {"md5": md5})
                
async def ensure_osu_file(bid):
    osu_file_path = os.path.join(osu_folder_path, str(bid) + ".osu")
    if (not os.path.exists(osu_file_path)):
        url = f"https://old.ppy.sh/osu/{bid}"
        log_green(f"Downloading: {url}")
        async with app.state.services.http_client.get(url) as resp:
            osu_file_path.write_bytes(await resp.read())
            
def log_green(message: str):
    print("\032[31m%s" % message)
    
def log_red(message: str):
    print("\031[31m%s" % message)

if __name__ == '__main__':
    while True:
        try:
            asyncio.run(fetch_beatmaps())
        except:
            log_red("Catch exception when processing some maps. Skipped!")
        time.sleep(60) # Five maps in a minute