from dataclasses import dataclass
from typing import TypeGuard, Annotated, cast
from fastapi.requests import Request

from app.objects.beatmap import Beatmap
from app.objects.player import Player
from app.objects.score import Score


@dataclass
class OsuSubmitModularRaw:
    """
    This class is responsible for collecting score submission data
    """

    exited_out: bool
    fail_time: int
    visual_settings_b64: bytes
    updated_beatmap_hash: str
    iv_b64: bytes
    unique_ids: str
    score_time: int
    pw_md5: str
    osu_version: str
    client_hash: str
    storyboard_md5: str | None = None
    fl_cheat_screenshot: bytes | None = None
    token: str | None = None  # ppysb feature: none when using ppysb client


@dataclass
class OsuSubmitModularContextBase:
    request: Request
    raw: OsuSubmitModularRaw


@dataclass
class OsuSubmitModularContext(OsuSubmitModularContextBase):

    score: Score | None = None
    map: Beatmap | None = None
    player: Player | None = None

    def is_post_submit(
        self,
        input: "OsuSubmitModularContext",
    ) -> TypeGuard["OsuSubmitModularContextPostSubmit"]:
        return input.score is not None and input.map is not None and input.player is not None

    def cast_post_submit(self) -> "OsuSubmitModularContextPostSubmit":
        return cast(OsuSubmitModularContextPostSubmit, self)


@dataclass
class OsuSubmitModularContextPostSubmit(OsuSubmitModularContextBase):
    """
    This will be passed around for sb features
    """

    score: Score
    map: Beatmap
    player: Player
