from __future__ import annotations

import asyncio
from base64 import b64decode
from binascii import Error as Base64Error
from collections.abc import Mapping
from datetime import datetime
from datetime import timezone
from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from pathlib import Path
from typing import Any
from typing import Literal

import bcrypt
from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request
from fastapi import status
from fastapi.param_functions import Query
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials as HTTPCredentials
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from pydantic import Field
from redis import asyncio as aioredis
from sqlalchemy import select
from sqlalchemy import update

import app
from app import settings
from app.api.v2.common import responses
from app.api.v2.common.json import ORJSONResponse
from app.api.v2.common.responses import Failure
from app.api.v2.common.responses import Success
from app.constants.gamemodes import GameMode
from app.constants.mods import Mods
from app.constants.privileges import Privileges
from app.logging import Ansi
from app.logging import log
from app.objects.beatmap import Beatmap
from app.objects.beatmap import RankedStatus
from app.objects.beatmap import disk_has_expected_osu_file
from app.objects.beatmap import ensure_osu_file_is_available
from app.repositories import mail as mail_repo
from app.repositories import stats as stats_repo
from app.repositories import users as users_repo
from app.repositories.mail import MailTable
from app.repositories.maps import MapsTable
from app.repositories.scores import READ_PARAMS
from app.repositories.scores import ScoresTable
from app.repositories.users import UsersTable
from app.usecases.performance import ScoreParams

router = APIRouter(tags=["API ppy.sb"], prefix="/sb")
http_bearer_scheme = HTTPBearer(auto_error=False)
REPLAYS_PATH = Path.cwd() / ".data/osr"


def replay_available(score_id: int) -> bool:
    """Whether this server has a stored replay file for the score."""
    return (REPLAYS_PATH / f"{score_id}.osr").is_file()


RELAX_CLASSIC_MOD = 1 << 7
AUTOPILOT_CLASSIC_MOD = 1 << 13


def vanilla_pair(game_mode: GameMode) -> tuple[int, int]:
    """Map a source game mode to the stable client's (mode, classic mods) pair."""
    if game_mode >= GameMode.AUTOPILOT_OSU:
        return game_mode.as_vanilla, AUTOPILOT_CLASSIC_MOD
    if game_mode >= GameMode.RELAX_OSU:
        return game_mode.as_vanilla, RELAX_CLASSIC_MOD
    return game_mode.as_vanilla, 0


def has_conflicting_mode_mods(classic_mods: int) -> bool:
    """Whether a pair sets both Relax and Autopilot."""
    return bool(
        classic_mods & RELAX_CLASSIC_MOD and classic_mods & AUTOPILOT_CLASSIC_MOD
    )


def game_mode_from_wire(mode: int, classic_mods: int) -> GameMode | None:
    """Resolve a stable (mode, classic mods) pair, or None when unsupported."""
    if has_conflicting_mode_mods(classic_mods):
        return None

    game_mode = GameMode.from_params(mode, Mods(classic_mods))
    return game_mode if game_mode in GameMode.valid_gamemodes() else None


def preferred_vanilla_pair(preferred_mode: int) -> tuple[int, int]:
    """Map the stored preferred mode to a pair, falling back to vanilla std."""
    try:
        game_mode = GameMode(preferred_mode)
    except ValueError:
        return (0, 0)
    if game_mode not in GameMode.valid_gamemodes():
        return (0, 0)
    return vanilla_pair(game_mode)


ZERO_STAT_ROW: Mapping[str, Any] = {
    "pp": 0,
    "acc": 0.0,
    "plays": 0,
    "tscore": 0,
    "rscore": 0,
    "total_hits": 0,
    "max_combo": 0,
}


def stat_payload(
    stat: Mapping[str, Any],
    rank: int,
) -> dict[str, Any]:
    """Serialize one stat row in the stable client's wire vocabulary.

    The source does not store per-mode hit counts, and aggregating the score
    table on every login or mode switch is too expensive, so they are zero.
    """
    return {
        "pp": stat["pp"],
        "accuracy": stat["acc"],
        "plays": stat["plays"],
        "rank": rank,
        "totalScore": stat["tscore"],
        "rankedScore": stat["rscore"],
        "totalHits": stat["total_hits"],
        "maxCombo": stat["max_combo"],
        "count300": 0,
        "count100": 0,
        "count50": 0,
        "countMiss": 0,
    }


