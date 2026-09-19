from __future__ import annotations

from datetime import datetime

import app.state.cache
from app.objects.beatmap import BEATMAP_CACHE_TTL
from app.objects.beatmap import Beatmap
from app.objects.beatmap import BeatmapSet
from app.objects.beatmap import cache_beatmap_set
from app.objects.beatmap import cleanup_expired_beatmap_cache


def test_expired_beatmap_cache_removes_set_and_all_aliases() -> None:
    old_beatmap_cache = app.state.cache.beatmap.copy()
    old_beatmapset_cache = app.state.cache.beatmapset.copy()
    old_cached_at = app.state.cache.beatmapset_cached_at.copy()

    try:
        app.state.cache.beatmap.clear()
        app.state.cache.beatmapset.clear()
        app.state.cache.beatmapset_cached_at.clear()

        beatmap_set = BeatmapSet(
            id=123,
            last_osuapi_check=datetime.now(),
        )
        beatmap = Beatmap(
            map_set=beatmap_set,
            id=456,
            set_id=beatmap_set.id,
            md5="current-md5",
        )
        beatmap_set.maps.append(beatmap)

        cache_beatmap_set(beatmap_set)
        app.state.cache.beatmap["old-md5"] = beatmap
        app.state.cache.beatmapset_cached_at[beatmap_set.id] = 0

        removed = cleanup_expired_beatmap_cache(
            now=BEATMAP_CACHE_TTL.total_seconds() + 1,
        )

        assert removed == 1
        assert beatmap_set.id not in app.state.cache.beatmapset
        assert beatmap_set.id not in app.state.cache.beatmapset_cached_at
        assert "current-md5" not in app.state.cache.beatmap
        assert "old-md5" not in app.state.cache.beatmap
        assert beatmap.id not in app.state.cache.beatmap
    finally:
        app.state.cache.beatmap.clear()
        app.state.cache.beatmap.update(old_beatmap_cache)
        app.state.cache.beatmapset.clear()
        app.state.cache.beatmapset.update(old_beatmapset_cache)
        app.state.cache.beatmapset_cached_at.clear()
        app.state.cache.beatmapset_cached_at.update(old_cached_at)
