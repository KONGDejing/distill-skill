from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import date
from database import get_db
from models.generated_script import GeneratedScript
from models.blogger import Blogger

router = APIRouter()


class ScriptUpdate(BaseModel):
    title: Optional[str] = None
    script: Optional[str] = None
    hook: Optional[str] = None
    hashtags: Optional[list] = None
    visual_suggestion: Optional[str] = None


@router.get("")
def list_scripts(
    blogger_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(GeneratedScript).order_by(GeneratedScript.created_at.desc())
    if blogger_id:
        q = q.filter(GeneratedScript.blogger_id == blogger_id)
    if status:
        q = q.filter(GeneratedScript.status == status)
    else:
        q = q.filter(GeneratedScript.status != "trashed")

    scripts = q.limit(50).all()
    return {
        "scripts": [
            {
                "id": s.id,
                "blogger_id": s.blogger_id,
                "title": s.title,
                "script": s.script,
                "hook": s.hook,
                "hashtags": s.hashtags,
                "visual_suggestion": s.visual_suggestion,
                "status": s.status,
                "scheduled_date": s.scheduled_date.isoformat() if s.scheduled_date else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in scripts
        ]
    }


@router.get("/{script_id}")
def get_script(script_id: str, db: Session = Depends(get_db)):
    script = db.query(GeneratedScript).filter(GeneratedScript.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="文案不存在")
    return {
        "id": script.id,
        "blogger_id": script.blogger_id,
        "title": script.title,
        "script": script.script,
        "hook": script.hook,
        "hashtags": script.hashtags,
        "visual_suggestion": script.visual_suggestion,
        "status": script.status,
        "scheduled_date": script.scheduled_date.isoformat() if script.scheduled_date else None,
        "created_at": script.created_at.isoformat() if script.created_at else None,
    }


@router.patch("/{script_id}")
def update_script(script_id: str, data: ScriptUpdate, db: Session = Depends(get_db)):
    script = db.query(GeneratedScript).filter(GeneratedScript.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="文案不存在")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(script, field, value)
    db.commit()
    return {"updated": True}


@router.delete("/trash/empty")
def empty_trash(db: Session = Depends(get_db)):
    deleted = db.query(GeneratedScript).filter(GeneratedScript.status == "trashed").delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}


@router.delete("/{script_id}")
def delete_script(script_id: str, db: Session = Depends(get_db)):
    script = db.query(GeneratedScript).filter(GeneratedScript.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="文案不存在")
    if script.status == "trashed":
        db.delete(script)
        db.commit()
        return {"deleted": True}
    script.status = "trashed"
    db.commit()
    return {"trashed": True}


@router.post("/{script_id}/restore")
def restore_script(script_id: str, db: Session = Depends(get_db)):
    script = db.query(GeneratedScript).filter(GeneratedScript.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="文案不存在")
    if script.status == "trashed":
        script.status = "pending"
        db.commit()
    return {"status": script.status}


@router.delete("/{script_id}/permanent")
def permanently_delete_script(script_id: str, db: Session = Depends(get_db)):
    script = db.query(GeneratedScript).filter(GeneratedScript.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="文案不存在")
    db.delete(script)
    db.commit()
    return {"deleted": True}


@router.post("/{script_id}/approve")
def approve_script(script_id: str, db: Session = Depends(get_db)):
    script = db.query(GeneratedScript).filter(GeneratedScript.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="文案不存在")
    script.status = "approved"
    db.commit()
    return {"status": "approved"}


@router.post("/{script_id}/reject")
def reject_script(script_id: str, db: Session = Depends(get_db)):
    script = db.query(GeneratedScript).filter(GeneratedScript.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="文案不存在")
    script.status = "rejected"
    db.commit()
    return {"status": "rejected"}


@router.post("/generate")
def trigger_generate(blogger_id: str = Query(...), db: Session = Depends(get_db)):
    blogger = db.query(Blogger).filter(Blogger.id == blogger_id).first()
    if not blogger:
        raise HTTPException(status_code=404, detail="博主不存在")

    from tasks.celery_app import run_script_generation
    run_script_generation.delay(blogger_id)

    return {"status": "generating", "blogger_id": blogger_id}


@router.post("/{script_id}/generate-video")
def trigger_video_generation(script_id: str, db: Session = Depends(get_db)):
    script = db.query(GeneratedScript).filter(GeneratedScript.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="文案不存在")

    script.status = "generating"
    db.commit()

    from tasks.celery_app import run_video_generation
    run_video_generation.delay(script_id)

    return {"status": "generating", "script_id": script_id}
