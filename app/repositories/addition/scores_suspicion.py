from app.state.services import orm_base
from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON, text

class ScoresSuspicion(orm_base):
    __tablename__ = "scores_suspicion"
    
    score_id = Column(Integer, primary_key=True)
    suspicion_reason = Column(String(128), nullable=False)
    ignored = Column(Boolean, nullable=False, default=False)
    circleguard_detail = Column(JSON, nullable=True)
    suspicion_time = Column(DateTime, nullable=False)