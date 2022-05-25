from typing import List

from ppysb_pp_py import CalculateResult, ScoreParams, Calculator

from app.constants.mods import Mods


def calculate(mode_vn: int, osu_file_path: str, params: List[ScoreParams]) -> List[CalculateResult]:
    calculator = Calculator(osu_file_path)
    return_value = []
    for param in params:
        # V2 & NF makes not influence
        if param.mods & Mods.SCOREV2:
            param.mods &= ~Mods.SCOREV2
        if param.mods & Mods.NOFAIL:
            param.mods &= ~Mods.NOFAIL
        print(param)
        (result,) = calculator.calculate(param)
        print(result)
        # Transform map should not gather any pp
        if result.mode != mode_vn:
            result.pp = 0
        # To keep pp value away from database limitation.
        if result.pp > 8192:
            result.pp = 8192
        return_value.append(result)
    return return_value
