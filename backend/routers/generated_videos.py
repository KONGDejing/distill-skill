import os
from datetime import timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models.generated_video import GeneratedVideo

# Beijing timezone UTC+8
BJT = timezone(timedelta(hours=8))


def _to_beijing(dt):
    """Convert UTC datetime to Beijing time string."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BJT).strftime('%Y-%m-%d %H:%M:%S')


router = APIRouter()


@router.get("")
def list_generated_videos(db: Session = Depends(get_db)):
    videos = db.query(GeneratedVideo).order_by(GeneratedVideo.created_at.desc()).limit(50).all()
    return {
        "videos": [
            {
                "id": v.id,
                "script_id": v.script_id,
                "video_path": v.video_path,
                "duration": v.duration,
                "status": v.status,
                "error_message": v.error_message,
                "created_at": _to_beijing(v.created_at),
            }
            for v in videos
        ]
    }


@router.get("/{video_id}")
def get_generated_video(video_id: str, db: Session = Depends(get_db)):
    video = db.query(GeneratedVideo).filter(GeneratedVideo.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    return {
        "id": video.id,
        "script_id": video.script_id,
        "video_path": video.video_path,
        "audio_path": video.audio_path,
        "subtitle_path": video.subtitle_path,
        "duration": video.duration,
        "status": video.status,
        "error_message": video.error_message,
        "created_at": video.created_at.isoformat() if video.created_at else None,
    }


@router.delete("/{video_id}")
def delete_generated_video(video_id: str, db: Session = Depends(get_db)):
    video = db.query(GeneratedVideo).filter(GeneratedVideo.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    for path in [video.video_path, video.audio_path, video.subtitle_path]:
        if path and os.path.exists(path):
            os.remove(path)
    db.delete(video)
    db.commit()
    return {"deleted": True}


@router.get("/{video_id}/download")
def download_video(video_id: str, db: Session = Depends(get_db)):
    video = db.query(GeneratedVideo).filter(GeneratedVideo.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    if not video.video_path or not os.path.exists(video.video_path):
        raise HTTPException(status_code=404, detail="视频文件不存在")
    return FileResponse(video.video_path, media_type="video/mp4", filename=f"output_{video_id}.mp4")
