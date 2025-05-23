from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from typing import Any

from app.logging import Ansi
from app.logging import log
from app.objects.collections import Channels
from app.objects.collections import Matches
from app.objects.collections import Players

if TYPE_CHECKING:
    from app.objects.player import Player

players = Players()
channels = Channels()
matches = Matches()

api_keys: dict[str, int] = {}
streaming_players: dict[int, bool] = {}

housekeeping_tasks: set[asyncio.Task[Any]] = set()

bot: Player


# use cases


async def cancel_housekeeping_tasks() -> None:
    log(
        f"-> Cancelling {len(housekeeping_tasks)} housekeeping tasks.",
        Ansi.LMAGENTA,
    )

    # cancel housekeeping tasks
    for task in housekeeping_tasks:
        task.cancel()

    await asyncio.gather(*housekeeping_tasks, return_exceptions=True)

    loop = asyncio.get_running_loop()

    for task in housekeeping_tasks:
        if not task.cancelled():
            exception = task.exception()
            if exception:
                loop.call_exception_handler(
                    {
                        "message": "unhandled exception during loop shutdown",
                        "exception": exception,
                        "task": task,
                    },
                )
