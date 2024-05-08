
from datetime import datetime
from enum import StrEnum
import json
from typing import TypedDict, cast
import app
from app.repositories import Base
from sqlalchemy import insert
from sqlalchemy import select
from sqlalchemy import Column, JSON, String, DateTime, BigInteger, Enum

class SuspicionKind(StrEnum):
    PPCAP = "ppcap"
    REPLAY = "replay"
    HASH = "hash"
    REPORT = "report"

class ScoresSuspicionTable(Base):
    __tablename__ = "scores_suspicion"

    score_id = Column(BigInteger, primary_key=True)
    kind = Column(Enum(SuspicionKind, name="kind"), nullable=False)
    reason = Column(String(128), nullable=False)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False)

class ScoresSuspicion(TypedDict):
    score_id: int
    kind: str
    reason: str
    detail: dict
    created_at: datetime

READ_PARAMS = (
    ScoresSuspicionTable.score_id,
    ScoresSuspicionTable.kind,
    ScoresSuspicionTable.reason,
    ScoresSuspicionTable.detail,
    ScoresSuspicionTable.created_at,
)

async def create(score_id: int, kind: SuspicionKind, reason: str, detail: dict) -> ScoresSuspicion:
    """Create a new score suspicion."""
    insert_stmt = insert(ScoresSuspicionTable).values(
        score_id=score_id,
        kind=str(kind),
        reason=reason,
        detail=json.dumps(detail),
        created_at=datetime.now()
    )
    await app.state.services.database.execute(insert_stmt)

    select_stmt = (
        select(*READ_PARAMS)
        .where(ScoresSuspicionTable.score_id == score_id)
    )
    _suspicion = await app.state.services.database.fetch_one(select_stmt)
    assert _suspicion is not None
    return cast(ScoresSuspicion, _suspicion)


async def has_suspicion(user_id) -> bool:
    return await app.state.services.database.fetch_one(
        "SELECT 1 FROM scores_suspicion su \
        JOIN scores sc ON su.score_id=sc.id \
        JOIN users us ON sc.userid=us.id \
        WHERE us.id=:user_id",
        {"user_id": user_id}
    )
    
    