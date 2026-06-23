from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from database import get_db
from models.blogger import Blogger, BloggerStatus, Platform
from models.blogger_content_dna import BloggerContentDNA
from models.source_video import SourceVideo

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
        "content_dna": {
            "value_positioning": dna.value_positioning,
            "viral_techniques": dna.viral_techniques,
            "content_preferences": dna.content_preferences,
            "language_style": dna.language_style,
            "content_calendar": dna.content_calendar,
            "version": dna.version,
        } if dna else None,
        "videos": [
            {
                "id": v.id,
                "source_url": v.source_url,
                "title": v.title,
                "status": v.status,
                "transcript_preview": v.transcript[:500] if v.transcript else None,
                "has_transcript": v.transcript is not None,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in videos
        ],
    }


@router.delete("/{blogger_id}")
def delete_blogger(blogger_id: str, db: Session = Depends(get_db)):
    blogger = db.query(Blogger).filter(Blogger.id == blogger_id).first()
    if not blogger:
        raise HTTPException(status_code=404, detail="博主不存在")
    db.delete(blogger)
    db.commit()
    return {"deleted": True}


@router.post("/{blogger_id}/videos")
def add_video(blogger_id: str, data: VideoLinkAdd, db: Session = Depends(get_db)):
    blogger = db.query(Blogger).filter(Blogger.id == blogger_id).first()
    if not blogger:
        raise HTTPException(status_code=404, detail="博主不存在")

    video = SourceVideo(
        blogger_id=blogger_id,
        source_url=data.source_url,
        title=data.title,
        status="pending",
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    # Auto-trigger download + transcribe pipeline
    from tasks.celery_app import run_download_and_transcribe
    run_download_and_transcribe.delay(str(video.id))

    return {"id": video.id, "source_url": video.source_url, "status": video.status, "task": "download_and_transcribe"}


@router.get("/{blogger_id}/videos")
def list_blogger_videos(blogger_id: str, db: Session = Depends(get_db)):
    videos = db.query(SourceVideo).filter(SourceVideo.blogger_id == blogger_id).order_by(SourceVideo.created_at.desc()).all()
    return {
        "videos": [
            {
                "id": v.id,
                "source_url": v.source_url,
                "title": v.title,
                "status": v.status,
                "has_transcript": v.transcript is not None,
                "transcript_preview": v.transcript[:500] if v.transcript else None,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in videos
        ]
    }


@router.post("/{blogger_id}/analyze")
def trigger_analyze(blogger_id: str, db: Session = Depends(get_db)):
    blogger = db.query(Blogger).filter(Blogger.id == blogger_id).first()
    if not blogger:
        raise HTTPException(status_code=404, detail="博主不存在")

    videos = db.query(SourceVideo).filter(SourceVideo.blogger_id == blogger_id).all()
    transcripts = [v.transcript for v in videos if v.transcript]
    if not transcripts:
        raise HTTPException(status_code=400, detail="没有可分析的转录文本，请先添加视频并完成转写")

    blogger.status = BloggerStatus.ANALYZING.value
    db.commit()

    # Trigger async task
    from tasks.celery_app import run_distill_analysis
    run_distill_analysis.delay(blogger_id)

    return {"status": "analyzing", "blogger_id": blogger_id}


@router.post("/{blogger_id}/re-analyze")
def trigger_re_analyze(blogger_id: str, db: Session = Depends(get_db)):
    return trigger_analyze(blogger_id, db)
