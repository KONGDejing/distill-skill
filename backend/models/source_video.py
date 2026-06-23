import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON, Text, ForeignKey
from database import Base


class SourceVideo(Base):
    __tablename__ = "source_videos"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    blogger_id = Column(String(36), ForeignKey("bloggers.id", ondelete="CASCADE"), nullable=False)
    source_url = Column(String(2048), nullable=True)
    title = Column(String(1024), nullable=True)
    video_path = Column(String(1024), nullable=True)
    audio_path = Column(String(1024), nullable=True)
    transcript = Column(Text, nullable=True)
    extra_data = Column(JSON, nullable=True)
    status = Column(String(50), nullable=False, default="pending")  # pending/downloading/downloaded/extracting/transcribing/transcribed/analyzed/error
    error_message = Column(String(2048), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
