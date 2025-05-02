from datetime import datetime
import hashlib
import struct
from typing import Optional, Union
from circleguard import Circleguard, ReplayString
from slider import Beatmap

from app import settings
from pathlib import Path
import app
from app.constants.gamemodes import GameMode
from app.constants.mods import Mods
from app.constants.privileges import Privileges
from app.logging import Ansi, log
from app.objects.player import Player
from app.objects.score import Score
from app.repositories import scores_suspicion
from app.repositories.scores_suspicion import SuspicionKind


REPLAYS_PATH = Path.cwd() / ".data/osr"
BEATMAPS_PATH = Path.cwd() / ".data/osu"
DATETIME_OFFSET = 0x89F7FF5F7B58000
FROZEN_MSG = "We have detected potential violations of our experimental anticheat associated with your account, and it has been placed under restricted mode. This does not imply that your account has been banned, but we kindly request you to upload a verify video as instructed."
FROZEN_MSG_1 = "Please click the link below to access more information https://gist.github.com/TrueRou/e492f56c2f86445d1578337379b442b6"

PPCAP = {
    GameMode.VANILLA_OSU: 1000,
    GameMode.VANILLA_TAIKO: 1000,
    GameMode.VANILLA_CATCH: 1100,
    GameMode.VANILLA_MANIA: 1600,
    GameMode.RELAX_OSU: 1600,
    GameMode.RELAX_TAIKO: 1000,
    GameMode.RELAX_CATCH: 1000,
    GameMode.AUTOPILOT_OSU: 1000,
}

circleguard = Circleguard(settings.OSU_API_KEY)
frametime_limition = 14
vanilla_ur_limition = 60
snaps_limition = 20


def _parse_score(score: Score) -> tuple[ReplayString, Optional[Beatmap]] | None:
    if score.bmap is None:
        log(f"score without beatmap ({score.id}), skipped.", Ansi.RED)
        return None

    if score.player is None:
        log(f"score without player ({score.id}), skipped.", Ansi.RED)
        return None

    replay_file = REPLAYS_PATH / f"{score.id}.osr"
    beatmap_file = BEATMAPS_PATH / f"{score.bmap.id}.osu"
    raw_replay_data = replay_file.read_bytes()
    replay_md5 = hashlib.md5(
        "{}p{}o{}o{}t{}a{}r{}e{}y{}o{}u{}{}{}".format(
            score.n100 + score.n300,
            score.n50,
            score.ngeki,
            score.nkatu,
            score.nmiss,
            score.bmap.md5,
            score.max_combo,
            str(score.perfect == 1),
            score.player.name,
            score.score,
            0,  # TODO: rank
            score.mods,
            "True",  # TODO: ??
        ).encode(),
    ).hexdigest()
    # create a buffer to construct the replay output
    replay_data = bytearray()
    # pack first section of headers.
    replay_data += struct.pack(
        "<Bi",
        GameMode(score.mode).as_vanilla,
        20200207,
    )  # TODO: osuver
    replay_data += app.packets.write_string(score.bmap.md5)
    replay_data += app.packets.write_string(score.player.name)
    replay_data += app.packets.write_string(replay_md5)
    replay_data += struct.pack(
        "<hhhhhhihBi",
        score.n300,
        score.n100,
        score.n50,
        score.ngeki,
        score.nkatu,
        score.nmiss,
        score.score,
        score.max_combo,
        score.perfect,
        score.mods,
    )
    replay_data += b"\x00"  # TODO: hp graph
    timestamp = int(score.server_time.timestamp() * 1e7)
    replay_data += struct.pack("<q", timestamp + DATETIME_OFFSET)
    # pack the raw replay data into the buffer
    replay_data += struct.pack("<i", len(raw_replay_data))
    replay_data += raw_replay_data
    # pack additional info buffer.
    replay_data += struct.pack("<q", score.id)
    try:
        cg_beatmap = Beatmap.from_path(beatmap_file)
        return circleguard.ReplayString(replay_data), cg_beatmap
    except ValueError:
        log(f"Failed to load beatmap ({beatmap_file}), skipped.", Ansi.RED)
        return None


async def _save_suspicion(player: Player, score: Score, kind: SuspicionKind, reason: str, detail: dict) -> None:
    # PP threshold exceeded but player is not whitelisted.
    if kind == SuspicionKind.PPCAP and not player.priv & Privileges.WHITELISTED:
        await player.restrict(app.state.sessions.bot, "suspicion detected, temporally restrict the player.")
    if score.id is None:
        log(
            f"suspicious score with no id: ({player.name}), kind: {kind}, reason: {reason}, detail: {detail}",
            Ansi.RED,
        )
        return
    await scores_suspicion.create(score.id, kind, reason, detail)


