import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = f"sqlite:///{BASE_DIR}/distill.db"

    # Claude API (via Anthropic-compatible gateway)
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_AUTH_TOKEN: str = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
    ANTHROPIC_BASE_URL: str = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    HTTPS_PROXY: str = os.getenv("HTTPS_PROXY", "")
    CLAUDE_MODEL: str = "claude-sonnet-4-6"
    FACTORY_FALLBACK_MODEL: str = "claude-sonnet-4-5-20250929"

    # Stop admitting work under memory pressure; resume requires user action.
    FACTORY_MAX_MEMORY_GIB: float = Field(default=8, gt=0)
    FACTORY_MIN_AVAILABLE_GIB: float = Field(default=2, gt=0)
    FACTORY_MIN_AVAILABLE_PERCENT: float = Field(default=8, gt=0, lt=100)
    FACTORY_MAX_MEMORY_STALL_PERCENT: float = Field(default=5, gt=0, le=100)

    # Redis (for Celery)
    REDIS_URL: str = "redis://localhost:6379/0"

    # Storage
    STORAGE_DIR: Path = BASE_DIR / "storage"
    VIDEO_DIR: Path = STORAGE_DIR / "videos"
    AUDIO_DIR: Path = STORAGE_DIR / "audio"
    TRANSCRIPT_DIR: Path = STORAGE_DIR / "transcripts"
    GENERATED_AUDIO_DIR: Path = STORAGE_DIR / "generated_audio"
    GENERATED_VIDEO_DIR: Path = STORAGE_DIR / "generated_videos"
    USER_DIR: Path = STORAGE_DIR / "user"

    # Whisper
    WHISPER_MODEL: str = "medium"  # tiny/base/small/medium/large

    # TTS
    DEFAULT_TTS_VOICE: str = "zh-CN-XiaoxiaoNeural"
    COSYVOICE_DIR: Path = BASE_DIR / "engines" / "CosyVoice"
    COSYVOICE_MODEL_DIR: Path = STORAGE_DIR / "models" / "CosyVoice2-0.5B"
    VOICE_CLONE_COMMAND: str = os.getenv(
        "VOICE_CLONE_COMMAND",
        f"python {BASE_DIR / 'services' / 'clone_tts.py'} --ref {{sample_path}} --text {{text_file}} --out {{output_path}}",
    )

    # Video output
    VIDEO_WIDTH: int = 1080
    VIDEO_HEIGHT: int = 1920
    VIDEO_FPS: int = 30

    # Scheduler
    DAILY_GENERATION_TIME: str = "08:00"
    SCRIPTS_PER_DAY: int = 3

    class Config:
        env_file = str(BASE_DIR / ".env")


settings = Settings()

if settings.HTTPS_PROXY:
    os.environ.setdefault("HTTPS_PROXY", settings.HTTPS_PROXY)

# Ensure storage dirs exist
for d in [settings.VIDEO_DIR, settings.AUDIO_DIR, settings.TRANSCRIPT_DIR,
          settings.GENERATED_AUDIO_DIR, settings.GENERATED_VIDEO_DIR, settings.USER_DIR]:
    d.mkdir(parents=True, exist_ok=True)
