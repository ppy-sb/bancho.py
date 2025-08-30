from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi import Request
from sqlalchemy import update

import app
from app.api.v2.common import responses
from app.api.v2.common.responses import Failure
from app.api.v2.common.responses import Success
from app.objects.beatmap import Beatmap
from app.repositories import mail as mail_repo
from app.repositories.mail import MailTable

router = APIRouter(tags=["API ppy.sb"], prefix="/sb")


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
        affected_maps: list[Beatmap] = []
        if hash := request.query_params.get("hash"):
            if bmap := app.state.cache.beatmap.get(hash):
                app.state.cache.beatmap.pop(hash, None)
                affected_maps.append(bmap)
        elif bid := request.query_params.get("bid"):
            maps = [m for m in app.state.cache.beatmap.values() if m.id == int(bid)]
            affected_maps.extend(maps)
            for m in maps:
                app.state.cache.beatmap.pop(m.md5, None)
        else:
            return responses.failure(message="Either hash or bid must be provided.")
        affected_sets = set(m.set_id for m in affected_maps)
        for set_id in affected_sets:
            app.state.cache.beatmapset.pop(set_id, None)
        return responses.success({}, meta={"affected": len(affected_maps)})
    return responses.failure(message="Cache flushing is not implemented for this type.")
