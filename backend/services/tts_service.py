"""
TTS (Text-to-Speech) service using Edge-TTS.
Free, high-quality Chinese voice synthesis.
"""
import asyncio
import subprocess
import os
import tempfile
import re
from pathlib import Path
from config import settings

_xtts_model = None


def _get_xtts():
    """Lazily load XTTS-v2 model once per process (preserves CUDA context)."""
    global _xtts_model
    if _xtts_model is None:
        os.environ["COQUI_TOS_AGREED"] = "1"
        from TTS.api import TTS
        _xtts_model = TTS(model_name="tts_models/multilingual/multi-dataset/xtts_v2", gpu=True)
    return _xtts_model


def _edge_tts_text(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("<speak"):
        return text
    return text.replace("AI", "人工智能").replace("ai", "人工智能")


def normalize_reference_audio(input_path: str, output_path: str) -> str | None:
    """Convert uploaded voice samples to mono 24k wav with light denoise and loudness normalization."""
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vn",
            "-af", "highpass=f=80,lowpass=f=7600,afftdn=nf=-25,loudnorm=I=-18:TP=-2:LRA=11",
            "-ac", "1",
            "-ar", "24000",
            "-sample_fmt", "s16",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(output_path):
            return None
        return result.stderr[:500] if result.stderr else "Failed to normalize voice sample"
    except Exception as e:
        return str(e)[:500]


def _subtitle_text_weight(text: str) -> int:
    clean = re.sub(r"[\s，,。！？!?；;：:、.．]", "", text or "")
    return max(len(clean), 1)


def _estimate_subtitle_segments(segments: list[str], total_duration: float) -> list[dict]:
    weights = [_subtitle_text_weight(segment) for segment in segments]
    total_weight = sum(weights) or len(segments)
    current = 0.0
    subtitle_segments = []
    for index, segment in enumerate(segments):
        if index == len(segments) - 1:
            end = total_duration
        else:
            duration = total_duration * weights[index] / total_weight
            end = min(total_duration, current + duration)
        subtitle_segments.append({"text": segment, "start": current, "end": end})
        current = end
    return subtitle_segments


def _generate_whole_cloned_speech(segments: list[str], output_path: str, clone_sample_path: str) -> tuple[list[dict] | None, str | None]:
    text = "".join(segments)
    error = generate_speech(text, output_path, clone_sample_path=clone_sample_path)
    if error:
        return None, error
    duration = get_audio_duration(output_path)
    if duration <= 0:
        return None, "Invalid cloned TTS duration"
    return _estimate_subtitle_segments(segments, duration), None


def generate_speech(text: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural", clone_sample_path: str | None = None) -> str | None:
    """
    Generate speech audio from text. Uses the configured voice-clone command when a sample is available;
    otherwise falls back to Edge-TTS.
    """
    if clone_sample_path:
        error = _generate_cloned_speech(text, output_path, clone_sample_path)
        if error is None:
            return None
    try:
        error = asyncio.run(generate_speech_async(_edge_tts_text(text), output_path, voice))
        if error is None:
            return None
        cmd = [
            "edge-tts",
            "--voice", voice,
            "--rate=+8%",
            "--pitch=+2Hz",
            "-t", _edge_tts_text(text),
            "--write-media", output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(output_path):
            return None
        return result.stderr[:500] if result.stderr else "edge-tts failed"
    except subprocess.TimeoutExpired:
        return "TTS timeout"
    except FileNotFoundError:
        return "edge-tts not installed"
    except Exception as e:
        return str(e)[:500]


def _generate_cloned_speech(text: str, output_path: str, clone_sample_path: str) -> str | None:
    """Generate speech using Coqui TTS XTTS-v2 directly in-process (preserves CUDA)."""
    if not os.path.exists(clone_sample_path):
        return "Voice clone sample not found"

    try:
        tts = _get_xtts()
        output_is_mp3 = Path(output_path).suffix.lower() == ".mp3"
        tts_out = output_path
        if output_is_mp3:
            tts_out = output_path + ".wav"
        tts.tts_to_file(
            text=text,
            speaker_wav=str(clone_sample_path),
            language="zh-cn",
            file_path=tts_out,
        )
        if output_is_mp3:
            convert_cmd = [
                "ffmpeg", "-y",
                "-i", tts_out,
                "-vn",
                "-acodec", "libmp3lame",
                "-q:a", "2",
                output_path,
            ]
            convert = subprocess.run(convert_cmd, capture_output=True, text=True, timeout=60)
            try:
                os.remove(tts_out)
            except OSError:
                pass
            if convert.returncode != 0 or not os.path.exists(output_path):
                return convert.stderr[:500] if convert.stderr else "Failed to convert cloned audio to mp3"
        if os.path.exists(output_path):
            return None
        return "XTTS did not produce output file"
    except Exception as e:
        return str(e)[:500]


async def generate_speech_async(text: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural") -> str | None:
    """Async version using edge-tts Python library directly."""
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice, rate="+8%", pitch="+2Hz")
        await communicate.save(output_path)
        if os.path.exists(output_path):
            return None
        return "Failed to save audio"
    except Exception as e:
        return str(e)[:500]



def generate_segmented_speech(
    segments: list[str],
    output_path: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    pause_ms: int = 260,
    clone_sample_path: str | None = None,
) -> tuple[list[dict] | None, str | None]:
    """
    Generate one TTS file per sentence, concatenate them, and return exact subtitle timings.
    Returns (subtitle_segments, error_message).
    """
    clean_segments = [s.strip() for s in segments if s and s.strip()]
    if not clean_segments:
        return None, "No text segments to synthesize"
    if clone_sample_path:
        return _generate_whole_cloned_speech(clean_segments, output_path, clone_sample_path)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = output.parent / f"_{output.stem}_segments"
    temp_dir.mkdir(parents=True, exist_ok=True)

    audio_files = []
    subtitle_segments = []
    current = 0.0

    try:
        for index, text in enumerate(clean_segments):
            segment_path = temp_dir / f"segment_{index:03d}.mp3"
            error = generate_speech(text, str(segment_path), voice=voice, clone_sample_path=clone_sample_path)
            if error:
                return None, error
            duration = get_audio_duration(str(segment_path))
            if duration <= 0:
                return None, f"Invalid TTS duration for segment {index + 1}"
            audio_files.append(segment_path)
            subtitle_segments.append({"text": text, "start": current, "end": current + duration})
            current += duration + pause_ms / 1000

        silence_path = temp_dir / "silence.mp3"
        silence_seconds = max(pause_ms / 1000, 0.01)
        silence_cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=r=24000:cl=mono",
            "-t", str(silence_seconds),
            "-q:a", "9",
            "-acodec", "libmp3lame",
            str(silence_path),
        ]
        result = subprocess.run(silence_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None, result.stderr[:500] if result.stderr else "Failed to create silence audio"

        concat_list = temp_dir / "concat.txt"
        lines = []
        for index, audio_file in enumerate(audio_files):
            lines.append(f"file '{audio_file.as_posix()}'")
            if index != len(audio_files) - 1:
                lines.append(f"file '{silence_path.as_posix()}'")
        concat_list.write_text("\n".join(lines), encoding="utf-8")

        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(output),
        ]
        result = subprocess.run(concat_cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0 or not output.exists():
            return None, result.stderr[:500] if result.stderr else "Failed to concatenate TTS audio"

        return subtitle_segments, None
    finally:
        for path in temp_dir.glob("*"):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            temp_dir.rmdir()
        except OSError:
            pass


def get_audio_duration(audio_path: str) -> float:
    """Get duration of audio file in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(result.stdout.strip())
    except Exception:
        return 0.0
