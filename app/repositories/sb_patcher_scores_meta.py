from __future__ import annotations


from datetime import datetime
import json
from typing import Any
from typing import cast

from sqlalchemy import Column
from sqlalchemy import Boolean
from sqlalchemy import VARCHAR
from sqlalchemy import Index
from sqlalchemy import ForeignKey
from sqlalchemy import JSON
from sqlalchemy import func
from sqlalchemy import insert
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.dialects.mysql import BIGINT

from app.objects.beatmap import Beatmap
from app.objects.sb.patcher_score_meta import SealedSbPatcherScoreMeta, SbPatcherScoreMetaRaw
from app.objects.score import Score
import app.state.services
from app._typing import UNSET
from app._typing import _UnsetSentinel
from app.repositories import Base


class SbPatcherScoreMetaTable(Base):
    __tablename__ = "sb_patcher_scores_meta"

    id = Column(
        "id",
        BIGINT(unsigned=True),
        ForeignKey("scores.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        primary_key=True,
    )
    no_pause = Column("no_pause", Boolean, nullable=False)
    strict_no_pause = Column("strict_no_pause", Boolean, nullable=False)
    hash = Column("hash", VARCHAR(64))
    v = Column("v", VARCHAR(16))
    raw = Column("raw", JSON, nullable=False, default={})


READ_PARAMS = (
    SbPatcherScoreMetaTable.id,
    SbPatcherScoreMetaTable.no_pause,
    SbPatcherScoreMetaTable.strict_no_pause,
    SbPatcherScoreMetaTable.hash,
    SbPatcherScoreMetaTable.v,
    # ScoresTable.raw,
)


async def create(
    id: int,
    no_pause: bool,
    strict_no_pause: bool,
    hash: str,
    v: str,
    raw: SbPatcherScoreMetaRaw,
) -> SealedSbPatcherScoreMeta:
    rec_id = await insert_returning_id(
        id=id, no_pause=no_pause, strict_no_pause=strict_no_pause, hash=hash, v=v, raw=raw
    )
    select_stmt = select(*READ_PARAMS).where(SbPatcherScoreMetaTable.id == rec_id)
    _meta = await app.state.services.database.fetch_one(select_stmt)
    assert _meta is not None
    return SealedSbPatcherScoreMeta(
        id=_meta["id"],
        no_pause=_meta["no_pause"],
        hash=_meta["hash"],
        v=_meta["v"],
        strict_no_pause=_meta["strict_no_pause"],
        raw=_meta["raw"],
    )


async def insert_returning_id(
    *,
    id: int,
    no_pause: bool,
    strict_no_pause: bool,
    hash: str,
    v: str,
    raw: SbPatcherScoreMetaRaw,
) -> int:
    insert_stmt = insert(SbPatcherScoreMetaTable).values(
        id=id,
        no_pause=no_pause,
        strict_no_pause=strict_no_pause,
        hash=hash,
        v=v,
        raw=json.dumps(raw.db_serialize()),
    )
    return await app.state.services.database.execute(insert_stmt)


async def fetch_one(id: int) -> SealedSbPatcherScoreMeta | None:
    select_stmt = select(*READ_PARAMS).where(SbPatcherScoreMetaTable.id == id)
    _meta = await app.state.services.database.fetch_one(select_stmt)
    return cast(SealedSbPatcherScoreMeta | None, _meta)
