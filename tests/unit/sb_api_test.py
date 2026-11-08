from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import app.state.services
from app.constants.gamemodes import GameMode
from app.sb import sb_api


def test_replay_available_checks_the_replay_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sb_api, "REPLAYS_PATH", tmp_path)

    assert sb_api.replay_available(42) is False

    (tmp_path / "42.osr").write_bytes(b"replay")

    assert sb_api.replay_available(42) is True
    assert sb_api.replay_available(43) is False


def test_vanilla_pair_maps_tracked_modes() -> None:
    assert sb_api.vanilla_pair(GameMode.VANILLA_OSU) == (0, 0)
    assert sb_api.vanilla_pair(GameMode.VANILLA_MANIA) == (3, 0)
    assert sb_api.vanilla_pair(GameMode.RELAX_TAIKO) == (
        1,
        sb_api.RELAX_CLASSIC_MOD,
    )
    assert sb_api.vanilla_pair(GameMode.AUTOPILOT_OSU) == (
        0,
        sb_api.AUTOPILOT_CLASSIC_MOD,
    )


def test_game_mode_from_wire_resolves_tracked_pairs() -> None:
    assert sb_api.game_mode_from_wire(0, 0) is GameMode.VANILLA_OSU
    assert sb_api.game_mode_from_wire(3, 0) is GameMode.VANILLA_MANIA
    assert sb_api.game_mode_from_wire(0, sb_api.RELAX_CLASSIC_MOD) is GameMode.RELAX_OSU
    assert (
        sb_api.game_mode_from_wire(0, sb_api.AUTOPILOT_CLASSIC_MOD)
        is GameMode.AUTOPILOT_OSU
    )


@pytest.mark.parametrize(
    ("mode", "classic_mods"),
    [
        (3, sb_api.RELAX_CLASSIC_MOD),  # rx!mania
        (1, sb_api.AUTOPILOT_CLASSIC_MOD),  # ap!taiko
        (2, sb_api.AUTOPILOT_CLASSIC_MOD),  # ap!catch
        (3, sb_api.AUTOPILOT_CLASSIC_MOD),  # ap!mania
        (
            0,
            sb_api.RELAX_CLASSIC_MOD | sb_api.AUTOPILOT_CLASSIC_MOD,
        ),  # no agreed meaning
    ],
)
def test_game_mode_from_wire_rejects_untracked_pairs(
    mode: int,
    classic_mods: int,
) -> None:
    assert sb_api.game_mode_from_wire(mode, classic_mods) is None


def test_preferred_vanilla_pair_uses_the_stored_mode() -> None:
    assert sb_api.preferred_vanilla_pair(GameMode.VANILLA_TAIKO.value) == (1, 0)
    assert sb_api.preferred_vanilla_pair(GameMode.RELAX_CATCH.value) == (
        2,
        sb_api.RELAX_CLASSIC_MOD,
    )
    assert sb_api.preferred_vanilla_pair(GameMode.AUTOPILOT_OSU.value) == (
        0,
        sb_api.AUTOPILOT_CLASSIC_MOD,
    )


@pytest.mark.parametrize("preferred_mode", [GameMode.RELAX_MANIA.value, -1, 12])
def test_preferred_vanilla_pair_falls_back_to_vanilla_std(
    preferred_mode: int,
) -> None:
    assert sb_api.preferred_vanilla_pair(preferred_mode) == (0, 0)


