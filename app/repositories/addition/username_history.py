from app.state.services import orm_base
from sqlalchemy import Column, Integer, String, DateTime, text

class UsernameHistory(orm_base):
    __tablename__ = "username_history"
    
    user_id = Column(Integer, primary_key=True)
    change_date = Column(DateTime, primary_key=True, nullable=False)
    username = Column(String(32), nullable=False)