from fastapi import APIRouter
from sqlalchemy import update

import app
from app.api.v2.common import responses
from app.api.v2.common.responses import Failure, Success
from app.repositories import mail as mail_repo
from app.repositories.mail import MailTable


router = APIRouter(tags=["API ppy.sb"], prefix="/sb")

@router.post("/players/{player_id}/notify")
async def notify_player(
    player_id: int,
) -> Success | Failure:
    """Notify a player that they might have new messages."""
    if target := app.state.sessions.players.get(id=player_id):
        mail_rows = await mail_repo.fetch_all_mail_to_user(player_id, read=False)
        for mail in mail_rows:
            target.enqueue(app.packets.send_message(sender=mail["from_name"], msg=mail["msg"], recipient=mail["to_name"], sender_id=mail["from_id"]))
            # we consider the mail as read when we notify the online player, in case of duplicate notifications
            await app.state.services.database.execute(update(MailTable).where(MailTable.id == mail["id"]).values(read=True))
        return responses.success({}, meta={"online": True, "enqueued": len(mail_rows)})
    return responses.success({}, meta={"online": False})