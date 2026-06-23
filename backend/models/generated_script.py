import uuid
from datetime import datetime, date
from sqlalchemy import Column, String, DateTime, Date, JSON, Text, ForeignKey
from database import Base


class GeneratedScript(Base):
    __tablename__ = "generated_scripts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    blogger_id = Column(String(36), ForeignKey("bloggers.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(1024), nullable=True)
    script = Column(Text, nullable=True)
    hook = Column(String(1024), nullable=True)
    hashtags = Column(JSON, nullable=True)
    visual_suggestion = Column(String(2048), nullable=True)
    status = Column(String(50), nullable=False, default="pending")  # pending/approved/rejected/generating/generated_video/error
    scheduled_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
