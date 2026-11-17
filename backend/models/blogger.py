import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Enum as SAEnum, Boolean, Integer, JSON, ForeignKey
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
    WECHAT_CHANNELS = "wechat_channels"
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


class BloggerWatch(Base):
    """Incremental public reference monitoring, independent of publication."""
    __tablename__ = "blogger_watches"
    blogger_id = Column(String(36), ForeignKey("bloggers.id", ondelete="CASCADE"), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    interval_seconds = Column(Integer, nullable=False, default=10800)
    daily_import_limit = Column(Integer, nullable=False, default=6)
    status = Column(String(40), nullable=False, default="waiting")
    profile_url = Column(String(2048), nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    next_check_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_analyzed_at = Column(DateTime, nullable=True)
    analysis_signature = Column(String(64), nullable=True)
    summary = Column(JSON, nullable=False, default=dict)
    error_message = Column(String(1024), nullable=True)
