""" bancho.py's v2 apis for interacting with scores """
from __future__ import annotations
from datetime import datetime

from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile
from fastapi import status
from fastapi.param_functions import Query
import app
from app.api.domains.osu import REPLAYS_PATH

from app.api.v2.common import responses
from app.api.v2.common.responses import Success
from app.api.v2.models.scores import Score
from app.constants.gamemodes import GameMode
from app.constants.mods import Mods
from app.objects.beatmap import BEATMAPS_PATH, Beatmap, ensure_local_osu_file
from app.objects.score import Grade, SubmissionStatus
from app.repositories import scores as scores_repo

router = APIRouter()


@router.get("/scores")
async def get_all_scores(
    map_md5: Optional[str] = None,
    mods: Optional[int] = None,
    status: Optional[int] = None,
    mode: Optional[int] = None,
    user_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> Success[list[Score]]:
    scores = await scores_repo.fetch_many(
        map_md5=map_md5,
        mods=mods,
        status=status,
        mode=mode,
        user_id=user_id,
        page=page,
        page_size=page_size,
    )
    total_scores = await scores_repo.fetch_count(
        map_md5=map_md5,
        mods=mods,
        status=status,
        mode=mode,
        user_id=user_id,
    )

    response = [Score.from_mapping(rec) for rec in scores]

    return responses.success(
        content=response,
        meta={
            "total": total_scores,
            "page": page,
            "page_size": page_size,
        },
    )

# @router.post("/scores")
# disabled until authorization complete
async def upload_score(
    replay_file: UploadFile = File(default=None),
    map_md5: str = Form(...),
    score_value: int = Form(..., alias="score"),
    max_combo: int = Form(...),
    mods: int = Form(...),
    n300: int = Form(...),
    n100: int = Form(...),
    n50: int = Form(...),
    nmiss: int = Form(...),
    ngeki: int = Form(...),
    nkatu: int = Form(...),
    mode: int = Form(...),
    perfect: bool = Form(default=False),
    playtime: int = Form(...),
    grade: str = Form(...),
    server: str = Form(...),
    foreign_score_id: int = Form(default=0),
    userid: int = Form(...),
):
    user = {} # recipient_id from auth
    # Check information about outer submission record
    if server not in ["bancho", "ppysb", "akatsuki", "offline"]:
        return {
            "status": 400,
            "msg": "'server' is not in following values: bancho, ppysb, akatsuki, offline",
        }
    if server != 'offline' and foreign_score_id == 0:
        return {
            "status": 400,
            "msg": "Any online scores should have foreign_score_id",
        }
    # Check whether we have the map
    bmap = await Beatmap.from_md5(map_md5)
    if bmap is None:
        return {
            "status": 400,
            "msg": "Couldn't find a beatmap on that md5",
        }
    osu_file_path = BEATMAPS_PATH / f"{bmap.id}.osu"
    await ensure_local_osu_file(osu_file_path, bmap.id, bmap.md5)
    # Make a score manually to calc pp and acc
    score: Score = Score()
    score.score = score_value
    score.max_combo = max_combo
    score.mods = Mods(mods)
    score.n300 = n300
    score.n100 = n100
    score.n50 = n50
    score.nmiss = nmiss
    score.ngeki = ngeki
    score.nkatu = nkatu
    score.mode = GameMode.from_params(mode, score.mods)
    score.server_time = datetime.utcfromtimestamp(float(playtime))
    score.player = await app.state.sessions.players.from_cache_or_sql(id=userid)
    score.grade = Grade.from_str(grade)
    score.bmap = bmap
    score.acc = score.calculate_accuracy()
    score.pp = score.calculate_performance(osu_file_path)[0]
    await score.calculate_status()
    # This is required in the submission like progress
    if score.status == SubmissionStatus.BEST:
        await app.state.services.database.execute(
            "UPDATE scores SET status = 1 "
            "WHERE status = 2 AND map_md5 = :map_md5 "
            "AND userid = :user_id AND mode = :mode",
            {
                "map_md5": score.bmap.md5,
                "user_id": score.player.id,
                "mode": score.mode,
            },
        )
    # Insert score into sql progress
    is_info_table_exist = (
        await app.state.services.database.fetch_one(
            "SELECT table_name FROM information_schema.TABLES WHERE table_name = 'scores_foreign'",
        )
    ) is not None
    new_id = await app.state.services.database.execute(
        "INSERT INTO scores "
        "VALUES (NULL, "
        ":map_md5, :score, :pp, :acc, "
        ":max_combo, :mods, :n300, :n100, "
        ":n50, :nmiss, :ngeki, :nkatu, "
        ":grade, :status, :mode, :play_time, "
        ":time_elapsed, :client_flags, :user_id, :perfect, "
        ":checksum)",
        {
            "map_md5": map_md5,
            "score": score_value,
            "pp": score.pp,
            "acc": score.acc,
            "max_combo": max_combo,
            "mods": mods,
            "n300": n300,
            "n100": n100,
            "n50": n50,
            "nmiss": nmiss,
            "ngeki": ngeki,
            "nkatu": nkatu,
            "grade": score.grade.name,
            "status": score.status,
            "mode": score.mode,
            "play_time": score.server_time,
            "time_elapsed": 0,
            "client_flags": 0,
            "user_id": score.player.id,
            "perfect": perfect,
            "checksum": "foreign score",
        },
    )
    if is_info_table_exist:
        await app.state.services.database.execute(
            "INSERT INTO scores_foreign "
            "VALUES (:id, :server, :foreign_score_id, :recipient_id, :has_replay, FALSE, NOW())",
            {
                "id": new_id,
                "server": server,
                "foreign_score_id": foreign_score_id,
                "recipient_id": user.id,
                "has_replay": replay_file is not None,
            },
        )
    # Download replay into the folder
    if replay_file is not None:
        replay_file_path = REPLAYS_PATH / f"{new_id}.osr"
        replay_file_path.write_bytes(await replay_file.read())
    return {"status": 200, "score_id": new_id}


@router.get("/scores/{score_id}")
async def get_score(score_id: int) -> Success[Score]:
    data = await scores_repo.fetch_one(id=score_id)
    if data is None:
        return responses.failure(
            message="Score not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    response = Score.from_mapping(data)
    return responses.success(response)
