from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.source_video import SourceVideo

router = APIRouter()


@router.get("/{video_id}")
def get_video(video_id: str, db: Session = Depends(get_db)):
    video = db.query(SourceVideo).filter(SourceVideo.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    return {
        "id": video.id,
        "blogger_id": video.blogger_id,
        "source_url": video.source_url,
        "title": video.title,
        "video_path": video.video_path,
        "audio_path": video.audio_path,
        "transcript": video.transcript,
        "metadata": video.extra_data,
        "status": video.status,
        "error_message": video.error_message,
        "created_at": video.created_at.isoformat() if video.created_at else None,
    }


@router.post("/{video_id}/transcribe")
def trigger_transcribe(video_id: str, db: Session = Depends(get_db)):
    video = db.query(SourceVideo).filter(SourceVideo.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    if not video.video_path:
        raise HTTPException(status_code=400, detail="视频尚未下载完成")

    video.status = "transcribing"
    db.commit()

    from tasks.celery_app import run_download_and_transcribe
    run_download_and_transcribe.delay(video_id)

    return {"status": "transcribing", "video_id": video_id}


@router.delete("/{video_id}")
def delete_video(video_id: str, db: Session = Depends(get_db)):
    video = db.query(SourceVideo).filter(SourceVideo.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    # Clean up files
    import os
    for path in [video.video_path, video.audio_path]:
        if path and os.path.exists(path):
            os.remove(path)
    db.delete(video)
    db.commit()
    return {"deleted": True}
