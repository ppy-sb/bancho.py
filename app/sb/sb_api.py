from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.param_functions import Query
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials as HTTPCredentials
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select, update

import app
from app import settings
from app.api.v2.common import responses
from app.api.v2.common.json import ORJSONResponse
from app.api.v2.common.responses import Failure, Success
from app.constants.gamemodes import GameMode
from app.constants.mods import Mods
from app.constants.privileges import Privileges
from app.objects.beatmap import (
    Beatmap,
    disk_has_expected_osu_file,
    ensure_osu_file_is_available,
)
from app.repositories import mail as mail_repo
from app.repositories import stats as stats_repo
from app.repositories.mail import MailTable
from app.repositories.maps import MapsTable
from app.repositories.scores import READ_PARAMS, ScoresTable
from app.repositories.users import UsersTable
from app.usecases.performance import ScoreParams

router = APIRouter(tags=["API ppy.sb"], prefix="/sb")
http_bearer_scheme = HTTPBearer(auto_error=False)


@router.get("/user-scores")
async def user_best_scores(
    *,
    email: str = Query(..., min_length=3, max_length=254),
    mode: int = Query(..., ge=0, le=11),
    token: HTTPCredentials | None = Depends(http_bearer_scheme),
) -> Response:
    """Return up to 200 best scores for the unrestricted user with this email."""
    if (
        token is None
        or settings.TRUSTED_SECRET is None
        or not compare_digest(token.credentials, settings.TRUSTED_SECRET)
    ):
        return ORJSONResponse(
            {"status": "Invalid trusted secret."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    query = (
        select(*READ_PARAMS, UsersTable.name.label("user_name"))
        .select_from(ScoresTable)
        .join(UsersTable, UsersTable.id == ScoresTable.userid)
        .where(
            UsersTable.email == email.strip(),
            UsersTable.priv.op("&")(Privileges.UNRESTRICTED.value) != 0,
            ScoresTable.mode == mode,
            ScoresTable.status == 2,
        )
        .order_by(
            ScoresTable.pp.desc(),
            ScoresTable.score.desc(),
            ScoresTable.id.desc(),
        )
        .limit(200)
    )
    rows = await app.state.services.database.fetch_all(query)

    def serialize(row: dict[str, Any]) -> dict[str, Any]:
        played_at = row["play_time"]
        if isinstance(played_at, datetime):
            played_at = played_at.replace(tzinfo=timezone.utc).isoformat()
        return {
            "id": str(row["id"]),
            "status": row["status"],
            "userId": row["userid"],
            "userName": row["user_name"],
            "mapHash": row["map_md5"].strip(),
            "mode": row["mode"],
            "mods": row["mods"],
            "score": row["score"],
            "pp": row["pp"],
            "accuracy": row["acc"],
            "combo": row["max_combo"],
            "n300": row["n300"],
            "n100": row["n100"],
            "n50": row["n50"],
            "miss": row["nmiss"],
            "geki": row["ngeki"],
            "katu": row["nkatu"],
            "grade": row["grade"],
            "perfect": bool(row["perfect"]),
            "playedAt": played_at,
            "checksum": row["online_checksum"],
        }

    return ORJSONResponse({"scores": [serialize(row) for row in rows]})


@router.get("/map-scores")
async def map_best_scores(
    *,
    mapHash: str = Query(..., min_length=32, max_length=32),
    mode: int = Query(..., ge=0, le=11),
    country: str | None = Query(None, min_length=2, max_length=2),
    mods: int | None = Query(None, ge=0),
    rank: Literal["score", "pp"] = Query("score"),
    token: HTTPCredentials | None = Depends(http_bearer_scheme),
) -> Response:
    if (
        token is None
        or settings.TRUSTED_SECRET is None
        or not compare_digest(token.credentials, settings.TRUSTED_SECRET)
    ):
        return ORJSONResponse(
            {"status": "Invalid trusted secret."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if any(c not in "0123456789abcdefABCDEF" for c in mapHash):
        return ORJSONResponse({"status": "Invalid map hash."}, status_code=422)

    query = (
        select(
            *READ_PARAMS,
            UsersTable.name.label("user_name"),
            UsersTable.email.label("binding_email"),
        )
        .select_from(ScoresTable)
        .join(UsersTable, UsersTable.id == ScoresTable.userid)
        .where(
            ScoresTable.map_md5 == mapHash,
            ScoresTable.mode == mode,
            ScoresTable.status == 2,
            UsersTable.priv.op("&")(Privileges.UNRESTRICTED.value) != 0,
        )
    )
    if country is not None:
        query = query.where(UsersTable.country == country)
    if mods is not None:
        query = query.where(ScoresTable.mods == mods)
    metric = ScoresTable.pp if rank == "pp" else ScoresTable.score
    query = query.order_by(
        metric.desc(), ScoresTable.score.desc(), ScoresTable.id.desc()
    ).limit(200)
    rows = await app.state.services.database.fetch_all(query)
    scores = []
    for row in rows:
        played_at = row["play_time"]
        if isinstance(played_at, datetime):
            played_at = played_at.replace(tzinfo=timezone.utc).isoformat()
        scores.append(
            {
                "id": str(row["id"]),
                "status": row["status"],
                "userId": row["userid"],
                "userName": row["user_name"],
                "mapHash": row["map_md5"].strip(),
                "mode": row["mode"],
                "mods": row["mods"],
                "score": row["score"],
                "pp": row["pp"],
                "accuracy": row["acc"],
                "combo": row["max_combo"],
                "n300": row["n300"],
                "n100": row["n100"],
                "n50": row["n50"],
                "miss": row["nmiss"],
                "geki": row["ngeki"],
                "katu": row["nkatu"],
                "grade": row["grade"],
                "perfect": bool(row["perfect"]),
                "playedAt": played_at,
                "checksum": row["online_checksum"],
                "bindingKey": hmac_new(
                    settings.TRUSTED_SECRET.encode(),
                    row["binding_email"].strip().lower().encode(),
                    sha256,
                ).hexdigest(),
            }
        )
    return ORJSONResponse({"scores": scores})


class ScoreWriteback(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    map_hash: str = Field(pattern=r"^[a-fA-F0-9]{32}$")
    checksum: str = Field(min_length=1, max_length=128)
    mode: int = Field(ge=0, le=11)
    mods: int = Field(ge=0)
    score: int = Field(ge=0)
    pp: float = Field(ge=0)
    accuracy: float = Field(ge=0, le=100)
    combo: int = Field(ge=0)
    n300: int = Field(ge=0)
    n100: int = Field(ge=0)
    n50: int = Field(ge=0)
    miss: int = Field(ge=0)
    geki: int = Field(ge=0)
    katu: int = Field(ge=0)
    grade: Literal["A", "B", "C", "D", "S", "SH", "X", "XH", "F"]
    perfect: bool
    passed: bool
    played_at: datetime
    time_elapsed: int = Field(ge=0)
    client_flags: int = Field(ge=0)
    replay: str | None = None


@router.post("/score-writeback")
async def score_writeback(
    payload: ScoreWriteback,
    token: HTTPCredentials | None = Depends(http_bearer_scheme),
) -> Response:
    if (
        token is None
        or settings.TRUSTED_SECRET is None
        or not compare_digest(token.credentials, settings.TRUSTED_SECRET)
    ):
        return ORJSONResponse(
            {"status": "Invalid trusted secret."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if (
        payload.played_at.tzinfo is None
        or payload.mode not in GameMode.valid_gamemodes()
        or GameMode.from_params(payload.mode % 4, Mods(payload.mods)).value
        != payload.mode
    ):
        return ORJSONResponse(
            {"status": "Invalid score mode or time."}, status_code=422
        )
    if payload.replay is not None and len(payload.replay) > 5_500_000:
        return ORJSONResponse({"status": "Replay too large."}, status_code=413)
    try:
        replay = b64decode(payload.replay, validate=True) if payload.replay else None
    except Base64Error:
        return ORJSONResponse({"status": "Invalid replay."}, status_code=422)
    if payload.passed and (replay is None or not 24 <= len(replay) <= 4_000_000):
        return ORJSONResponse(
            {"status": "Replay required for passed scores."}, status_code=422
        )

    user = await app.state.services.database.fetch_one(
        select(UsersTable.id, UsersTable.country, UsersTable.priv).where(
            UsersTable.email == payload.email.strip(),
        )
    )
    if user is None or not user["priv"] & Privileges.UNRESTRICTED.value:
        return ORJSONResponse(
            {"status": "No eligible account for email."}, status_code=404
        )
    bmap = await Beatmap.from_md5(payload.map_hash)
    if bmap is None:
        return ORJSONResponse({"status": "Beatmap unavailable."}, status_code=422)
    if await stats_repo.fetch_one(user["id"], payload.mode) is None:
        return ORJSONResponse(
            {"status": "Score mode unavailable for account."}, status_code=422
        )
    source_pp = 0.0
    if payload.passed:
        if not await ensure_osu_file_is_available(
            bmap.id, expected_md5=payload.map_hash
        ) or not disk_has_expected_osu_file(bmap.id, payload.map_hash):
            return ORJSONResponse(
                {"status": "Beatmap file unavailable."}, status_code=422
            )
        performances = await app.usecases.performance.calculate_performances(
            osu_file_path=str(Path.cwd() / ".data/osu" / f"{bmap.id}.osu"),
            scores=[
                ScoreParams(
                    mode=payload.mode % 4,
                    mods=payload.mods,
                    combo=payload.combo,
                    ngeki=payload.geki,
                    n300=payload.n300,
                    nkatu=payload.katu,
                    n100=payload.n100,
                    n50=payload.n50,
                    nmiss=payload.miss,
                    legacy_total_score=payload.score,
                )
            ],
        )
        source_pp = performances[0]["performance"]["pp"]

    db = app.state.services.database
    async with app.state.score_submission_locks[payload.checksum]:
        async with db.transaction():
            duplicate = await db.fetch_one(
                select(ScoresTable.id, ScoresTable.userid).where(
                    ScoresTable.online_checksum == payload.checksum,
                )
            )
            if duplicate is not None:
                if duplicate["userid"] != user["id"]:
                    return ORJSONResponse(
                        {"status": "Checksum belongs to another account."},
                        status_code=409,
                    )
                score_id = duplicate["id"]
                inserted = False
            else:
                metric = ScoresTable.pp if payload.mode >= 4 else ScoresTable.score
                previous = await db.fetch_one(
                    select(ScoresTable.id, metric.label("metric"))
                    .where(
                        ScoresTable.userid == user["id"],
                        ScoresTable.map_md5 == payload.map_hash,
                        ScoresTable.mode == payload.mode,
                        ScoresTable.status == 2,
                    )
                    .order_by(metric.desc(), ScoresTable.id.desc())
                    .limit(1)
                )
                value = source_pp if payload.mode >= 4 else payload.score
                score_status = (
                    0
                    if not payload.passed
                    else 2
                    if previous is None or value > previous["metric"]
                    else 1
                )
                if score_status == 2:
                    await db.execute(
                        update(ScoresTable)
                        .where(
                            ScoresTable.userid == user["id"],
                            ScoresTable.map_md5 == payload.map_hash,
                            ScoresTable.mode == payload.mode,
                            ScoresTable.status == 2,
                        )
                        .values(status=1)
                    )
                score_id = await db.execute(
                    ScoresTable.__table__.insert().values(
                        map_md5=payload.map_hash,
                        score=payload.score,
                        pp=source_pp,
                        acc=payload.accuracy,
                        max_combo=payload.combo,
                        mods=payload.mods,
                        n300=payload.n300,
                        n100=payload.n100,
                        n50=payload.n50,
                        nmiss=payload.miss,
                        ngeki=payload.geki,
                        nkatu=payload.katu,
                        grade=payload.grade,
                        status=score_status,
                        mode=payload.mode,
                        play_time=payload.played_at.astimezone(timezone.utc).replace(
                            tzinfo=None
                        ),
                        time_elapsed=payload.time_elapsed,
                        client_flags=payload.client_flags,
                        userid=user["id"],
                        perfect=payload.perfect,
                        online_checksum=payload.checksum,
                    )
                )
                await db.execute(
                    update(MapsTable)
                    .where(MapsTable.md5 == payload.map_hash)
                    .values(
                        plays=MapsTable.plays + 1,
                        passes=MapsTable.passes + int(payload.passed),
                    )
                )
                inserted = True
        if inserted:
            bmap.plays += 1
            bmap.passes += int(payload.passed)
        if replay is not None:
            replay_path = Path.cwd() / ".data/osr" / f"{score_id}.osr"
            replay_path.parent.mkdir(parents=True, exist_ok=True)
            if not replay_path.exists():
                replay_path.write_bytes(replay)
        if player := app.state.sessions.players.get(id=user["id"]):
            mode = GameMode(payload.mode)
            await player.recalc_stats_sql(mode)
            await player.update_rank(mode)
            player.enqueue(app.packets.user_stats(player))
        else:
            await stats_repo.sql_recalculate_mode(user["id"], payload.mode)
            stat = await stats_repo.fetch_one(user["id"], payload.mode)
            if stat is not None:
                await app.state.services.redis.zadd(
                    f"bancho:leaderboard:{payload.mode}", {str(user["id"]): stat["pp"]}
                )
                await app.state.services.redis.zadd(
                    f"bancho:leaderboard:{payload.mode}:{user['country']}",
                    {str(user["id"]): stat["pp"]},
                )
    return ORJSONResponse(
        {"status": "inserted" if inserted else "duplicate", "scoreId": score_id}
    )


@router.get("/pd/injector")
async def pd_injector_meta_options() -> Success[str]:
    """percyDan injector check allowance"""
    return responses.success("accept")


@router.post("/players/{player_id}/notify")
async def notify_player(
    player_id: int,
) -> Success | Failure:
    """Notify a player that they might have new messages."""
    if target := app.state.sessions.players.get(id=player_id):
        mail_rows = await mail_repo.fetch_all_mail_to_user(player_id, read=False)
        for mail in mail_rows:
            target.enqueue(
                app.packets.send_message(
                    sender=mail["from_name"],
                    msg=mail["msg"],
                    recipient=mail["to_name"],
                    sender_id=mail["from_id"],
                )
            )
            # we consider the mail as read when we notify the online player, in case of duplicate notifications
            await app.state.services.database.execute(
                update(MailTable).where(MailTable.id == mail["id"]).values(read=True)
            )
        return responses.success({}, meta={"online": True, "enqueued": len(mail_rows)})
    return responses.success({}, meta={"online": False})


@router.delete("/cache")
async def flush_caches(
    request: Request,
    type: Literal["bcrypt", "beatmap", "beatmapset", "unsubmitted", "needs_update"],
) -> Success | Failure:
    """Flush the specified cache."""
    if type == "beatmap":
        affected_maps: dict[int, Beatmap] = {}
        if hash := request.query_params.get("hash"):
            # A beatmap is indexed by both md5 and numeric id. Search values
            # as well as the md5 key because the md5 can have changed during
            # an in-place API refresh while the old alias remains present.
            for key, bmap in app.state.cache.beatmap.items():
                if key == hash or bmap.md5 == hash:
                    affected_maps[bmap.id] = bmap
        elif bid := request.query_params.get("bid"):
            for bmap in app.state.cache.beatmap.values():
                if bmap.id == int(bid):
                    affected_maps[bmap.id] = bmap
        else:
            return responses.failure(message="Either hash or bid must be provided.")

        for bmap in affected_maps.values():
            # Remove both the md5 and numeric-id aliases, plus any stale md5
            # aliases which may point to the same beatmap object.
            for key, cached in list(app.state.cache.beatmap.items()):
                if cached.id == bmap.id:
                    app.state.cache.beatmap.pop(key, None)

        affected_sets = {m.set_id for m in affected_maps.values()}
        for set_id in affected_sets:
            app.state.cache.beatmapset.pop(set_id, None)
        return responses.success({}, meta={"affected": len(affected_maps)})
    return responses.failure(message="Cache flushing is not implemented for this type.")