async def global_rank(user_id: int, game_mode: GameMode) -> int:
    """The source's pp rank for a mode, using the same query as the web backend.

    RANK() OVER (ORDER BY pp DESC) over the mode's stats, joined to accounts
    that are registered, verified and not restricted; ties share a rank. An
    account outside that set (or without a stats row) has rank 0.

    Redis leaderboards are deliberately not read: only the running server
    maintains them, so a direct database change (recalc, manual fix, import)
    leaves them stale and the reported rank wrong.
    """
    rank = await app.state.services.database.fetch_val(
        "SELECT ranked.rnk FROM ("
        "SELECT s.id, RANK() OVER (ORDER BY s.pp DESC) AS rnk "
        "FROM stats s "
        "INNER JOIN users u ON u.id = s.id "
        "WHERE s.mode = :mode AND (u.priv & 3) = 3"
        ") ranked WHERE ranked.id = :user_id",
        {"mode": game_mode.value, "user_id": user_id},
    )
    return int(rank) if rank is not None else 0


class AuthRateLimiter:
    """Fixed-window failure counters for /sb/auth, per account and per IP."""

    WINDOW_SECONDS = 15 * 60
    ACCOUNT_LIMIT = 5
    IP_LIMIT = 30

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    @staticmethod
    def _key(kind: str, value: str) -> str:
        return f"bancho:sb:auth:{kind}:failures:{value}"

    async def exceeded(self, account: str, ip: str) -> bool:
        account_failures = await self._redis.get(self._key("account", account))
        ip_failures = await self._redis.get(self._key("ip", ip))
        return (
            _failure_count(account_failures) >= self.ACCOUNT_LIMIT
            or _failure_count(ip_failures) >= self.IP_LIMIT
        )

    async def record_failure(self, account: str, ip: str) -> None:
        for kind, value in (("account", account), ("ip", ip)):
            key = self._key(kind, value)
            if await self._redis.incr(key) == 1:
                await self._redis.expire(key, self.WINDOW_SECONDS)

    async def clear(self, account: str) -> None:
        await self._redis.delete(self._key("account", account))


def _failure_count(value: bytes | str | None) -> int:
    return int(value) if value is not None else 0


def trusted_secret(token: HTTPCredentials | None) -> str | None:
    """The configured bridge secret when the request presents it, else None."""
    if token is None or settings.TRUSTED_SECRET is None:
        return None
    if not compare_digest(token.credentials, settings.TRUSTED_SECRET):
        return None
    return settings.TRUSTED_SECRET


