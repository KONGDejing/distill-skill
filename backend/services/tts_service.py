"""
TTS (Text-to-Speech) service using Edge-TTS.
Free, high-quality Chinese voice synthesis.
"""
import asyncio
import subprocess
import os
import tempfile
from pathlib import Path
from config import settings


def generate_speech(text: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural", clone_sample_path: str | None = None) -> str | None:
    """
    Generate speech audio from text. Uses the configured voice-clone command when a sample is available;
    otherwise falls back to Edge-TTS.
    """
    if clone_sample_path and settings.VOICE_CLONE_COMMAND:
        error = _generate_cloned_speech(text, output_path, clone_sample_path)
        if error is None:
            return None
    try:
        cmd = [
            "edge-tts",
            "--voice", voice,
            "--text", text,
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
    """
    Run a local or external voice-clone command.
    The command may use: {text_file}, {output_path}, {sample_path}
    Example:
    VOICE_CLONE_COMMAND='python clone_tts.py --ref {sample_path} --text {text_file} --out {output_path}'
    """
    if not os.path.exists(clone_sample_path):
        return "Voice clone sample not found"

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as f:
        f.write(text)
        text_file = f.name

    try:
        command = settings.VOICE_CLONE_COMMAND.format(
            text_file=text_file,
            output_path=output_path,
            sample_path=clone_sample_path,
        )
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=180)
        if result.returncode == 0 and os.path.exists(output_path):
            return None
        return result.stderr[:500] if result.stderr else "voice clone command failed"
    except subprocess.TimeoutExpired:
        return "Voice clone TTS timeout"
    except Exception as e:
        return str(e)[:500]
    finally:
        try:
            os.remove(text_file)
        except OSError:
            pass


async def generate_speech_async(text: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural") -> str | None:
    """Async version using edge-tts Python library directly."""
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
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
    pause_ms: int = 140,
    clone_sample_path: str | None = None,
) -> tuple[list[dict] | None, str | None]:
    """
    Generate one TTS file per sentence, concatenate them, and return exact subtitle timings.
    Returns (subtitle_segments, error_message).
    """
    clean_segments = [s.strip() for s in segments if s and s.strip()]
    if not clean_segments:
        return None, "No text segments to synthesize"

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
