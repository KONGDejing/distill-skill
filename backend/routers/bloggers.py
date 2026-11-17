from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from database import get_db
from models.blogger import Blogger, BloggerStatus, Platform, BloggerWatch
from models.blogger_content_dna import BloggerContentDNA
from models.source_video import SourceVideo
from services.reference_watch import watch_summary

router = APIRouter()


class BloggerCreate(BaseModel):
    name: str
    platform: str = Platform.OTHER.value
    profile_url: Optional[str] = None


class BloggerUpdate(BaseModel):
    name: Optional[str] = None
    profile_url: Optional[str] = None


class VideoLinkAdd(BaseModel):
    source_url: str
    title: Optional[str] = None


class WatchUpdate(BaseModel):
    enabled: bool


def video_summary(video):
    from pathlib import Path
    from config import settings
    media_url = None
    if video.video_path:
        path = Path(video.video_path).resolve()
        if path.is_relative_to(settings.STORAGE_DIR.resolve()) and path.is_file():
            media_url = '/storage/' + path.relative_to(settings.STORAGE_DIR.resolve()).as_posix()
    return {'id': video.id, 'source_url': video.source_url, 'title': video.title,
            'status': video.status, 'error_message': video.error_message, 'video_url': media_url,
            'has_transcript': bool(video.transcript),
            'transcript_preview': video.transcript[:500] if video.transcript else None,
            'created_at': video.created_at.isoformat() if video.created_at else None}


@router.get("")
def list_bloggers(db: Session = Depends(get_db)):
    bloggers = db.query(Blogger).order_by(Blogger.created_at.desc()).all()
    result = []
    for b in bloggers:
        video_count = db.query(SourceVideo).filter(SourceVideo.blogger_id == b.id).count()
        has_dna = db.query(BloggerContentDNA).filter(BloggerContentDNA.blogger_id == b.id).first() is not None
        result.append({
            "id": b.id,
            "name": b.name,
            "platform": b.platform,
            "profile_url": b.profile_url,
            "status": b.status,
            "error_message": b.error_message,
            "video_count": video_count,
            "has_dna": has_dna,
            "watch": watch_summary(db.get(BloggerWatch, b.id)),
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "updated_at": b.updated_at.isoformat() if b.updated_at else None,
        })
    return {"bloggers": result}


@router.post("")
def create_blogger(data: BloggerCreate, db: Session = Depends(get_db)):
    blogger = Blogger(
        name=data.name,
        platform=data.platform,
        profile_url=data.profile_url,
    )
    db.add(blogger)
    db.commit()
    db.refresh(blogger)
    return {"id": blogger.id, "name": blogger.name, "status": blogger.status}


@router.get("/{blogger_id}")
def get_blogger(blogger_id: str, db: Session = Depends(get_db)):
    blogger = db.query(Blogger).filter(Blogger.id == blogger_id).first()
    if not blogger:
        raise HTTPException(status_code=404, detail="博主不存在")

    dna = db.query(BloggerContentDNA).filter(BloggerContentDNA.blogger_id == blogger_id).first()
    videos = db.query(SourceVideo).filter(SourceVideo.blogger_id == blogger_id).order_by(SourceVideo.created_at.desc()).all()

    return {
        "id": blogger.id,
        "name": blogger.name,
        "platform": blogger.platform,
        "profile_url": blogger.profile_url,
        "status": blogger.status,
        "error_message": blogger.error_message,
        "created_at": blogger.created_at.isoformat() if blogger.created_at else None,
        "updated_at": blogger.updated_at.isoformat() if blogger.updated_at else None,
        "watch": watch_summary(db.get(BloggerWatch, blogger_id)),
        "content_dna": {
            "value_positioning": dna.value_positioning,
            "viral_techniques": dna.viral_techniques,
            "content_preferences": dna.content_preferences,
            "language_style": dna.language_style,
            "content_calendar": dna.content_calendar,
            "version": dna.version,
        } if dna else None,
        "videos": [video_summary(v) for v in videos],
    }


@router.delete("/{blogger_id}")
def delete_blogger(blogger_id: str, db: Session = Depends(get_db)):
    blogger = db.query(Blogger).filter(Blogger.id == blogger_id).first()
    if not blogger:
        raise HTTPException(status_code=404, detail="博主不存在")
    watch = db.get(BloggerWatch, blogger_id)
    if watch:
        db.delete(watch)
    db.delete(blogger)
    db.commit()
    return {"deleted": True}