def invalid_trusted_secret() -> Response:
    return ORJSONResponse(
        {"status": "Invalid trusted secret."},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def client_ip(request: Request) -> str:
    try:
        return str(app.state.services.ip_resolver.get_ip(request.headers))
    except Exception:
        return request.client.host if request.client is not None else "unknown"


def audit_auth(account: str, ip: str, result: str) -> None:
    line = f"sb auth: account={account} ip={ip} result={result}"
    if result == "success":
        log(line)
    elif result == "rate-limited":
        log(line, Ansi.LRED)
    else:
        log(line, Ansi.LYELLOW)


@router.get("/user-scores")
async def user_best_scores(
    *,
    email: str = Query(..., min_length=3, max_length=254),
    mode: int = Query(..., ge=0, le=11),
    rank: Literal["ranked", "all"] = Query("ranked"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    token: HTTPCredentials | None = Depends(http_bearer_scheme),
) -> Response:
    """Return up to 200 best scores for the unrestricted user with this email."""
    if trusted_secret(token) is None:
        return invalid_trusted_secret()

    query = (
        select(*READ_PARAMS, UsersTable.name.label("user_name"))
        .select_from(ScoresTable)
        .join(UsersTable, UsersTable.id == ScoresTable.userid)
        .join(MapsTable, MapsTable.md5 == ScoresTable.map_md5)
        .where(
            UsersTable.email == email.strip(),
            UsersTable.priv.op("&")(Privileges.UNRESTRICTED.value) != 0,
            ScoresTable.mode == mode,
            ScoresTable.status == 2,
        )
    )
    if rank == "ranked":
        query = query.where(
            MapsTable.status.in_(
                (RankedStatus.Ranked, RankedStatus.Approved, RankedStatus.Qualified),
            ),
        )
    query = query.order_by(
        ScoresTable.pp.desc(),
        ScoresTable.score.desc(),
        ScoresTable.id.desc(),
    )
    query = query.limit(limit).offset(offset)
    rows = await app.state.services.database.fetch_all(query)

    def serialize(row: dict[str, Any]) -> dict[str, Any]:
        played_at = row["play_time"]
        if isinstance(played_at, datetime):
            played_at = played_at.replace(tzinfo=timezone.utc).isoformat()
        return {
            "id": str(row["id"]),
            "status": row["status"],
            "userId": row["userid"],
            "userName": row["user_name"],
            "mapHash": row["map_md5"].strip(),
            "mode": row["mode"],
            "mods": row["mods"],
            "score": row["score"],
            "pp": row["pp"],
            "accuracy": row["acc"],
            "combo": row["max_combo"],
            "n300": row["n300"],
            "n100": row["n100"],
            "n50": row["n50"],
            "miss": row["nmiss"],
            "geki": row["ngeki"],
            "katu": row["nkatu"],
            "grade": row["grade"],
            "perfect": bool(row["perfect"]),
            "playedAt": played_at,
            "checksum": row["online_checksum"],
            "hasReplay": replay_available(row["id"]),
        }

    return ORJSONResponse({"scores": [serialize(row) for row in rows]})


@router.get("/map-scores")
async def map_best_scores(
    *,
    mapHash: str = Query(..., min_length=32, max_length=32),
    mode: int = Query(..., ge=0, le=11),
    country: str | None = Query(None, min_length=2, max_length=2),
    mods: int | None = Query(None, ge=0),
    rank: Literal["score", "pp"] = Query("score"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    token: HTTPCredentials | None = Depends(http_bearer_scheme),
) -> Response:
    secret = trusted_secret(token)
    if secret is None:
        return invalid_trusted_secret()
    if any(c not in "0123456789abcdefABCDEF" for c in mapHash):
        return ORJSONResponse({"status": "Invalid map hash."}, status_code=422)

    query = (
        select(
            *READ_PARAMS,
            UsersTable.name.label("user_name"),
            UsersTable.email.label("binding_email"),
        )
        .select_from(ScoresTable)
        .join(UsersTable, UsersTable.id == ScoresTable.userid)
        .where(
            ScoresTable.map_md5 == mapHash,
            ScoresTable.mode == mode,
            ScoresTable.status == 2,
            UsersTable.priv.op("&")(Privileges.UNRESTRICTED.value) != 0,
        )
    )
    if country is not None:
        query = query.where(UsersTable.country == country)
    if mods is not None:
        query = query.where(ScoresTable.mods == mods)
    metric = ScoresTable.pp if rank == "pp" else ScoresTable.score
    query = (
        query.order_by(metric.desc(), ScoresTable.score.desc(), ScoresTable.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = await app.state.services.database.fetch_all(query)
    scores = []
    for row in rows:
        played_at = row["play_time"]
        if isinstance(played_at, datetime):
            played_at = played_at.replace(tzinfo=timezone.utc).isoformat()
        scores.append(
            {
                "id": str(row["id"]),
                "status": row["status"],
                "userId": row["userid"],
                "userName": row["user_name"],
                "mapHash": row["map_md5"].strip(),
                "mode": row["mode"],
                "mods": row["mods"],
                "score": row["score"],
                "pp": row["pp"],
                "accuracy": row["acc"],
                "combo": row["max_combo"],
                "n300": row["n300"],
                "n100": row["n100"],
                "n50": row["n50"],
                "miss": row["nmiss"],
                "geki": row["ngeki"],
                "katu": row["nkatu"],
                "grade": row["grade"],
                "perfect": bool(row["perfect"]),
                "playedAt": played_at,
                "checksum": row["online_checksum"],
                "hasReplay": replay_available(row["id"]),
                "bindingKey": hmac_new(
                    secret.encode(),
                    row["binding_email"].strip().lower().encode(),
                    sha256,
                ).hexdigest(),
            }
        )
    return ORJSONResponse({"scores": scores})


class SbAuthRequest(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password_md5: str = Field(pattern=r"^[a-fA-F0-9]{32}$")


@router.post("/auth")
async def sb_auth(
    payload: SbAuthRequest,
    request: Request,
    token: HTTPCredentials | None = Depends(http_bearer_scheme),
) -> Response:
    """Verify a remote account's credentials; identity only."""
    if trusted_secret(token) is None:
        return invalid_trusted_secret()

    login = payload.login.strip()
    user = await users_repo.fetch_one(name=login, fetch_all_fields=True)
    if user is None and login.isdigit():
        user = await users_repo.fetch_one(id=int(login), fetch_all_fields=True)

    account = str(user["id"]) if user is not None else f"login={login!r}"
    ip = client_ip(request)
    limiter = AuthRateLimiter(app.state.services.redis)
    if await limiter.exceeded(account, ip):
        audit_auth(account, ip, "rate-limited")
        return ORJSONResponse(
            {"status": "Too many attempts."},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    password_md5 = payload.password_md5.encode()
    verified = False
    if user is not None:
        trusted_hash = user["pw_bcrypt"].encode()
        if (cached := app.state.cache.bcrypt.get(trusted_hash)) is not None:
            verified = compare_digest(cached, password_md5)
        elif bcrypt.checkpw(password_md5, trusted_hash):
            verified = True
            app.state.cache.bcrypt[trusted_hash] = password_md5

    if user is None or not verified:
        await limiter.record_failure(account, ip)
        audit_auth(account, ip, "invalid-credentials")
        return ORJSONResponse(
            {"status": "Invalid credentials."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if not user["priv"] & Privileges.UNRESTRICTED.value:
        audit_auth(account, ip, "restricted")
        return ORJSONResponse(
            {"status": "Account is restricted."},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await limiter.clear(account)
    audit_auth(account, ip, "success")
    return ORJSONResponse(
        {
            "id": user["id"],
            "name": user["name"],
            "safeName": user["safe_name"],
            "country": user["country"],
        },
    )


@router.get("/user-stats")
async def user_stats(
    *,
    id: int = Query(..., ge=1),
    token: HTTPCredentials | None = Depends(http_bearer_scheme),
) -> Response:
    """Every mode the source tracks for an account, for login prepopulation."""
    if trusted_secret(token) is None:
        return invalid_trusted_secret()

    user = await users_repo.fetch_one(id=id)
    if user is None:
        return ORJSONResponse(
            {"status": "Player not found."},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if not user["priv"] & Privileges.UNRESTRICTED.value:
        return ORJSONResponse(
            {"status": "Account is restricted."},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    rows = await stats_repo.fetch_many(player_id=id)
    tracked: list[tuple[GameMode, stats_repo.Stat]] = []
    for row in rows:
        try:
            game_mode = GameMode(row["mode"])
        except ValueError:
            continue
        if game_mode in GameMode.valid_gamemodes():
            tracked.append((game_mode, row))
    if not tracked:
        return ORJSONResponse({"stats": []})

    ranks = await asyncio.gather(
        *(global_rank(id, game_mode) for game_mode, _ in tracked),
    )
    preferred = preferred_vanilla_pair(user["preferred_mode"])

    entries: list[dict[str, Any]] = []
    for (game_mode, row), rank in zip(tracked, ranks):
        mode, mods = vanilla_pair(game_mode)
        entries.append(
            {
                "mode": mode,
                "mods": mods,
                "preferred": (mode, mods) == preferred,
                **stat_payload(row, rank),
            },
        )

    if not any(entry["preferred"] for entry in entries):
        # The preferred pair has no stored row; report it as a zeroed entry so
        # the contract still names exactly one preferred pair.
        mode, mods = preferred
        entries.append(
            {
                "mode": mode,
                "mods": mods,
                "preferred": True,
                **stat_payload(ZERO_STAT_ROW, 0),
            },
        )

    entries.sort(key=lambda entry: (entry["mode"], entry["mods"]))
    return ORJSONResponse({"stats": entries})


@router.get("/user-stat")
async def user_stat(
    *,
    id: int = Query(..., ge=1),
    mode: int = Query(..., ge=0, le=3),
    classic_mods: int = Query(..., alias="classic-mods", ge=0),
    token: HTTPCredentials | None = Depends(http_bearer_scheme),
) -> Response:
    """One mode pair for an account, used for mode-switch refreshes."""
    if trusted_secret(token) is None:
        return invalid_trusted_secret()
    if has_conflicting_mode_mods(classic_mods):
        return ORJSONResponse(
            {"status": "Relax and Autopilot cannot be combined."},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    game_mode = game_mode_from_wire(mode, classic_mods)
    if game_mode is None:
        return ORJSONResponse(
            {"status": "Mode is not tracked by this source."},
            status_code=status.HTTP_404_NOT_FOUND,
        )

    user = await users_repo.fetch_one(id=id)
    if user is None:
        return ORJSONResponse(
            {"status": "Player not found."},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if not user["priv"] & Privileges.UNRESTRICTED.value:
        return ORJSONResponse(
            {"status": "Account is restricted."},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    stat = await stats_repo.fetch_one(id, game_mode.value)
    if stat is None:
        return ORJSONResponse(
            {"status": "No stats for this mode."},
            status_code=status.HTTP_404_NOT_FOUND,
        )

    rank = await global_rank(id, game_mode)
    return ORJSONResponse({"stat": stat_payload(stat, rank)})


class ScoreWriteback(BaseModel):
    # Exactly one target is required; see score_writeback().
    user_id: int | None = Field(None, ge=1)
    email: str | None = Field(None, min_length=3, max_length=254)
    map_hash: str = Field(pattern=r"^[a-fA-F0-9]{32}$")
    checksum: str = Field(min_length=1, max_length=128)
    mode: int = Field(ge=0, le=3)
    mods: int = Field(ge=0)
    score: int = Field(ge=0)
    pp: float = Field(ge=0)
    accuracy: float = Field(ge=0, le=100)
    combo: int = Field(ge=0)
    n300: int = Field(ge=0)
    n100: int = Field(ge=0)
    n50: int = Field(ge=0)
    miss: int = Field(ge=0)
    geki: int = Field(ge=0)
    katu: int = Field(ge=0)
    grade: Literal["A", "B", "C", "D", "S", "SH", "X", "XH", "F"]
    perfect: bool
    passed: bool
    played_at: datetime
    time_elapsed: int = Field(ge=0)
    client_flags: int = Field(ge=0)
    replay: str | None = None


@router.post("/score-writeback")
async def score_writeback(
    payload: ScoreWriteback,
    token: HTTPCredentials | None = Depends(http_bearer_scheme),
) -> Response:
    if trusted_secret(token) is None:
        return invalid_trusted_secret()
    if (payload.user_id is None) == (payload.email is None):
        return ORJSONResponse(
            {"status": "Exactly one of user_id or email is required."},
            status_code=422,
        )
    if payload.played_at.tzinfo is None:
        return ORJSONResponse(
            {"status": "Invalid score mode or time."}, status_code=422
        )
    if has_conflicting_mode_mods(payload.mods):
        return ORJSONResponse(
            {"status": "Relax and Autopilot cannot be combined."},
            status_code=422,
        )

    game_mode = game_mode_from_wire(payload.mode, payload.mods)
    if game_mode is None:
        return ORJSONResponse({"status": "Unsupported score mode."}, status_code=422)
    if payload.replay is not None and len(payload.replay) > 5_500_000:
        return ORJSONResponse({"status": "Replay too large."}, status_code=413)
    try:
        replay = b64decode(payload.replay, validate=True) if payload.replay else None
    except Base64Error:
        return ORJSONResponse({"status": "Invalid replay."}, status_code=422)
    if payload.passed and (replay is None or not 24 <= len(replay) <= 4_000_000):
        return ORJSONResponse(
            {"status": "Replay required for passed scores."}, status_code=422
        )

    target_email = payload.email.strip() if payload.email is not None else None
    if payload.user_id is not None:
        user = await users_repo.fetch_one(id=payload.user_id)
    elif target_email is not None:
        user = await users_repo.fetch_one(email=target_email)
    else:
        user = None
    if user is None:
        return ORJSONResponse(
            {"status": "No eligible account for target."}, status_code=404
        )
    if not user["priv"] & Privileges.UNRESTRICTED.value:
        return ORJSONResponse({"status": "Account is restricted."}, status_code=403)
    bmap = await Beatmap.from_md5(payload.map_hash)
    if bmap is None:
        return ORJSONResponse({"status": "Beatmap unavailable."}, status_code=422)
    source_pp = 0.0
    if payload.passed:
        if not await ensure_osu_file_is_available(
            bmap.id, expected_md5=payload.map_hash
        ) or not disk_has_expected_osu_file(bmap.id, payload.map_hash):
            return ORJSONResponse(
                {"status": "Beatmap file unavailable."}, status_code=422
            )
        performances = await app.usecases.performance.calculate_performances(
            osu_file_path=str(Path.cwd() / ".data/osu" / f"{bmap.id}.osu"),
            scores=[
                ScoreParams(
                    mode=game_mode.as_vanilla,
                    mods=payload.mods,
                    combo=payload.combo,
                    ngeki=payload.geki,
                    n300=payload.n300,
                    nkatu=payload.katu,
                    n100=payload.n100,
                    n50=payload.n50,
                    nmiss=payload.miss,
                    legacy_total_score=payload.score,
                )
            ],
        )
        source_pp = performances[0]["performance"]["pp"]

    db = app.state.services.database
    async with app.state.score_submission_locks[payload.checksum]:
        async with db.transaction():
            duplicate = await db.fetch_one(
                select(ScoresTable.id, ScoresTable.userid).where(
                    ScoresTable.online_checksum == payload.checksum,
                )
            )
            if duplicate is not None:
                if duplicate["userid"] != user["id"]:
                    return ORJSONResponse(
                        {"status": "Checksum belongs to another account."},
                        status_code=409,
                    )
                score_id = duplicate["id"]
                inserted = False
            else:
                metric = ScoresTable.pp if game_mode.value >= 4 else ScoresTable.score
                previous = await db.fetch_one(
                    select(ScoresTable.id, metric.label("metric"))
                    .where(
                        ScoresTable.userid == user["id"],
                        ScoresTable.map_md5 == payload.map_hash,
                        ScoresTable.mode == game_mode.value,
                        ScoresTable.status == 2,
                    )
                    .order_by(metric.desc(), ScoresTable.id.desc())
                    .limit(1)
                )
                value = source_pp if game_mode.value >= 4 else payload.score
                score_status = (
                    0
                    if not payload.passed
                    else 2 if previous is None or value > previous["metric"] else 1
                )
                if score_status == 2:
                    await db.execute(
                        update(ScoresTable)
                        .where(
                            ScoresTable.userid == user["id"],
                            ScoresTable.map_md5 == payload.map_hash,
                            ScoresTable.mode == game_mode.value,
                            ScoresTable.status == 2,
                        )
                        .values(status=1)
                    )
                score_id = await db.execute(
                    ScoresTable.__table__.insert().values(
                        map_md5=payload.map_hash,
                        score=payload.score,
                        pp=source_pp,
                        acc=payload.accuracy,
                        max_combo=payload.combo,
                        mods=payload.mods,
                        n300=payload.n300,
                        n100=payload.n100,
                        n50=payload.n50,
                        nmiss=payload.miss,
                        ngeki=payload.geki,
                        nkatu=payload.katu,
                        grade=payload.grade,
                        status=score_status,
                        mode=game_mode.value,
                        play_time=payload.played_at.astimezone(timezone.utc).replace(
                            tzinfo=None
                        ),
                        time_elapsed=payload.time_elapsed,
                        client_flags=payload.client_flags,
                        userid=user["id"],
                        perfect=payload.perfect,
                        online_checksum=payload.checksum,
                    )
                )
                await db.execute(
                    update(MapsTable)
                    .where(MapsTable.md5 == payload.map_hash)
                    .values(
                        plays=MapsTable.plays + 1,
                        passes=MapsTable.passes + int(payload.passed),
                    )
                )
                inserted = True
        if inserted:
            bmap.plays += 1
            bmap.passes += int(payload.passed)
        if replay is not None:
            replay_path = Path.cwd() / ".data/osr" / f"{score_id}.osr"
            replay_path.parent.mkdir(parents=True, exist_ok=True)
            if not replay_path.exists():
                replay_path.write_bytes(replay)
        if player := app.state.sessions.players.get(id=user["id"]):
            await player.recalc_stats_sql(game_mode)
            await player.update_rank(game_mode)
            player.enqueue(app.packets.user_stats(player))
        else:
            await stats_repo.sql_recalculate_mode(user["id"], game_mode.value)
            recalculated = await stats_repo.fetch_one(user["id"], game_mode.value)
            if recalculated is not None:
                await app.state.services.redis.zadd(
                    f"bancho:leaderboard:{game_mode.value}",
                    {str(user["id"]): recalculated["pp"]},
                )
                await app.state.services.redis.zadd(
                    f"bancho:leaderboard:{game_mode.value}:{user['country']}",
                    {str(user["id"]): recalculated["pp"]},
                )

    stat = await stats_repo.fetch_one(user["id"], game_mode.value)
    rank = await global_rank(user["id"], game_mode)
    return ORJSONResponse(
        {
            "status": "inserted" if inserted else "duplicate",
            "scoreId": score_id,
            "stat": stat_payload(
                stat if stat is not None else ZERO_STAT_ROW,
                rank,
            ),
        },
    )


@router.get("/pd/injector")
async def pd_injector_meta_options() -> Success[str]:
    """percyDan injector check allowance"""
    return responses.success("accept")


@router.post("/players/{player_id}/notify")
async def notify_player(
    player_id: int,
) -> Success | Failure:
    """Notify a player that they might have new messages."""
    if target := app.state.sessions.players.get(id=player_id):
        mail_rows = await mail_repo.fetch_all_mail_to_user(player_id, read=False)
        for mail in mail_rows:
            target.enqueue(
                app.packets.send_message(
                    sender=mail["from_name"],
                    msg=mail["msg"],
                    recipient=mail["to_name"],
                    sender_id=mail["from_id"],
                )
            )
            # we consider the mail as read when we notify the online player, in case of duplicate notifications
            await app.state.services.database.execute(
                update(MailTable).where(MailTable.id == mail["id"]).values(read=True)
            )
        return responses.success({}, meta={"online": True, "enqueued": len(mail_rows)})
    return responses.success({}, meta={"online": False})


@router.delete("/cache")
async def flush_caches(
    request: Request,
    type: Literal["bcrypt", "beatmap", "beatmapset", "unsubmitted", "needs_update"],
) -> Success | Failure:
    """Flush the specified cache."""
    if type == "beatmap":
        affected_maps: dict[int, Beatmap] = {}
        if hash := request.query_params.get("hash"):
            # A beatmap is indexed by both md5 and numeric id. Search values
            # as well as the md5 key because the md5 can have changed during
            # an in-place API refresh while the old alias remains present.
            for key, bmap in app.state.cache.beatmap.items():
                if key == hash or bmap.md5 == hash:
                    affected_maps[bmap.id] = bmap
        elif bid := request.query_params.get("bid"):
            for bmap in app.state.cache.beatmap.values():
                if bmap.id == int(bid):
                    affected_maps[bmap.id] = bmap
        else:
            return responses.failure(message="Either hash or bid must be provided.")

        for bmap in affected_maps.values():
            # Remove both the md5 and numeric-id aliases, plus any stale md5
            # aliases which may point to the same beatmap object.
            for key, cached in list(app.state.cache.beatmap.items()):
                if cached.id == bmap.id:
                    app.state.cache.beatmap.pop(key, None)

        affected_sets = {m.set_id for m in affected_maps.values()}
        for set_id in affected_sets:
            app.state.cache.beatmapset.pop(set_id, None)
        return responses.success({}, meta={"affected": len(affected_maps)})
    return responses.failure(message="Cache flushing is not implemented for this type.")
