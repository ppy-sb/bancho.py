import json
from typing import Any

import app
from app.logging import Ansi
from app.logging import log
from app.repositories import sb_patcher_scores_meta as patcher_scores_repo
from app.objects.sb.patcher_score_meta import SbPatcherScoreMeta, SbPatcherScoreMetaRawV2
from app.usecases.sb.osu_submit_modular_context import OsuSubmitModularContextPostSubmit


async def osu_submit_modular_handler(context: OsuSubmitModularContextPostSubmit) -> None:
    """patcher data handler for sb patcher"""

    form = await context.request.form()

    deprecated_pause = form.get("sbPause")
    if deprecated_pause:
        context.player.enqueue(
            app.packets.notification(
                "your version of sb patcher is outdated, data collected with patcher will not be accepted.\nPlease update to the latest version.",
            ),
        )
        return

    if not (context.score.passed):
        return

    if not context.score.id:
        log("sb_patcher handler called without score id", Ansi.LRED)
        return

    json_sb_patcher_meta = form.get("sb_patcher_meta")
    if not isinstance(json_sb_patcher_meta, str):
        return

    try:
        if json_sb_patcher_meta:
            patcher_meta: dict[str, Any] = json.loads(json_sb_patcher_meta)
            raw = SbPatcherScoreMetaRawV2(p=patcher_meta["p"], h=patcher_meta["h"], v=patcher_meta["v"])

            # save extra metadata
            db_meta = SbPatcherScoreMeta(raw=raw)
            sealed = await (
                db_meta.infer_raw_data()
                .collect_score(context.score)
                .collect_score_id(context.score.id)
                .collect_beatmap_meta(context.map)
                .seal()
            )
            if sealed is not None:
                await patcher_scores_repo.insert_returning_id(
                    id=sealed.id,
                    no_pause=sealed.no_pause,
                    strict_no_pause=sealed.strict_no_pause,
                    hash=sealed.hash,
                    v=sealed.v,
                    raw=sealed.raw,
                )

    except (json.JSONDecodeError, KeyError, ValueError, TypeError, BaseException) as exc:
        match exc:
            case json.JSONDecodeError:
                log(f"Failed to parse sb_patcher_meta JSON: {exc}", Ansi.LRED)
            case KeyError():
                log(f"sb_patcher_meta KeyError: {exc}", Ansi.LRED)
            case ValueError():
                log(f"sb_patcher_meta ValueError: {exc}", Ansi.LRED)
            case TypeError():
                log(f"sb_patcher_meta TypeError: {exc}", Ansi.LRED)
            case _:
                log(f"unknown exception during sb_patcher_meta: {exc}", Ansi.LRED)