@router.put('/{blogger_id}/watch')
def update_watch(blogger_id: str, data: WatchUpdate, db: Session = Depends(get_db)):
    from datetime import datetime
    from services.reference_watch import profile_link, watch_summary
    blogger = db.get(Blogger, blogger_id)
    if not blogger:
        raise HTTPException(404, '博主不存在')
    watch = db.get(BloggerWatch, blogger_id)
    if data.enabled:
        if blogger.platform != 'douyin':
            raise HTTPException(422, '自动跟踪暂支持抖音公开主页')
        try:
            url = profile_link(blogger.profile_url)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
    if not watch:
        watch = BloggerWatch(blogger_id=blogger_id)
        db.add(watch)
    watch.enabled = data.enabled
    if data.enabled:
        watch.profile_url = url
        if watch.status not in {'checking', 'importing', 'analyzing'}:
            watch.status, watch.next_check_at, watch.error_message = 'waiting', datetime.utcnow(), None
    else:
        watch.status = 'disabled'
    db.commit()
    return watch_summary(watch)


@router.post('/{blogger_id}/watch/refresh')
def refresh_watch(blogger_id: str, db: Session = Depends(get_db)):
    from datetime import datetime
    from services.reference_watch import watch_summary
    watch = db.get(BloggerWatch, blogger_id)
    if not watch or not watch.enabled:
        raise HTTPException(409, '请先启用自动跟踪')
    if watch.status not in {'checking', 'importing', 'analyzing'}:
        watch.status, watch.next_check_at, watch.error_message = 'waiting', datetime.utcnow(), None
        db.commit()
    return watch_summary(watch)


@router.post("/{blogger_id}/videos")
def add_video(blogger_id: str, data: VideoLinkAdd, db: Session = Depends(get_db)):
    blogger = db.query(Blogger).filter(Blogger.id == blogger_id).first()
    if not blogger:
        raise HTTPException(status_code=404, detail="博主不存在")

    from services.video_downloader import normalize_video_url
    try:
        source_url = normalize_video_url(data.source_url)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    from sqlalchemy import update
    db.execute(update(Blogger).where(Blogger.id == blogger_id).values(updated_at=Blogger.updated_at))
    for existing in db.query(SourceVideo).filter_by(blogger_id=blogger_id).order_by(SourceVideo.created_at):
        try:
            if normalize_video_url(existing.source_url) == source_url:
                return {**video_summary(existing), 'reused': True, 'message': '该视频已存在，保留原记录；失败时可点击重新导入。'}
        except ValueError:
            continue
    video = SourceVideo(
        blogger_id=blogger_id,
        source_url=source_url,
        title=data.title,
        status="pending",
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    # Auto-trigger download + transcribe pipeline
    from tasks.celery_app import run_download_and_transcribe
    try:
        task = run_download_and_transcribe.delay(str(video.id))
    except Exception:
        video.status = 'error'; video.error_message = '下载队列暂时不可用，请点击重新导入'
        db.commit()
        raise HTTPException(503, video.error_message) from None

    return {"id": video.id, "source_url": video.source_url, "status": video.status, "task": "download_and_transcribe", 'task_id': task.id}


@router.get("/{blogger_id}/videos")
def list_blogger_videos(blogger_id: str, db: Session = Depends(get_db)):
    videos = db.query(SourceVideo).filter(SourceVideo.blogger_id == blogger_id).order_by(SourceVideo.created_at.desc()).all()
    return {
        "videos": [video_summary(v) for v in videos]
    }


@router.post("/{blogger_id}/analyze")
def trigger_analyze(blogger_id: str, db: Session = Depends(get_db)):
    blogger = db.query(Blogger).filter(Blogger.id == blogger_id).first()
    if not blogger:
        raise HTTPException(status_code=404, detail="博主不存在")

    if blogger.status == BloggerStatus.ANALYZING.value:
        return {"status": "analyzing", "blogger_id": blogger_id, "message": "已有分析进行中，未重复安排"}

    videos = db.query(SourceVideo).filter(SourceVideo.blogger_id == blogger_id).all()
    transcripts = [v.transcript for v in videos if v.transcript]
    if not transcripts:
        raise HTTPException(status_code=400, detail="没有可分析的转录文本，请先添加视频并完成转写")

    blogger.status = BloggerStatus.ANALYZING.value
    blogger.error_message = None
    db.commit()

    # Trigger async task
    from tasks.celery_app import run_distill_analysis
    try:
        run_distill_analysis.delay(blogger_id)
    except Exception:
        blogger.status = BloggerStatus.ERROR.value
        blogger.error_message = '分析队列暂时不可用，请稍后重新分析'
        db.commit()
        raise HTTPException(503, blogger.error_message) from None

    return {"status": "analyzing", "blogger_id": blogger_id}


@router.post("/{blogger_id}/re-analyze")
def trigger_re_analyze(blogger_id: str, db: Session = Depends(get_db)):
    return trigger_analyze(blogger_id, db)
