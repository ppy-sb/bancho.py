from __future__ import annotations

import asyncio
import multiprocessing
import threading
import time
import traceback
import zlib
from collections import OrderedDict
from collections.abc import Iterable
from itertools import count
from typing import Any

from rosu_pp_py import Beatmap

from app.logging import Ansi
from app.logging import log
from app.usecases.performance.calculator import PerformanceResult
from app.usecases.performance.calculator import ScoreParams
from app.usecases.performance.calculator import calculate_performances

_STOP = None


class BeatmapCache:
    def __init__(self, max_size: int, ttl: float) -> None:
        self._max_size = max_size
        self._ttl = ttl
        self._entries: OrderedDict[str, tuple[float, Beatmap]] = OrderedDict()

    def get(self, osu_file_path: str) -> Beatmap:
        now = time.monotonic()
        expired_paths = [
            path for path, (expires_at, _) in self._entries.items() if expires_at <= now
        ]
        for path in expired_paths:
            del self._entries[path]

        entry = self._entries.get(osu_file_path)
        if entry is not None:
            self._entries.move_to_end(osu_file_path)
            return entry[1]

        beatmap = Beatmap(path=osu_file_path)
        self._entries[osu_file_path] = (now + self._ttl, beatmap)
        if len(self._entries) > self._max_size:
            self._entries.popitem(last=False)

        return beatmap


def performance_worker(
    worker_id: int,
    task_queue: Any,
    result_queue: Any,
    cache_size: int,
    cache_ttl: float,
) -> None:
    cache = BeatmapCache(cache_size, cache_ttl)
    while True:
        task = task_queue.get()
        if task is _STOP:
            return

        task_id, osu_file_path, scores = task
        try:
            beatmap = cache.get(osu_file_path)
            result = calculate_performances(osu_file_path, scores, beatmap)
        except BaseException as exc:
            result_queue.put(
                (
                    task_id,
                    worker_id,
                    None,
                    f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                ),
            )
        else:
            result_queue.put((task_id, worker_id, result, None))


class PerformanceWorkerError(Exception):
    def __init__(self, worker_id: int, message: str) -> None:
        super().__init__(message)
        self.worker_id = worker_id


class PerformanceProcessPool:
    def __init__(self) -> None:
        self._processes: list[Any] = []
        self._task_queues: list[Any] = []
        self._result_queue: Any | None = None
        self._result_thread: threading.Thread | None = None
        self._pending: dict[int, asyncio.Future[list[PerformanceResult]]] = {}
        self._task_ids = count()
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self, workers: int, cache_size: int, cache_ttl: float) -> None:
        if self._processes:
            return

        context = multiprocessing.get_context("spawn")
        self._loop = asyncio.get_running_loop()
        self._result_queue = context.Queue()
        for worker_id in range(workers):
            task_queue = context.Queue()
            process = context.Process(
                target=performance_worker,
                args=(
                    worker_id,
                    task_queue,
                    self._result_queue,
                    cache_size,
                    cache_ttl,
                ),
                name=f"performance-worker-{worker_id}",
            )
            process.start()
            self._task_queues.append(task_queue)
            self._processes.append(process)

        self._result_thread = threading.Thread(
            target=self._collect_results,
            name="performance-results",
            daemon=True,
        )
        self._result_thread.start()

    async def stop(self) -> None:
        if not self._processes:
            return

        for task_queue in self._task_queues:
            task_queue.put(_STOP)
        await asyncio.gather(
            *(asyncio.to_thread(process.join) for process in self._processes),
        )

        if self._result_queue is not None:
            self._result_queue.put(_STOP)
        if self._result_thread is not None:
            await asyncio.to_thread(self._result_thread.join)

        self._processes.clear()
        self._task_queues.clear()
        self._result_queue = None
        self._result_thread = None
        self._loop = None

    async def calculate(
        self,
        osu_file_path: str,
        scores: Iterable[ScoreParams],
    ) -> list[PerformanceResult]:
        if self._loop is None or not self._task_queues:
            raise RuntimeError("Performance process pool is not running")

        score_list = list(scores)
        task_id = next(self._task_ids)
        future = self._loop.create_future()
        self._pending[task_id] = future

        worker_id = zlib.crc32(osu_file_path.encode()) % len(self._task_queues)
        try:
            self._task_queues[worker_id].put((task_id, osu_file_path, score_list))
        except BaseException as exc:
            self._pending.pop(task_id, None)
            return self._fallback(
                worker_id,
                osu_file_path,
                score_list,
                f"failed to submit task: {type(exc).__name__}: {exc}",
            )

        try:
            return await future
        except PerformanceWorkerError as exc:
            return self._fallback(
                exc.worker_id,
                osu_file_path,
                score_list,
                str(exc),
            )
        finally:
            self._pending.pop(task_id, None)

    @staticmethod
    def _fallback(
        worker_id: int,
        osu_file_path: str,
        scores: list[ScoreParams],
        reason: str,
    ) -> list[PerformanceResult]:
        log(
            "Performance worker failed; falling back to uncached calculation "
            f"in the main process: worker={worker_id}, "
            f"beatmap={osu_file_path}, error={reason}",
            Ansi.LYELLOW,
        )
        return calculate_performances(osu_file_path, scores)

    def _collect_results(self) -> None:
        assert self._result_queue is not None
        assert self._loop is not None
        while True:
            result = self._result_queue.get()
            if result is _STOP:
                return

            task_id, worker_id, performances, error = result
            self._loop.call_soon_threadsafe(
                self._complete_task,
                task_id,
                worker_id,
                performances,
                error,
            )

    def _complete_task(
        self,
        task_id: int,
        worker_id: int,
        performances: list[PerformanceResult] | None,
        error: str | None,
    ) -> None:
        future = self._pending.get(task_id)
        if future is None or future.done():
            return

        if error is not None:
            future.set_exception(PerformanceWorkerError(worker_id, error))
        else:
            assert performances is not None
            future.set_result(performances)
