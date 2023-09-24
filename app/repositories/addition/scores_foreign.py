from app.state.services import orm_base
from sqlalchemy import Column, Integer, String, Boolean, DateTime, text

class ScoresForeign(orm_base):
    __tablename__ = "scores_foreign"
    
    id = Column(Integer, primary_key=True)
    server = Column(String(32), nullable=False)
    original_score_id = Column(Integer, nullable=False)
    original_player_id = Column(Integer, nullable=False)
    recipient_id = Column(Integer, nullable=False)
    has_replay = Column(Boolean, nullable=False)
    receipt_time = Column(DateTime, nullable=False, server_default=text("now()"))