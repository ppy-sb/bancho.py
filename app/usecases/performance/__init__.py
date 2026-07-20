from __future__ import annotations

from collections.abc import Iterable

from app.usecases.performance.calculator import DifficultyRating
from app.usecases.performance.calculator import PerformanceRating
from app.usecases.performance.calculator import PerformanceResult
from app.usecases.performance.calculator import ScoreParams
from app.usecases.performance.calculator import get_mode
from app.usecases.performance.multiprocess import PerformanceProcessPool

process_pool = PerformanceProcessPool()


async def calculate_performances(
    osu_file_path: str,
    scores: Iterable[ScoreParams],
) -> list[PerformanceResult]:
    return await process_pool.calculate(osu_file_path, scores)


__all__ = (
    "DifficultyRating",
    "PerformanceRating",
    "PerformanceResult",
    "ScoreParams",
    "calculate_performances",
    "get_mode",
    "process_pool",
)
