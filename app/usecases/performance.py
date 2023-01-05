from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable
from typing import Optional
from typing import TypedDict

from rosu_pp_py import Beatmap
from rosu_pp_py import Calculator

from ppysb_pp_py import Calculator as CalculatorSB
from ppysb_pp_py import ScoreParams as ParamsSB

from app.constants.mods import Mods


@dataclass
class ScoreParams:
    mode: int
    mods: Optional[int] = None
    combo: Optional[int] = None

    # caller may pass either acc OR 300/100/50/geki/katu/miss
    acc: Optional[float] = None

    n300: Optional[int] = None
    n100: Optional[int] = None
    n50: Optional[int] = None
    ngeki: Optional[int] = None
    nkatu: Optional[int] = None
    nmiss: Optional[int] = None
    score: Optional[int] = None


class DifficultyRating(TypedDict):
    performance: float
    star_rating: float


def calculate_performances(
    osu_file_path: str,
    scores: Iterable[ScoreParams],
) -> list[DifficultyRating]:
    calc_bmap = Beatmap(path=osu_file_path)

    results = []

    for score in scores:
        # assert either acc OR 300/100/50/geki/katu/miss is present, but not both
        # if (score.acc is None) == (
        #     score.n300 is None
        #     and score.n100 is None
        #     and score.n50 is None
        #     and score.ngeki is None
        #     and score.nkatu is None
        #     and score.nmiss is None
        # ):
        #     raise ValueError("Either acc OR 300/100/50/geki/katu/miss must be present")
        
        # To avoid some problems
        if score.mods is not None:
            if score.mods & Mods.SCOREV2:
                score.mods &= ~Mods.SCOREV2
            if score.mods & Mods.NOFAIL:
                score.mods &= ~Mods.NOFAIL
        if score.score is None or score.score < 0:
            score.score = 0
            
        sb_param = ParamsSB(mods = score.mods if score.mods is not None else 0, acc=score.acc, n300=score.n300, n100=score.n100, n50=score.n50, nMisses=score.nmiss, nKatu=score.nkatu, combo=score.combo, score=score.score)
      
        # New PP System is not prepared, fallback to old formula      
        result = calculate_aisuru(osu_file_path, sb_param)
        results.append(result)
        continue
            
        calculator = Calculator(
            mode=score.mode,
            mods=score.mods if score.mods is not None else 0,
            combo=score.combo,
            acc=score.acc,
            n300=score.n300,
            n100=score.n100,
            n50=score.n50,
            n_geki=score.ngeki,
            n_katu=score.nkatu,
            n_misses=score.nmiss,
            score=score.score
        )
        
        result = calculator.performance(calc_bmap)

        pp = result.pp
        sr = result.difficulty.stars

        if math.isnan(pp) or math.isinf(pp):
            # TODO: report to logserver
            pp = 0.0
            sr = 0.0
        else:
            pp = round(pp, 5)

        results.append({"performance": pp, "star_rating": sr})

    return results


def calculate_aisuru(
    osu_file_path: str,
    param: ScoreParams,
):
    calculator = CalculatorSB(osu_file_path)
    # V2 & NF makes not influence
    if param.mods & Mods.SCOREV2:
        param.mods &= ~Mods.SCOREV2
    if param.mods & Mods.NOFAIL:
        param.mods &= ~Mods.NOFAIL
    if param.score is None or param.score < 0:
        param.score = 0
    result = {}
    try:
        (result,) = calculator.calculate(param)
    except:
        result.pp = 0
    # To keep pp value away from database limitation.
    if result.pp > 8192:
        result.pp = 8192
        
    return {
        "performance": result.pp,
        "star_rating": result.stars
    }