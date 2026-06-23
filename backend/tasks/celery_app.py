import sys
import os
# Ensure backend directory is on Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from celery import Celery
from config import settings

# Import all models so SQLAlchemy can resolve foreign key relationships
from models.blogger import Blogger, BloggerStatus
from models.blogger_content_dna import BloggerContentDNA
from models.source_video import SourceVideo
from models.generated_script import GeneratedScript
from models.generated_video import GeneratedVideo
from models.user_profile import UserProfile

celery_app = Celery(
    "distill_tasks",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


@celery_app.task(name="download_and_transcribe")
def run_download_and_transcribe(video_id: str):
    """Full pipeline: download video -> extract audio -> transcribe"""
    from database import SessionLocal
    from models.source_video import SourceVideo
    from services.video_downloader import download_video, extract_audio
    from services.whisper_service import transcribe_audio
    from config import settings
    import os

    db = SessionLocal()
    try:
        video = db.query(SourceVideo).filter(SourceVideo.id == video_id).first()
        if not video:
            return {"error": "Video not found"}

        # Step 1: Download
        video.status = "downloading"
        db.commit()

        video_dir = str(settings.VIDEO_DIR / video.blogger_id)
        os.makedirs(video_dir, exist_ok=True)

        video_path, error = download_video(video.source_url, video_dir, video_id)
        if error:
            video.status = "error"
            video.error_message = f"Download failed: {error}"
            db.commit()
            return {"error": error}

        video.video_path = video_path
        video.status = "downloaded"
        db.commit()

        # Step 2: Extract audio
        video.status = "extracting"
        db.commit()

        audio_dir = str(settings.AUDIO_DIR / video.blogger_id)
        os.makedirs(audio_dir, exist_ok=True)
        audio_path = os.path.join(audio_dir, f"{video_id}.mp3")

        error = extract_audio(video_path, audio_path)
        if error:
            video.status = "error"
            video.error_message = f"Audio extraction failed: {error}"
            db.commit()
            return {"error": error}

        video.audio_path = audio_path
        db.commit()

        # Step 3: Transcribe
        video.status = "transcribing"
        db.commit()

        transcript, error = transcribe_audio(audio_path)
        if error:
            video.status = "error"
            video.error_message = f"Transcription failed: {error}"
            db.commit()
            return {"error": error}

        video.transcript = transcript
        video.status = "transcribed"
        db.commit()

        return {"status": "transcribed", "video_id": video_id}

    except Exception as e:
        if video:
            video.status = "error"
            video.error_message = str(e)[:2000]
            db.commit()
        return {"error": str(e)}
    finally:
        db.close()


@celery_app.task(name="distill_analysis")
def run_distill_analysis(blogger_id: str):
    """Distill blogger content DNA from all transcribed videos"""
    from database import SessionLocal
    from models.blogger import Blogger, BloggerStatus
    from models.blogger_content_dna import BloggerContentDNA
    from models.source_video import SourceVideo
    from services.distill_service import analyze_blogger_content

    db = SessionLocal()
    try:
        blogger = db.query(Blogger).filter(Blogger.id == blogger_id).first()
        if not blogger:
            return {"error": "Blogger not found"}

        videos = db.query(SourceVideo).filter(
            SourceVideo.blogger_id == blogger_id,
            SourceVideo.transcript.isnot(None),
        ).all()

        transcripts = [v.transcript for v in videos if v.transcript]
        if not transcripts:
            blogger.status = BloggerStatus.ERROR.value
            blogger.error_message = "No transcribed videos available"
            db.commit()
            return {"error": "No transcripts"}

        blogger.status = BloggerStatus.ANALYZING.value
        db.commit()

        dna_data = analyze_blogger_content(transcripts)
        if not dna_data:
            blogger.status = BloggerStatus.ERROR.value
            blogger.error_message = "Claude analysis failed"
            db.commit()
            return {"error": "Analysis failed"}

        # Upsert DNA
        existing = db.query(BloggerContentDNA).filter(BloggerContentDNA.blogger_id == blogger_id).first()
        if existing:
            existing.value_positioning = dna_data.get("value_positioning")
            existing.viral_techniques = dna_data.get("viral_techniques")
            existing.content_preferences = dna_data.get("content_preferences")
            existing.language_style = dna_data.get("language_style")
            existing.content_calendar = dna_data.get("content_calendar")
            existing.raw_analysis = str(dna_data)
            existing.version = existing.version + 1
        else:
            dna = BloggerContentDNA(
                blogger_id=blogger_id,
                value_positioning=dna_data.get("value_positioning"),
                viral_techniques=dna_data.get("viral_techniques"),
                content_preferences=dna_data.get("content_preferences"),
                language_style=dna_data.get("language_style"),
                content_calendar=dna_data.get("content_calendar"),
                raw_analysis=str(dna_data),
            )
            db.add(dna)

        blogger.status = BloggerStatus.READY.value
        db.commit()

        return {"status": "ready", "blogger_id": blogger_id}

    except Exception as e:
        if blogger:
            blogger.status = BloggerStatus.ERROR.value
            blogger.error_message = str(e)[:2000]
            db.commit()
        return {"error": str(e)}
    finally:
        db.close()


@celery_app.task(name="generate_scripts")
def run_script_generation(blogger_id: str):
    """Generate daily scripts based on blogger content DNA"""
    from database import SessionLocal
    from models.blogger import Blogger
    from models.blogger_content_dna import BloggerContentDNA
    from models.generated_script import GeneratedScript
    from services.script_generator import generate_scripts
    from datetime import date

    db = SessionLocal()
    try:
        dna = db.query(BloggerContentDNA).filter(BloggerContentDNA.blogger_id == blogger_id).first()
        if not dna:
            return {"error": "No content DNA found. Analyze blogger first."}

        # Get recent scripts to avoid duplication
        recent_scripts = db.query(GeneratedScript).filter(
            GeneratedScript.blogger_id == blogger_id
        ).order_by(GeneratedScript.created_at.desc()).limit(20).all()
        recent_titles = [s.title for s in recent_scripts if s.title]

        scripts_data = generate_scripts(
            value_positioning=dna.value_positioning,
            viral_techniques=dna.viral_techniques,
            content_preferences=dna.content_preferences,
            language_style=dna.language_style,
            recent_titles=recent_titles,
        )

        if not scripts_data:
            return {"error": "Script generation failed"}

        created = []
        for s in scripts_data:
            script = GeneratedScript(
                blogger_id=blogger_id,
                title=s.get("title"),
                script=s.get("script"),
                hook=s.get("hook"),
                hashtags=s.get("hashtags"),
                visual_suggestion=s.get("visual_suggestion"),
                status="pending",
                scheduled_date=date.today(),
            )
            db.add(script)
            created.append(s.get("title"))

        db.commit()
        return {"status": "generated", "count": len(created), "titles": created}

    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()


@celery_app.task(name="generate_video")
def run_video_generation(script_id: str):
    """Generate final video: TTS + subtitles + photo -> MP4"""
    from database import SessionLocal
    from models.generated_script import GeneratedScript
    from models.generated_video import GeneratedVideo
    from models.user_profile import UserProfile
    from services.tts_service import generate_segmented_speech
    from services.video_composer import compose_video, split_script_sentences
    from config import settings
    import os

    db = SessionLocal()
    try:
        script = db.query(GeneratedScript).filter(GeneratedScript.id == script_id).first()
        if not script or not script.script:
            return {"error": "Script not found or empty"}

        profile = db.query(UserProfile).first()

        # Generate unique IDs for both audio and video files
        import uuid
        video_id = str(uuid.uuid4())

        # Step 1: TTS
        script.status = "generating"
        db.commit()

        audio_dir = str(settings.GENERATED_AUDIO_DIR)
        os.makedirs(audio_dir, exist_ok=True)
        audio_path = os.path.join(audio_dir, f"{video_id}.mp3")

        tts_voice = profile.tts_voice if profile else settings.DEFAULT_TTS_VOICE
        clone_sample_path = None

        # If a clone voice is selected, find the sample path
        if profile and tts_voice and tts_voice.startswith("clone:"):
            sample_id = tts_voice.removeprefix("clone:")
            samples = profile.voice_clone_samples or []
            matched = next((s for s in samples if s.get("id") == sample_id), None)
            if matched and matched.get("path"):
                clone_sample_path = matched["path"]
            tts_voice = settings.DEFAULT_TTS_VOICE
        elif profile and profile.voice_clone_enabled == "true" and profile.voice_clone_sample_path:
            clone_sample_path = profile.voice_clone_sample_path
        spoken_segments = split_script_sentences(script.script)
        subtitle_segments, error = generate_segmented_speech(
            spoken_segments,
            audio_path,
            voice=tts_voice,
            clone_sample_path=clone_sample_path,
        )
        if error:
            script.status = "error"
            db.commit()
            return {"error": f"TTS failed: {error}"}

        # Step 2: Compose video
        video_dir = str(settings.GENERATED_VIDEO_DIR)
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, f"{video_id}.mp4")

        photo_path = profile.photo_path if profile and profile.photo_path else None
        watermark = profile.watermark if profile else None

        duration, error = compose_video(
            photo_path=photo_path,
            audio_path=audio_path,
            script_text=script.script,
            output_path=video_path,
            watermark=watermark,
            subtitle_segments=subtitle_segments,
        )
        if error:
            script.status = "error"
            db.commit()
            return {"error": f"Video composition failed: {error}"}

        # Save result
        video = GeneratedVideo(
            id=video_id,
            script_id=script_id,
            video_path=video_path,
            audio_path=audio_path,
            subtitle_path=video_path.replace(".mp4", ".ass"),
            duration=duration,
            status="ready",
        )
        db.add(video)
        script.status = "generated_video"
        db.commit()

        return {"status": "ready", "video_id": video.id, "duration": duration}

    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()


@celery_app.task(name="generate_scripts_for_all_active")
def generate_scripts_for_all_active():
    """Daily scheduled task: generate scripts for all ready bloggers."""
    from database import SessionLocal
    from models.blogger import Blogger, BloggerStatus

    db = SessionLocal()
    try:
        active = db.query(Blogger).filter(Blogger.status == BloggerStatus.READY.value).all()
        results = []
        for blogger in active:
            result = run_script_generation.delay(blogger.id)
            results.append({"blogger_id": blogger.id, "task_id": result.id})
        return {"processed": len(results), "results": results}
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()


# Celery Beat schedule
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    "daily-script-generation": {
        "task": "generate_scripts_for_all_active",
        "schedule": crontab(hour=8, minute=0),
    },
}
