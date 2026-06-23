import os
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
from database import get_db, SessionLocal
from models.user_profile import UserProfile
from sqlalchemy.orm.attributes import flag_modified
from config import settings

router = APIRouter()


class SettingsUpdate(BaseModel):
    tts_voice: Optional[str] = None
    watermark: Optional[str] = None
    video_style: Optional[dict] = None


class VoiceSampleRename(BaseModel):
    name: str


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
    samples = list(profile.voice_clone_samples or [])
    if not samples and profile.voice_clone_sample_path:
        sample_id = "default"
        sample = {
            "id": sample_id,
            "name": "我的克隆音色",
            "path": profile.voice_clone_sample_path,
        }
        samples = [sample]
        profile.voice_clone_samples = samples
        flag_modified(profile, "voice_clone_samples")
        if not profile.tts_voice or not str(profile.tts_voice).startswith("clone:"):
            profile.tts_voice = f"clone:{sample_id}"
    return [{**sample, "url": sample.get("url") or _storage_url(sample.get("path"))} for sample in samples]


def _selected_clone_sample(profile: UserProfile) -> Optional[dict]:
    tts_voice = profile.tts_voice or ""
    if not tts_voice.startswith("clone:"):
        return None
    sample_id = tts_voice.removeprefix("clone:")
    return next((sample for sample in _voice_samples(profile) if sample.get("id") == sample_id), None)


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    profile = _get_or_create_profile(db)
    voice_samples = _voice_samples(profile)
    selected_clone = _selected_clone_sample(profile)
    db.commit()
    return {
        "id": profile.id,
        "photo_path": profile.photo_path,
        "photo_url": _storage_url(profile.photo_path),
        "tts_voice": profile.tts_voice,
        "watermark": profile.watermark,
        "video_style": profile.video_style,
        "voice_clone_sample_path": selected_clone.get("path") if selected_clone else None,
        "voice_clone_samples": voice_samples,
        "voice_clone_enabled": selected_clone is not None,
        "voice_clone_ready": any(sample.get("path") and os.path.exists(sample["path"]) for sample in voice_samples),
        "clone_engine_configured": bool(settings.VOICE_CLONE_COMMAND),
    }


@router.put("")
def update_settings(data: SettingsUpdate, db: Session = Depends(get_db)):
    profile = _get_or_create_profile(db)
    if data.tts_voice is not None:
        if data.tts_voice.startswith("clone:"):
            sample_id = data.tts_voice.removeprefix("clone:")
            sample = next((s for s in _voice_samples(profile) if s.get("id") == sample_id), None)
            if not sample or not sample.get("path") or not os.path.exists(sample["path"]):
                raise HTTPException(status_code=400, detail="声音样本不存在")
            profile.voice_clone_sample_path = sample["path"]
            profile.voice_clone_enabled = "true"
        else:
            profile.voice_clone_enabled = "false"
        profile.tts_voice = data.tts_voice
    if data.watermark is not None:
        profile.watermark = data.watermark
    if data.video_style is not None:
        profile.video_style = data.video_style
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
async def upload_voice_sample(file: UploadFile = File(...), name: str = Form(""), db: Session = Depends(get_db)):
    profile = _get_or_create_profile(db)
    ext = os.path.splitext(file.filename or "")[1].lower() or ".wav"
    if ext not in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}:
        raise HTTPException(status_code=400, detail="请上传音频文件")

    sample_id = str(uuid.uuid4())
    filename = f"voice_{sample_id}{ext}"
    filepath = settings.USER_DIR / filename

    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    label = name.strip() or os.path.splitext(file.filename or "")[0] or "我的克隆音色"
    sample = {
        "id": sample_id,
        "name": label,
        "path": str(filepath),
        "url": _storage_url(str(filepath)),
    }

    existing_samples = list(profile.voice_clone_samples or [])
    existing_samples.append(sample)
    profile.voice_clone_samples = existing_samples
    flag_modified(profile, "voice_clone_samples")

    # Make the newly uploaded voice selectable immediately in AI voice options.
    profile.voice_clone_sample_path = str(filepath)
    profile.voice_clone_enabled = "true"
    profile.tts_voice = f"clone:{sample_id}"

    db.commit()
    return {
        "sample": sample,
        "selected_tts_voice": profile.tts_voice,
        "voice_clone_samples": _voice_samples(profile),
        "voice_clone_enabled": profile.voice_clone_enabled == "true",
        "voice_clone_ready": True,
        "clone_engine_configured": bool(settings.VOICE_CLONE_COMMAND),
    }


@router.put("/voice-samples/{sample_id}")
def rename_voice_sample(sample_id: str, data: VoiceSampleRename, db: Session = Depends(get_db)):
    profile = _get_or_create_profile(db)
    samples = list(profile.voice_clone_samples or [])
    for s in samples:
        if s.get("id") == sample_id:
            s["name"] = data.name.strip() or s.get("name", "我的克隆音色")
            profile.voice_clone_samples = samples
            flag_modified(profile, "voice_clone_samples")
            db.commit()
            return {"sample": s, "voice_clone_samples": _voice_samples(profile)}
    raise HTTPException(status_code=404, detail="声音样本不存在")


@router.delete("/voice-samples/{sample_id}")
def delete_voice_sample(sample_id: str, db: Session = Depends(get_db)):
    profile = _get_or_create_profile(db)
    samples = list(profile.voice_clone_samples or [])
    target = next((s for s in samples if s.get("id") == sample_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="声音样本不存在")

    # Delete file
    filepath = target.get("path", "")
    if filepath and os.path.exists(filepath):
        os.remove(filepath)

    samples = [s for s in samples if s.get("id") != sample_id]
    profile.voice_clone_samples = samples
    flag_modified(profile, "voice_clone_samples")

    # Fallback selected voice after deleting a sample.
    if profile.tts_voice == f"clone:{sample_id}":
        if samples:
            next_sample = samples[0]
            profile.tts_voice = f"clone:{next_sample['id']}"
            profile.voice_clone_sample_path = next_sample.get("path")
            profile.voice_clone_enabled = "true"
        else:
            profile.tts_voice = settings.DEFAULT_TTS_VOICE
            profile.voice_clone_sample_path = None
            profile.voice_clone_enabled = "false"
    elif not samples:
        profile.voice_clone_sample_path = None
        profile.voice_clone_enabled = "false"

    db.commit()
    return {"deleted": True, "voice_clone_samples": _voice_samples(profile), "selected_tts_voice": profile.tts_voice}


@router.get("/tts-voices")
def list_tts_voices():
    """Available Edge-TTS Chinese voices + user uploaded voice samples"""
    db = SessionLocal()
    try:
        profile = db.query(UserProfile).first()
        voice_samples = _voice_samples(profile) if profile else []
        if profile:
            db.commit()
    finally:
        db.close()

    clone_voices = []
    for s in voice_samples:
        if s.get("path") and os.path.exists(s["path"]):
            clone_voices.append({
                "id": f"clone:{s['id']}",
                "name": s.get("name", "我的克隆音色"),
                "gender": "clone",
                "is_clone": True,
            })

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
        ] + clone_voices
    }
