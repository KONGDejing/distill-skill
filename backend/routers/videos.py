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
@router.post("/{video_id}/retry")
def trigger_transcribe(video_id: str, db: Session = Depends(get_db)):
    video = db.query(SourceVideo).filter(SourceVideo.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    if video.status in {'downloading', 'extracting', 'transcribing'}:
        return {'status': video.status, 'video_id': video_id, 'message': '已有任务正在处理，未重复添加'}
    from services.video_downloader import normalize_video_url
    try:
        video.source_url = normalize_video_url(video.source_url)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    video.status = "pending"
    video.error_message = None
    db.commit()

    from tasks.celery_app import run_download_and_transcribe
    try:
        task = run_download_and_transcribe.delay(video_id)
    except Exception:
        video.status = 'error'; video.error_message = '下载队列暂时不可用，请稍后重试'; db.commit()
        raise HTTPException(503, video.error_message) from None

    return {"status": "pending", "video_id": video_id, 'task_id': task.id}


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
