from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from models.blogger import Blogger
    from models.blogger_content_dna import BloggerContentDNA
    from models.source_video import SourceVideo
    from models.generated_script import GeneratedScript
    from models.generated_video import GeneratedVideo
    from models.user_profile import UserProfile
    from models.factory import FactoryConfig, FactoryRun, FactoryItem, FactoryEvent, FactoryPublication
    Base.metadata.create_all(bind=engine)
    _ensure_user_profile_columns()
    _ensure_factory_columns()


def _ensure_factory_columns():
    if "sqlite" not in settings.DATABASE_URL:
        return
    with engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(factory_config)").fetchall()}
        if "auto_publish_xiaohongshu" not in existing:
            conn.exec_driver_sql("ALTER TABLE factory_config ADD COLUMN auto_publish_xiaohongshu BOOLEAN NOT NULL DEFAULT 0")
        if "visual_style" not in existing:
            conn.exec_driver_sql("ALTER TABLE factory_config ADD COLUMN visual_style VARCHAR(30) NOT NULL DEFAULT 'cards'")


def _ensure_user_profile_columns():
    if "sqlite" not in settings.DATABASE_URL:
        return
    with engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(user_profile)").fetchall()}
        if "voice_clone_sample_path" not in existing:
            conn.exec_driver_sql("ALTER TABLE user_profile ADD COLUMN voice_clone_sample_path VARCHAR(1024)")
        if "voice_clone_samples" not in existing:
            conn.exec_driver_sql("ALTER TABLE user_profile ADD COLUMN voice_clone_samples JSON")
        if "voice_clone_enabled" not in existing:
            conn.exec_driver_sql("ALTER TABLE user_profile ADD COLUMN voice_clone_enabled VARCHAR(20) DEFAULT 'false'")
