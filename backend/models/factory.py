"""Durable factory state. Media and model outputs survive worker restarts."""
import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from database import Base


def uid():
    return str(uuid.uuid4())


class FactoryConfig(Base):
    __tablename__ = "factory_config"
    id = Column(Integer, primary_key=True, default=1)
    daily_target = Column(Integer, nullable=False, default=10)
    sector = Column(String(100), nullable=False, default="AI 科技")
    schedule_enabled = Column(Boolean, nullable=False, default=True)
    schedule_time = Column(String(5), nullable=False, default="00:30")
    timezone = Column(String(50), nullable=False, default="Asia/Shanghai")
    platforms = Column(JSON, nullable=False, default=lambda: ["douyin", "xiaohongshu", "kuaishou"])
    max_revisions = Column(Integer, nullable=False, default=1)
    max_llm_calls = Column(Integer, nullable=False, default=32)
    auto_publish_xiaohongshu = Column(Boolean, nullable=False, default=False)
    visual_style = Column(String(30), nullable=False, default="tech_montage")
    publish_enabled = Column(Boolean, nullable=False, default=False)
    publish_times = Column(JSON, nullable=False, default=lambda: ["06:00", "07:00", "08:00", "09:10", "10:30", "17:00", "19:00", "20:00", "21:00", "22:00"])
    publish_start_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FactoryRun(Base):
    __tablename__ = "factory_runs"
    id = Column(String(36), primary_key=True, default=uid)
    # Unique keys protect both the daily schedule and the single production lane.
    schedule_key = Column(String(64), unique=True, nullable=True)
    active_slot = Column(String(20), unique=True, nullable=True, default="factory")
    trigger = Column(String(20), nullable=False, default="manual")
    status = Column(String(30), nullable=False, default="queued")
    stage = Column(String(30), nullable=False, default="queued")
    target_count = Column(Integer, nullable=False, default=3)
    completed_count = Column(Integer, nullable=False, default=0)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    settings_snapshot = Column(JSON, nullable=False, default=dict)
    sources = Column(JSON, nullable=False, default=list)
    plan = Column(JSON, nullable=False, default=dict)
    report = Column(JSON, nullable=False, default=dict)
    llm_calls = Column(Integer, nullable=False, default=0)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    task_id = Column(String(100), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    heartbeat_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)


class FactoryItem(Base):
    __tablename__ = "factory_items"
    __table_args__ = (UniqueConstraint("run_id", "position", name="uq_factory_item_position"),)
    id = Column(String(36), primary_key=True, default=uid)
    run_id = Column(String(36), ForeignKey("factory_runs.id"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    status = Column(String(30), nullable=False, default="planned")
    brief = Column(JSON, nullable=False, default=dict)
    content = Column(JSON, nullable=False, default=dict)
    review = Column(JSON, nullable=False, default=dict)
    media = Column(JSON, nullable=False, default=dict)
    packages = Column(JSON, nullable=False, default=list)
    revisions = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class FactoryEvent(Base):
    __tablename__ = "factory_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), ForeignKey("factory_runs.id"), nullable=False, index=True)
    agent_id = Column(String(30), nullable=False)
    stage = Column(String(40), nullable=False)
    status = Column(String(30), nullable=False)
    message = Column(Text, nullable=False)
    detail = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class FactoryPublication(Base):
    __tablename__ = "factory_publications"
    __table_args__ = (UniqueConstraint("item_id", "platform", "action", name="uq_factory_publication_action"),)
    id = Column(String(36), primary_key=True, default=uid)
    item_id = Column(String(36), ForeignKey("factory_items.id"), nullable=False, index=True)
    platform = Column(String(30), nullable=False, default="xiaohongshu")
    action = Column(String(20), nullable=False)
    command_id = Column(String(36), nullable=True)
    status = Column(String(30), nullable=False, default="queued")
    result = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class FactoryRelease(Base):
    """One original per time slot; platform jobs retain their own receipts."""
    __tablename__ = "factory_releases"
    id = Column(String(36), primary_key=True, default=uid)
    due_at = Column(DateTime, nullable=False, unique=True, index=True)
    run_id = Column(String(36), ForeignKey("factory_runs.id"), nullable=True)
    position = Column(Integer, nullable=True)
    item_id = Column(String(36), ForeignKey("factory_items.id"), nullable=True, unique=True)
    platforms = Column(JSON, nullable=False, default=list)
    status = Column(String(30), nullable=False, default="scheduled")
    results = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
