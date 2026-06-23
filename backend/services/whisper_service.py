"""
Whisper speech-to-text service.
Uses faster-whisper (CTranslate2 backend) for fast local Chinese transcription.
"""
from faster_whisper import WhisperModel
from config import settings

_model = None


def _load_model():
    global _model
    if _model is None:
        # Use medium model for good Chinese accuracy
        # Options: tiny, base, small, medium, large-v3
        model_size = settings.WHISPER_MODEL
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _model


def transcribe_audio(audio_path: str) -> tuple[str | None, str | None]:
    """
    Transcribe audio file to text.
    Returns (transcript_text, error_message).
    """
    try:
        model = _load_model()
        segments, info = model.transcribe(
            audio_path,
            language="zh",
            beam_size=5,
            vad_filter=True,
        )

        # Build timestamped transcript
        transcript_parts = []
        full_text = ""

        for segment in segments:
            start = segment.start
            end = segment.end
            text = segment.text.strip()
            if text:
                m1, s1 = divmod(int(start), 60)
                m2, s2 = divmod(int(end), 60)
                transcript_parts.append(f"[{m1:02d}:{s1:02d}-{m2:02d}:{s2:02d}] {text}")
                full_text += text

        formatted = "\n".join(transcript_parts) if transcript_parts else full_text
        return formatted, None
    except Exception as e:
        return None, str(e)
