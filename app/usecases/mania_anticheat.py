from __future__ import annotations

import lzma
import statistics
from pathlib import Path
from typing import Optional

# 按压时长截断上限 (ms), 超过视为长按误触/暂停等, 不参与统计
MAX_DURATION = 300
# 时长直方图范围 (ms), 与 MAX_DURATION 保持一致, 保证
# 峰值占比 (dom1) 的分子分母来自同一数据集
HIST_RANGE = MAX_DURATION
# 最高支持键数 (mania 最高 18K, 位掩码需 18 位)
MAX_KEYCOUNT = 18
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
# 10K+ 谱面按压分摊到更多列, 分布天然更集中, D 阈值放宽
# (16K 正常玩家实测最高 12.6%, 与 4K 作弊的 12.2-12.4% 无法同阈值区分)
HIGH_KEYC_THRESHOLD = 10
DOM1_LIMIT_HIGH_KEYC = 0.14


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
    keyc: int = 4,
) -> list[float]:
    """按列跟踪按键按下/抬起, 计算每次按压的持续时间 (ms).

    只统计低 `keyc` 位 (按下 = 位 0->1, 抬起 = 位 1->0):
    - 4K 及以下: keys 低 4 位为按下状态, 高 4 位为抬起位 (stable 遗留
      编码), 抬起位直接忽略, 状态位变化即可表达抬起
    - 5K-16K (lazer): keys 为纯状态位掩码 (bit i = 第 i+1 键按下)
    keyc 由谱面键数 (beatmap.cs) 或 infer_keycount 提供, 避免把 4K 的
    抬起位误读为额外列 (此前固定 9 列会污染 B/D 判据特征).
    """
    held = [False] * keyc
    press_t = [0.0] * keyc
    durations: list[float] = []
    t = 0.0
    for dt, keys in frames:
        if dt <= 0 or keys < 0:
            # 跳过垃圾帧 (-12345 RNG seed 帧等) 与无效帧
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


def infer_keycount(frames: list[tuple[int, int]]) -> int:
    """从回放数据推断按键列数 (无谱面信息时的兜底).

    4K 及以下的 stable 编码: 高 4 位 (抬起位) 是低 4 位 (按下位) 的
    子集 (同一帧只能抬起当前按下的键). 若观察到违反该约束的值, 说明是
    纯状态位编码 (5K-16K), 键数 = 最高置位位数.
    """
    max_keys = 0
    is_state_mask = False
    for dt, keys in frames:
        if dt <= 0 or keys < 0:
            continue
        max_keys = max(max_keys, keys)
        # 高 4 位存在低 4 位没有的位 -> 纯状态位编码
        if (keys >> 4) & ~(keys & 0x0F):
            is_state_mask = True

    if max_keys == 0:
        return 4
    if is_state_mask:
        return min(max_keys.bit_length(), MAX_KEYCOUNT)
    return 4


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


def analyze_replay_file(path: Path, keyc: Optional[int] = None) -> Optional[dict]:
    """读取服务器存储的 replay 文件并检测按压异常 (同步函数, 供线程池调用).

    keyc 为谱面键数 (mania 的 beatmap.cs); 为 None 时从回放数据推断.
    """
    frames = parse_replay_data(path.read_bytes())
    if keyc is None:
        keyc = infer_keycount(frames)
    durations = compute_press_durations(frames, keyc)
    return detect_pressing_anomaly(durations, keyc)


def detect_pressing_anomaly(
    durations: list[float],
    keyc: int = 4,
) -> Optional[dict]:
    """检测 pressing time 异常.

    任一判据命中即返回详情, 否则返回 None:
    A. 峰值规则: 其余强峰 <=1 且中位数 < 32ms (n>=1500)
    B. 短按占比: <20ms 按压占比 > 15% (n>=500)
    D. 峰值占比: 单 bin 峰值 / 总按压数 > 12% (n>=500); 10K+ 谱面
       阈值放宽至 14%
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

    dom1_limit = (
        DOM1_LIMIT_HIGH_KEYC if keyc >= HIGH_KEYC_THRESHOLD else DOM1_LIMIT
    )

    criteria: list[str] = []
    if n >= MIN_PRESSES_PEAK and osc <= 1 and med < MEDIAN_LIMIT:
        criteria.append("A")
    if lt20 > LT20_LIMIT:
        criteria.append("B")
    if dom1 > dom1_limit:
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
