from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Dict, TypedDict

from rosu_pp_py import Beatmap, GameMode, Performance

from app.constants.mods import Mods


@dataclass
class ScoreParams:
    mode: int
    mods: int | None = None
    combo: int | None = None

    # caller may pass either acc OR 300/100/50/geki/katu/miss
    # passing both will result in a value error being raised
    acc: float | None = None

    n300: int | None = None
    n100: int | None = None
    n50: int | None = None
    ngeki: int | None = None
    nkatu: int | None = None
    nmiss: int | None = None


class PerformanceRating(TypedDict):
    pp: float
    pp_acc: float | None
    pp_aim: float | None
    pp_speed: float | None
    pp_flashlight: float | None
    effective_miss_count: float | None
    pp_difficulty: float | None


class DifficultyRating(TypedDict):
    stars: float
    aim: float | None
    speed: float | None
    flashlight: float | None
    slider_factor: float | None
    speed_note_count: float | None
    stamina: float | None
    color: float | None
    rhythm: float | None


class PerformanceResult(TypedDict):
    performance: PerformanceRating
    difficulty: DifficultyRating


gm_dict: dict[int, GameMode] = {
    0: GameMode.Osu,
    1: GameMode.Taiko,
    2: GameMode.Catch,
    3: GameMode.Mania,
}


def get_mode(mode: int) -> GameMode:
    return gm_dict[mode]


def calculate_performances(
    osu_file_path: str,
    scores: Iterable[ScoreParams],
) -> list[PerformanceResult]:
    """\
    Calculate performance for multiple scores on a single beatmap.

    Typically most useful for mass-recalculation situations.

    TODO: Some level of error handling & returning to caller should be
    implemented here to handle cases where e.g. the beatmap file is invalid
    or there an issue during calculation.
    """
    calc_bmap = Beatmap(path=osu_file_path)

    results: list[PerformanceResult] = []

    for score in scores:
        if score.acc and (score.n300 or score.n100 or score.n50 or score.ngeki or score.nkatu):
            raise ValueError(
                "Must not specify accuracy AND 300/100/50/geki/katu. Only one or the other.",
            )

        # rosupp ignores NC and requires DT
        if score.mods is not None:
            if score.mods & Mods.NIGHTCORE:
                score.mods |= Mods.DOUBLETIME

        # solve for converted maps calculation
        calc_bmap.convert(get_mode(score.mode), score.mods)

        score_params = {
            "mods": score.mods or 0,
            "combo": score.combo,
            "accuracy": score.acc,
            "n300": score.n300,
            "n100": score.n100,
            "n50": score.n50,
            "n_geki": score.ngeki,
            "n_katu": score.nkatu,
            "misses": score.nmiss,
            "lazer": False,
        }

        score_params = {k: v for k, v in score_params.items() if v is not None}
        calculator = Performance(**score_params)
        result = calculator.calculate(calc_bmap)

        pp = result.pp

        if math.isnan(pp) or math.isinf(pp) or pp > 9999:
            # TODO: report to logserver
            pp = 0.0
        else:
            pp = round(pp, 3)

        results.append(
            {
                "performance": {
                    "pp": pp,
                    "pp_acc": result.pp_accuracy,
                    "pp_aim": result.pp_aim,
                    "pp_speed": result.pp_speed,
                    "pp_flashlight": result.pp_flashlight,
                    "effective_miss_count": result.effective_miss_count,
                    "pp_difficulty": result.pp_difficulty,
                },
                "difficulty": {
                    "stars": result.difficulty.stars,
                    "aim": result.difficulty.aim,
                    "speed": result.difficulty.speed,
                    "flashlight": result.difficulty.flashlight,
                    "slider_factor": result.difficulty.slider_factor,
                    "speed_note_count": result.difficulty.speed_note_count,
                    "stamina": result.difficulty.stamina,
                    "color": result.difficulty.color,
                    "rhythm": result.difficulty.rhythm,
                },
            },
        )

    return results
