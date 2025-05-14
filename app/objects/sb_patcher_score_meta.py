import asyncio
from dataclasses import dataclass
from typing import Any, Coroutine, Never, Optional, Union, TypeGuard, Callable, Self

from app.objects.beatmap import Beatmap
from app.objects.score import Score

start = int
duration = int


@dataclass
class SbPatcherScoreMetaRawV1:
    pauses: list[tuple[start, duration]] | None  # start, duration (in ms)

    def any_data(self) -> bool:
        return self.pauses is not None

    def trim_pauses(
        self, map_duration_ms: int | None, trim_start_ms: int = 0
    ) -> list[tuple[start, duration]] | None:
        if self.pauses is None:
            return None

        return [
            pause
            for pause in self.pauses
            if pause[0] > trim_start_ms
            and (pause[0] < map_duration_ms if map_duration_ms is not None else True)
        ]


SbPatcherScoreMetaRaw = Union[SbPatcherScoreMetaRawV1]
Job = Callable[[], Coroutine[Any, Any, None] | None]


class _SbPatcherScoreMeta:
    raw: SbPatcherScoreMetaRaw
    no_pause: bool | None
    strict_no_pause: bool | None

    score: Optional[Score] = None
    beatmap_meta: Optional[Beatmap] = None

    _should_save = False

    def __init__(
        self,
        *,
        raw: SbPatcherScoreMetaRaw,
    ):
        self.raw = raw

    def collect_score(self, score: Score) -> Self:
        self.score = score
        if score.bmap:
            self.collect_beatmap_meta(score.bmap)
        return self

    def collect_beatmap_meta(self, beatmap_meta: Beatmap) -> Self:
        self.beatmap_meta = beatmap_meta
        return self

    async def run_jobs(self) -> None:
        jobs: list[Job] = [
            self.ensure_raw_meta,
            self.ensure_score,
            self.compute_pause_data,
        ]

        for job in jobs:
            if asyncio.iscoroutinefunction(job):
                await job()
            else:
                job()
            if not self._should_save:
                break

    def compute_pause_data(self) -> None:
        intersected = self.raw.trim_pauses(
            trim_start_ms=8000,
            map_duration_ms=self.beatmap_meta.total_length * 1000 if self.beatmap_meta else None,
        )

        if not intersected:
            self._should_save = False
            return

        self.no_pause = len(intersected) == 0  # TODO parse osu beatmap to compute exclude break time
        self.strict_no_pause = len(intersected) == 0

    def ensure_raw_meta(self) -> None:
        if not self.raw.any_data():
            self._should_save = False

    def ensure_score(self) -> None:
        if not self.score:
            self._should_save = False


class SbPatcherScoreMeta(_SbPatcherScoreMeta):
    id: int = 0

    def collect_score_id(self, id: int) -> Self:
        self.id = id
        return self

    def collect_score(self, score: Score) -> Self:
        self.score = score
        if score.bmap:
            self.collect_beatmap_meta(score.bmap)
        if score.id:
            self.collect_score_id(score.id)

        return self

    async def run_jobs(self) -> None:
        jobs: list[Job] = [super().run_jobs]

        for job in jobs:
            if asyncio.iscoroutinefunction(job):
                await job()
            else:
                job()
            if not self._should_save:
                break

    @staticmethod
    def all_set(input: "SbPatcherScoreMeta") -> "TypeGuard[SealedSbPatcherScoreMeta]":
        return input._should_save is True and input.no_pause is not None and input.strict_no_pause is not None

    async def seal(self) -> "SealedSbPatcherScoreMeta | None":
        await self.run_jobs()

        if not SbPatcherScoreMeta.all_set(self):
            return None

        return SealedSbPatcherScoreMeta(
            id=self.id,
            no_pause=self.no_pause,
            strict_no_pause=self.strict_no_pause,
            raw=self.raw,
        )


@dataclass
class SealedSbPatcherScoreMeta:
    id: int
    no_pause: bool
    strict_no_pause: bool
    raw: SbPatcherScoreMetaRaw


def no(msg: str) -> Never:
    raise ValueError(msg)