class FakeDatabase:
    def __init__(self, rank: int | None) -> None:
        self.rank = rank
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def fetch_val(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> int | None:
        self.calls.append((query, params))
        return self.rank


@pytest.mark.asyncio
@pytest.mark.parametrize(("rank", "expected"), [(88, 88), (None, 0)])
async def test_global_rank_uses_the_sql_window_rank(
    rank: int | None,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase(rank)
    monkeypatch.setattr(app.state.services, "database", database)

    assert await sb_api.global_rank(1234, GameMode.RELAX_OSU) == expected

    query, params = database.calls[0]
    assert "RANK() OVER (ORDER BY s.pp DESC)" in query
    assert "(u.priv & 3) = 3" in query
    assert params == {"mode": GameMode.RELAX_OSU.value, "user_id": 1234}


def test_stat_payload_uses_the_wire_names() -> None:
    assert sb_api.stat_payload(
        {
            "pp": 2450,
            "acc": 98.7,
            "plays": 1200,
            "tscore": 123,
            "rscore": 456,
            "total_hits": 999,
            "max_combo": 1234,
        },
        rank=88,
    ) == {
        "pp": 2450,
        "accuracy": 98.7,
        "plays": 1200,
        "rank": 88,
        "totalScore": 123,
        "rankedScore": 456,
        "totalHits": 999,
        "maxCombo": 1234,
        # The source does not store per-mode hit counts; they are zero-filled.
        "count300": 0,
        "count100": 0,
        "count50": 0,
        "countMiss": 0,
    }


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.expire_calls = 0

    async def get(self, key: str) -> bytes | None:
        if key not in self.values:
            return None
        return str(self.values[key]).encode()

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expire_calls += 1
        self.expires[key] = seconds
        return True

    async def delete(self, key: str) -> int:
        self.values.pop(key, None)
        return 1


def build_limiter() -> tuple[sb_api.AuthRateLimiter, FakeRedis]:
    redis = FakeRedis()
    limiter = sb_api.AuthRateLimiter(redis)  # type: ignore[arg-type]
    return limiter, redis


@pytest.mark.asyncio
async def test_rate_limiter_blocks_after_five_account_failures() -> None:
    limiter, redis = build_limiter()

    for _ in range(sb_api.AuthRateLimiter.ACCOUNT_LIMIT):
        assert not await limiter.exceeded("1234", "1.2.3.4")
        await limiter.record_failure("1234", "1.2.3.4")

    assert await limiter.exceeded("1234", "1.2.3.4")
    assert not await limiter.exceeded("5678", "1.2.3.4")
    assert redis.expires == {
        "bancho:sb:auth:account:failures:1234": 15 * 60,
        "bancho:sb:auth:ip:failures:1.2.3.4": 15 * 60,
    }


@pytest.mark.asyncio
async def test_rate_limiter_blocks_after_thirty_ip_failures() -> None:
    limiter, _ = build_limiter()

    for account in range(sb_api.AuthRateLimiter.IP_LIMIT):
        await limiter.record_failure(str(account), "1.2.3.4")

    assert await limiter.exceeded("unknown", "1.2.3.4")
    assert not await limiter.exceeded("unknown", "5.6.7.8")


@pytest.mark.asyncio
async def test_rate_limiter_clear_resets_only_the_account() -> None:
    limiter, _ = build_limiter()

    for _ in range(sb_api.AuthRateLimiter.ACCOUNT_LIMIT):
        await limiter.record_failure("1234", "1.2.3.4")

    await limiter.clear("1234")

    assert not await limiter.exceeded("1234", "1.2.3.4")
    assert await limiter.exceeded("other", "1.2.3.4") is False


def test_score_writeback_model_speaks_vanilla_modes() -> None:
    score: dict[str, Any] = {
        "user_id": 1234,
        "map_hash": "0123456789abcdef0123456789abcdef",
        "checksum": "abc",
        "mods": sb_api.RELAX_CLASSIC_MOD,
        "score": 1234,
        "pp": 12.5,
        "accuracy": 98.2,
        "combo": 100,
        "n300": 1,
        "n100": 2,
        "n50": 3,
        "miss": 4,
        "geki": 0,
        "katu": 0,
        "grade": "S",
        "perfect": False,
        "passed": True,
        "played_at": "2026-11-08T00:00:00+00:00",
        "time_elapsed": 1000,
        "client_flags": 0,
        "replay": None,
    }

    payload = sb_api.ScoreWriteback(mode=0, **score)
    assert payload.mode == 0
    assert sb_api.game_mode_from_wire(payload.mode, payload.mods) is GameMode.RELAX_OSU

    with pytest.raises(ValidationError):
        sb_api.ScoreWriteback(mode=4, **score)
