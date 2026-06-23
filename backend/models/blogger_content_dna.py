import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, JSON, ForeignKey
from database import Base


class BloggerContentDNA(Base):
    __tablename__ = "blogger_content_dna"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    blogger_id = Column(String(36), ForeignKey("bloggers.id", ondelete="CASCADE"), nullable=False, unique=True)
    value_positioning = Column(JSON, nullable=True)
    viral_techniques = Column(JSON, nullable=True)
    content_preferences = Column(JSON, nullable=True)
    language_style = Column(JSON, nullable=True)
    content_calendar = Column(JSON, nullable=True)
    raw_analysis = Column(String(16384), nullable=True)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
