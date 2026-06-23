import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, ForeignKey
from database import Base


class GeneratedVideo(Base):
    __tablename__ = "generated_videos"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    script_id = Column(String(36), ForeignKey("generated_scripts.id", ondelete="CASCADE"), nullable=False)
    video_path = Column(String(1024), nullable=True)
    audio_path = Column(String(1024), nullable=True)
    subtitle_path = Column(String(1024), nullable=True)
    duration = Column(Integer, nullable=True)  # seconds
    status = Column(String(50), nullable=False, default="pending")  # pending/generating/ready/error
    error_message = Column(String(2048), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
