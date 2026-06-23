import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Enum as SAEnum
from database import Base
import enum


class BloggerStatus(str, enum.Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    READY = "ready"
    ERROR = "error"


class Platform(str, enum.Enum):
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    KUAISHOU = "kuaishou"
    OTHER = "other"


class Blogger(Base):
    __tablename__ = "bloggers"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    platform = Column(String(50), nullable=False, default=Platform.OTHER.value)
    profile_url = Column(String(1024), nullable=True)
    profile_image = Column(String(1024), nullable=True)
    status = Column(String(50), nullable=False, default=BloggerStatus.PENDING.value)
    error_message = Column(String(2048), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
