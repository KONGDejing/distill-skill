import os
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
from database import get_db, SessionLocal
from models.user_profile import UserProfile
from config import settings

router = APIRouter()


class SettingsUpdate(BaseModel):
    tts_voice: Optional[str] = None
    watermark: Optional[str] = None
    video_style: Optional[dict] = None
    voice_clone_enabled: Optional[bool] = None


def _get_or_create_profile(db: Session) -> UserProfile:
    profile = db.query(UserProfile).first()
    if not profile:
        profile = UserProfile()
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _storage_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        rel = Path(path).resolve().relative_to(settings.STORAGE_DIR.resolve())
        return f"/storage/{rel.as_posix()}"
    except ValueError:
        return None


def _voice_samples(profile: UserProfile) -> list[dict]:
    samples = profile.voice_clone_samples or []
    if not samples and profile.voice_clone_sample_path:
        samples = [{
            "id": "default",
            "name": "我的克隆音色",
            "path": profile.voice_clone_sample_path,
            "url": _storage_url(profile.voice_clone_sample_path),
        }]
    return [{**sample, "url": sample.get("url") or _storage_url(sample.get("path"))} for sample in samples]


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    profile = _get_or_create_profile(db)
    voice_samples = _voice_samples(profile)
    return {
        "id": profile.id,
        "photo_path": profile.photo_path,
        "photo_url": _storage_url(profile.photo_path),
        "tts_voice": profile.tts_voice,
        "watermark": profile.watermark,
        "video_style": profile.video_style,
        "voice_clone_sample_path": profile.voice_clone_sample_path,
        "voice_clone_samples": voice_samples,
        "voice_clone_enabled": profile.voice_clone_enabled == "true",
        "voice_clone_ready": any(sample.get("path") and os.path.exists(sample["path"]) for sample in voice_samples),
        "clone_engine_configured": bool(settings.VOICE_CLONE_COMMAND),
    }


@router.put("")
def update_settings(data: SettingsUpdate, db: Session = Depends(get_db)):
    profile = _get_or_create_profile(db)
    if data.tts_voice is not None:
        profile.tts_voice = data.tts_voice
    if data.watermark is not None:
        profile.watermark = data.watermark
    if data.video_style is not None:
        profile.video_style = data.video_style
    if data.voice_clone_enabled is not None:
        voice_samples = _voice_samples(profile)
        if data.voice_clone_enabled and not any(sample.get("path") and os.path.exists(sample["path"]) for sample in voice_samples):
            raise HTTPException(status_code=400, detail="请先上传声音样本")
        profile.voice_clone_enabled = "true" if data.voice_clone_enabled else "false"
    db.commit()
    return {"updated": True}


@router.post("/upload-photo")
async def upload_photo(file: UploadFile = File(...), db: Session = Depends(get_db)):
    profile = _get_or_create_profile(db)
    ext = os.path.splitext(file.filename)[1] or ".jpg"
    filename = f"{profile.id}{ext}"
    filepath = settings.USER_DIR / filename

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    profile.photo_path = str(filepath)
    db.commit()
    return {"photo_path": str(filepath), "photo_url": _storage_url(str(filepath))}


@router.post("/upload-voice-sample")
async def upload_voice_sample(file: UploadFile = File(...), db: Session = Depends(get_db)):
    profile = _get_or_create_profile(db)
    ext = os.path.splitext(file.filename or "")[1].lower() or ".wav"
    if ext not in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}:
        raise HTTPException(status_code=400, detail="请上传音频文件")

    filename = f"voice_clone_{profile.id}{ext}"
    filepath = settings.USER_DIR / filename

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    profile.voice_clone_sample_path = str(filepath)
    samples = _voice_samples(profile)
    sample = {
        "id": str(uuid.uuid4()),
        "name": os.path.splitext(file.filename or "我的克隆音色")[0] or "我的克隆音色",
        "path": str(filepath),
        "url": _storage_url(str(filepath)),
    }
    samples = [s for s in samples if s.get("path") != str(filepath)] + [sample]
    profile.voice_clone_samples = samples
    profile.voice_clone_enabled = "true"
    db.commit()
    return {
        "voice_clone_sample_path": str(filepath),
        "voice_clone_samples": samples,
        "voice_clone_enabled": True,
        "voice_clone_ready": True,
        "clone_engine_configured": bool(settings.VOICE_CLONE_COMMAND),
    }


@router.get("/tts-voices")
def list_tts_voices():
    """Available Edge-TTS Chinese voices"""
    return {
        "voices": [
            {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓 (女声-温柔)", "gender": "female"},
            {"id": "zh-CN-YunxiNeural", "name": "云希 (男声-青年)", "gender": "male"},
            {"id": "zh-CN-YunjianNeural", "name": "云健 (男声-成熟)", "gender": "male"},
            {"id": "zh-CN-XiaoyiNeural", "name": "晓伊 (女声-活泼)", "gender": "female"},
            {"id": "zh-CN-YunyangNeural", "name": "云扬 (男声-新闻)", "gender": "male"},
            {"id": "zh-CN-XiaochenNeural", "name": "晓辰 (女声-知性)", "gender": "female"},
            {"id": "zh-CN-XiaohanNeural", "name": "晓涵 (女声-自信)", "gender": "female"},
            {"id": "zh-CN-XiaoxuanNeural", "name": "晓萱 (女声-亲切)", "gender": "female"},
            {"id": "zh-CN-XiaoshuangNeural", "name": "晓双 (女声-童声)", "gender": "female"},
            {"id": "zh-CN-YunxiaNeural", "name": "云夏 (男声-少年)", "gender": "male"},
            {"id": "zh-CN-YunyeNeural", "name": "云野 (男声-沉稳)", "gender": "male"},
            {"id": "zh-CN-XiaoruNeural", "name": "晓如 (女声-知性)", "gender": "female"},
        ]
    }
