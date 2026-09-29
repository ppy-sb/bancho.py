from __future__ import annotations

from datetime import datetime, timezone
from hmac import compare_digest
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.param_functions import Query
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials as HTTPCredentials
from fastapi.security import HTTPBearer
from sqlalchemy import select, update

import app
from app import settings
from app.api.v2.common import responses
from app.api.v2.common.json import ORJSONResponse
from app.api.v2.common.responses import Failure, Success
from app.constants.privileges import Privileges
from app.objects.beatmap import Beatmap
from app.repositories import mail as mail_repo
from app.repositories.mail import MailTable
from app.repositories.scores import READ_PARAMS, ScoresTable
from app.repositories.users import UsersTable

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
