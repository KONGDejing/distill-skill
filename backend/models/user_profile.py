import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON
from database import Base


class UserProfile(Base):
    __tablename__ = "user_profile"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    photo_path = Column(String(1024), nullable=True)
    tts_voice = Column(String(255), nullable=True, default="zh-CN-XiaoxiaoNeural")
    voice_clone_sample_path = Column(String(1024), nullable=True)
    voice_clone_samples = Column(JSON, nullable=True)
    voice_clone_enabled = Column(String(20), nullable=True, default="false")
    watermark = Column(String(255), nullable=True)
    video_style = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
