from __future__ import annotations

import lzma
import statistics
from pathlib import Path
from typing import Optional

# 按压时长截断上限 (ms), 超过视为长按误触/暂停等, 不参与统计
MAX_DURATION = 300
# 时长直方图范围 (ms)
HIST_RANGE = 200
# 最低按压数, 低于此值跳过检测 (低样本易偶然形成作弊图形)
MIN_PRESSES = 500
# 峰值规则所需最低按压数
MIN_PRESSES_PEAK = 1500
# 局部极大值判定窗口 (ms)
PEAK_WINDOW = 2
# 频数底过滤比例 (低于 0.1 * 最高峰频数的 bin 忽略)
PEAK_FLOOR_RATIO = 0.1
# 强峰判定比例 (> 0.25 * 最高峰频数); 0.30 会放过次峰位于
# 0.25-0.30 区间的临界文件 (如 vibro 3261953), 0.25 以下开始漏检
STRONG_PEAK_RATIO = 0.25
# 判据 A: 按压时长中位数上限 (ms)
MEDIAN_LIMIT = 32
# 判据 B: <20ms 按压占比下限
LT20_LIMIT = 0.15
# 判据 D: 单 bin 峰值占总按压数比例下限
DOM1_LIMIT = 0.12


def parse_replay_data(raw: bytes) -> list[tuple[int, int]]:
    """解析服务器存储的 replay 数据 (lzma 压缩的文本帧).

    帧格式: "时间增量ms|按键状态位掩码|固定帧间隔|0", 以逗号分隔.
    """
    text = lzma.decompress(raw).decode("utf-8", errors="replace")
    frames: list[tuple[int, int]] = []
    for frame in text.split(","):
        parts = frame.split("|")
        if len(parts) != 4:
            continue
        try:
            dt = int(parts[0])
            keys = int(parts[1])
        except ValueError:
            continue
        frames.append((dt, keys))
    return frames


def compute_press_durations(
    frames: list[tuple[int, int]],
    keyc: int = 9,
) -> list[float]:
    """按列跟踪按键按下/抬起, 计算每次按压的持续时间 (ms)."""
    held = [False] * keyc
    press_t = [0.0] * keyc
    durations: list[float] = []
    t = 0.0
    for dt, keys in frames:
        if dt <= 0 or keys < 0:
            # 跳过垃圾帧 (-12345 等) 与无效帧
            continue
        t += dt
        for k in range(keyc):
            down = (keys >> k) & 1
            if down and not held[k]:
                held[k] = True
                press_t[k] = t
            elif not down and held[k]:
                held[k] = False
                durations.append(t - press_t[k])
    return durations


def _build_histogram(durations: list[float]) -> list[int]:
    hist = [0] * HIST_RANGE
    for d in durations:
        if d < HIST_RANGE:
            hist[int(d)] += 1
    return hist


def _count_other_strong_peaks(hist: list[int]) -> int:
    """局部极大值(±2ms) -> 过滤 <0.1*max 的频数 -> 统计排除最高峰后
    高于 0.3*max 的峰数量."""
    maxv = max(hist)
    if maxv <= 0:
        return 0

    local_maxima = []
    for i in range(PEAK_WINDOW, HIST_RANGE - PEAK_WINDOW):
        if hist[i] > 0 and all(
            hist[i] >= hist[i - j] for j in range(1, PEAK_WINDOW + 1)
        ) and all(
            hist[i] > hist[i + j] for j in range(1, PEAK_WINDOW + 1)
        ):
            local_maxima.append(i)

    floor = PEAK_FLOOR_RATIO * maxv
    kept = [i for i in local_maxima if hist[i] >= floor]
    if not kept:
        return 0
    max_at = max(kept, key=lambda i: hist[i])
    return sum(
        1 for i in kept if i != max_at and hist[i] > STRONG_PEAK_RATIO * maxv
    )


def analyze_replay_file(path: Path) -> Optional[dict]:
    """读取服务器存储的 replay 文件并检测按压异常 (同步函数, 供线程池调用)."""
    frames = parse_replay_data(path.read_bytes())
    durations = compute_press_durations(frames)
    return detect_pressing_anomaly(durations)


def detect_pressing_anomaly(durations: list[float]) -> Optional[dict]:
    """检测 pressing time 异常.

    任一判据命中即返回详情, 否则返回 None:
    A. 峰值规则: 其余强峰 <=1 且中位数 < 32ms (n>=1500)
    B. 短按占比: <20ms 按压占比 > 15% (n>=500)
    D. 峰值占比: 单 bin 峰值 / 总按压数 > 12% (n>=500)
    """
    d300 = [d for d in durations if d <= MAX_DURATION]
    n = len(d300)
    if n < MIN_PRESSES:
        return None

    hist = _build_histogram(d300)
    maxv = max(hist)
    med = statistics.median(d300)
    lt20 = sum(1 for d in d300 if d < 20) / n
    dom1 = maxv / n
    osc = _count_other_strong_peaks(hist)

    criteria: list[str] = []
    if n >= MIN_PRESSES_PEAK and osc <= 1 and med < MEDIAN_LIMIT:
        criteria.append("A")
    if lt20 > LT20_LIMIT:
        criteria.append("B")
    if dom1 > DOM1_LIMIT:
        criteria.append("D")

    if not criteria:
        return None

    return {
        "criteria": "+".join(criteria),
        "n_presses": n,
        "median_ms": round(med, 1),
        "lt20_ratio": round(lt20, 4),
        "peak_dominance": round(dom1, 4),
        "other_strong_peaks": osc,
    }