async def validate_checksum(
    unique_ids: str,
    osu_version: str,
    client_hash_decoded: str,
    storyboard_md5: str,
    bmap_md5: str,
    updated_beatmap_hash: str,
    player: Player,
    score: Score,
) -> None:
    unique_id1, unique_id2 = unique_ids.split("|", maxsplit=1)
    unique_id1_md5 = hashlib.md5(unique_id1.encode()).hexdigest()
    unique_id2_md5 = hashlib.md5(unique_id2.encode()).hexdigest()

    server_score_checksum = "unknown"
    try:
        assert player.client_details is not None

        if osu_version != f"{player.client_details.osu_version.date:%Y%m%d}":
            raise ValueError("osu! version mismatch")

        if client_hash_decoded != player.client_details.client_hash:
            raise ValueError("client hash mismatch")
        # assert unique ids (c1) are correct and match login params
        if unique_id1_md5 != player.client_details.uninstall_md5:
            raise ValueError(
                f"unique_id1 mismatch ({unique_id1_md5} != {player.client_details.uninstall_md5})",
            )

        if unique_id2_md5 != player.client_details.disk_signature_md5:
            raise ValueError(
                f"unique_id2 mismatch ({unique_id2_md5} != {player.client_details.disk_signature_md5})",
            )

        # assert online checksums match
        server_score_checksum = score.compute_online_checksum(
            osu_version=osu_version,
            osu_client_hash=client_hash_decoded,
            storyboard_checksum=storyboard_md5 or "",
        )
        if score.client_checksum != server_score_checksum:
            raise ValueError(
                f"online score checksum mismatch ({server_score_checksum} != {score.client_checksum})",
            )

        # assert beatmap hashes match
        if bmap_md5 != updated_beatmap_hash:
            raise ValueError(
                f"beatmap hash mismatch ({bmap_md5} != {updated_beatmap_hash})",
            )

    except AssertionError as error:
        log(f"invalid player client_details ({player.name})", Ansi.RED)

    except ValueError as error:
        detail = {
            "unique_id1": unique_id1,
            "unique_id2": unique_id2,
            "unique_id1_md5": unique_id1_md5,
            "unique_id2_md5": unique_id2_md5,
            "osu_version": osu_version,
            "client_hash_decoded": client_hash_decoded,
            "client_checksum": score.client_checksum,
            "server_checksum": server_score_checksum,
        }

        await _save_suspicion(player, score, SuspicionKind.HASH, error.args[0], detail)


async def validate_replay(player: Player, score: Score) -> None:
    try:
        has_relax = score.mods & Mods.RELAX or score.mods & Mods.AUTOPILOT
        r = _parse_score(score)

        if r is None:
            log(
                f"Failed to parse score ({score.id} by {player.name}), skipped.",
                Ansi.RED,
            )
            return

        replay, _beatmap = r

        beatmap = _beatmap or score.bmap

        if beatmap is None:
            log(
                f"Failed to load beatmap of score ({score.id}), skipped.",
                Ansi.RED,
            )
            return

        frametime = circleguard.frametime(replay)
        ur = circleguard.ur(replay, beatmap=beatmap) if not has_relax else 0
        snaps = circleguard.snaps(replay, beatmap=beatmap)
        snaps_size = len(list(snaps))
        detail = {"frametime": frametime, "ur": ur, "snaps": snaps_size}
        if (not has_relax) and frametime < frametime_limition:
            await _save_suspicion(
                player,
                score,
                SuspicionKind.REPLAY,
                f"timewarp cheating (frametime: {frametime:.2f}) / {frametime_limition})",
                detail,
            )

        if (not has_relax) and ur < vanilla_ur_limition:
            await _save_suspicion(
                player,
                score,
                SuspicionKind.REPLAY,
                f"potential relax (ur: {ur:.2f} / {vanilla_ur_limition})",
                detail,
            )

        if snaps_size > snaps_limition:
            await _save_suspicion(
                player,
                score,
                SuspicionKind.REPLAY,
                f"potential assist (snaps: {snaps_size:.2f} / {snaps_limition})",
                detail,
            )

        # bmap.status in [Ranked, Approved]
        assert score.bmap is not None
        if score.bmap.awards_ranked_pp and score.pp > PPCAP[score.mode]:
            await _save_suspicion(
                player,
                score,
                SuspicionKind.PPCAP,
                f"ppcap threshold exceeded (pp: {score.pp:.2f} / {PPCAP[score.mode]})",
                detail,
            )
    except:
        log(
            f"Failed to check the score ({score.id} by {score.player.name if score.player is not None else 'unknown'}), skipped.",
            Ansi.RED,
        )
