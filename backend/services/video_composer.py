"""
Video composition service.
Combines a digital presenter scene + TTS audio + mobile ASS subtitles -> MP4.
Uses FFmpeg for rendering and Pillow for scene assets.
"""
import os
import re
import subprocess
from config import settings

TRAILING_SUBTITLE_PUNCTUATION = "，,。！？!?；;：:、."


def compose_video(
    photo_path: str | None,
    audio_path: str,
    script_text: str,
    output_path: str,
    watermark: str | None = None,
    bg_music: str | None = None,
    subtitle_segments: list[dict] | None = None,
) -> tuple[int | None, str | None]:
    """
    Compose final MP4 video.
    Layout: animated digital presenter scene + voice audio + mobile-safe ASS subtitles.
    Returns (duration_seconds, error_message).
    """
    if not os.path.exists(audio_path):
        return None, "Audio file not found"

    duration = _get_duration(audio_path)
    if duration <= 0:
        return None, "Could not determine audio duration"

    subtitle_path = output_path.replace(".mp4", ".ass")
    _generate_subtitles(script_text, duration, subtitle_path, subtitle_segments=subtitle_segments)

    scene_path = _create_digital_presenter_scene(photo_path)
    subtitle_path_escaped = subtitle_path.replace(":", "\\\\:").replace("\\", "/")

    temp_video = output_path.replace(".mp4", "_temp.mp4")
    cmd_bg = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", scene_path,
        "-t", str(duration),
        "-vf",
        (
            f"zoompan=z='1+0.025*sin(on/90)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:s={settings.VIDEO_WIDTH}x{settings.VIDEO_HEIGHT}:fps={settings.VIDEO_FPS},"
            "eq=contrast=1.05:saturation=1.08"
        ),
        "-pix_fmt", "yuv420p",
        "-r", str(settings.VIDEO_FPS),
        temp_video,
    ]
    result = subprocess.run(cmd_bg, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        return None, f"Background generation failed: {result.stderr[:500]}"

    vf_filter = f"subtitles={subtitle_path_escaped}"
    if watermark:
        safe_watermark = _escape_drawtext(f"@{watermark}")
        vf_filter += f",drawtext=text='{safe_watermark}':x=w-tw-44:y=h-th-48:fontsize=30:fontcolor=white@0.55"

    cmd_final = [
        "ffmpeg", "-y",
        "-i", temp_video,
        "-i", audio_path,
        "-vf", vf_filter,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    result = subprocess.run(cmd_final, capture_output=True, text=True, timeout=180)

    if os.path.exists(temp_video):
        os.remove(temp_video)

    if result.returncode != 0:
        return None, f"Video composition failed: {result.stderr[:500]}"

    if not os.path.exists(output_path):
        return None, "Output video not created"

    return int(duration), None


def split_script_sentences(text: str) -> list[str]:
    """Split script by natural spoken pauses; keep punctuation for TTS prosody."""
    normalized = re.sub(r"[\r\n]+", "。", text or "")
    normalized = re.sub(r"\s+", "", normalized)
    parts = re.split(r"(?<=[，,；;：:。！？!?])", normalized)
    segments = []
    for part in parts:
        segment = part.strip()
        if not segment:
            continue
        segment = re.sub(r"^[A-Za-z0-9一二三四五六七八九十]+[：:、.．]\s*", "", segment)
        if segment:
            segments.append(segment)
    return segments or ([text.strip()] if text and text.strip() else [])


def _get_duration(file_path: str) -> float:
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _generate_subtitles(
    text: str,
    total_duration: float,
    output_path: str,
    subtitle_segments: list[dict] | None = None,
):
    """Generate ASS subtitles: one natural spoken pause per cue, no trailing display punctuation."""
    segments = subtitle_segments or _estimate_sentence_segments(text, total_duration)
    if not segments:
        return

    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {settings.VIDEO_WIDTH}",
        f"PlayResY: {settings.VIDEO_HEIGHT}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Noto Sans CJK SC,64,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,5,1,2,90,90,300,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for seg in segments:
        start = max(float(seg.get("start", 0)), 0.0)
        end = min(float(seg.get("end", total_duration)), total_duration)
        if end <= start:
            continue
        display_text = _wrap_subtitle_text(_strip_trailing_punctuation(str(seg.get("text", ""))))
        if not display_text:
            continue
        safe_text = _escape_ass(display_text)
        ass_lines.append(f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},Default,,0,0,0,,{safe_text}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_lines))


def _estimate_sentence_segments(text: str, total_duration: float) -> list[dict]:
    sentences = split_script_sentences(text)
    total_chars = sum(max(len(_strip_trailing_punctuation(s)), 1) for s in sentences)
    current = 0.0
    result = []
    for i, sentence in enumerate(sentences):
        if i == len(sentences) - 1:
            end = total_duration
        else:
            ratio = max(len(_strip_trailing_punctuation(sentence)), 1) / max(total_chars, 1)
            end = min(current + max(ratio * total_duration, 0.9), total_duration)
        result.append({"text": sentence, "start": current, "end": end})
        current = end
    return result


def _strip_trailing_punctuation(text: str) -> str:
    return (text or "").strip().rstrip(TRAILING_SUBTITLE_PUNCTUATION).strip()


def _wrap_subtitle_text(text: str, max_chars_per_line: int = 16) -> str:
    text = re.sub(r"\s+", "", text.strip())
    if len(text) <= max_chars_per_line:
        return text

    split_at = min(max_chars_per_line, len(text))
    first = text[:split_at].rstrip(TRAILING_SUBTITLE_PUNCTUATION)
    second = text[split_at:].lstrip("，,；;：:").rstrip(TRAILING_SUBTITLE_PUNCTUATION)
    if len(second) > max_chars_per_line:
        second = second[:max_chars_per_line].rstrip(TRAILING_SUBTITLE_PUNCTUATION)
    return f"{first}\\N{second}"


def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\\\\N", "\\N")


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _format_ass_time(seconds: float) -> str:
    cs = int((seconds - int(seconds)) * 100)
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _create_digital_presenter_scene(photo_path: str | None) -> str:
    """
    Create a vertical digital presenter scene.
    If the user uploaded a photo, it becomes the presenter's portrait; otherwise a generated avatar is used.
    """
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    import numpy as np

    width, height = settings.VIDEO_WIDTH, settings.VIDEO_HEIGHT
    photo_mtime = int(os.path.getmtime(photo_path)) if photo_path and os.path.exists(photo_path) else 0
    scene_key = f"digital_presenter_{abs(hash((photo_path, photo_mtime, width, height)))}.png"
    scene_path = str(settings.USER_DIR / scene_key)
    if os.path.exists(scene_path):
        return scene_path

    bg = _studio_background(width, height)
    draw = ImageDraw.Draw(bg, "RGBA")

    draw.rounded_rectangle((70, 110, width - 70, height - 370), radius=54, fill=(255, 255, 255, 22), outline=(255, 255, 255, 55), width=2)
    draw.ellipse((width - 260, 160, width - 80, 340), fill=(85, 170, 255, 45))
    draw.ellipse((80, 760, 260, 940), fill=(154, 105, 255, 35))

    presenter_box = (150, 280, width - 150, 1320)
    if photo_path and os.path.exists(photo_path):
        _paste_photo_presenter(bg, photo_path, presenter_box)
    else:
        _draw_generated_presenter(bg, presenter_box)

    draw = ImageDraw.Draw(bg, "RGBA")
    draw.rounded_rectangle((250, 1380, width - 250, 1472), radius=46, fill=(0, 0, 0, 115), outline=(255, 255, 255, 45), width=2)
    for i, bar_h in enumerate([24, 42, 66, 48, 78, 38, 58, 34, 68, 46, 28]):
        x = 320 + i * 40
        y = 1426 - bar_h // 2
        draw.rounded_rectangle((x, y, x + 18, y + bar_h), radius=8, fill=(255, 255, 255, 210))

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
    except Exception:
        font = None
    draw.text((width // 2, 1528), "AI DIGITAL PRESENTER", fill=(255, 255, 255, 125), anchor="mm", font=font)

    bg.save(scene_path)
    return scene_path


def _studio_background(width: int, height: int):
    from PIL import Image, ImageDraw, ImageFilter
    import numpy as np

    img_array = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        ratio = y / height
        r = int(14 + 34 * ratio)
        g = int(18 + 28 * ratio)
        b = int(48 + 78 * ratio)
        img_array[y, :] = [r, g, b]
    bg = Image.fromarray(img_array, "RGB")

    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    glow_draw.ellipse((-240, 150, 520, 910), fill=(63, 154, 255, 90))
    glow_draw.ellipse((620, 60, 1350, 780), fill=(190, 95, 255, 70))
    glow_draw.ellipse((160, 980, 940, 1780), fill=(49, 220, 190, 42))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    return Image.alpha_composite(bg.convert("RGBA"), glow).convert("RGB")


def _paste_photo_presenter(canvas, photo_path: str, box: tuple[int, int, int, int]):
    from PIL import Image, ImageDraw, ImageFilter

    x1, y1, x2, y2 = box
    target_w = x2 - x1
    target_h = y2 - y1
    photo = Image.open(photo_path).convert("RGB")
    photo_ratio = photo.width / photo.height
    target_ratio = target_w / target_h
    if photo_ratio > target_ratio:
        new_h = target_h
        new_w = int(new_h * photo_ratio)
    else:
        new_w = target_w
        new_h = int(new_w / photo_ratio)
    photo = photo.resize((new_w, new_h), Image.LANCZOS)
    left = max((new_w - target_w) // 2, 0)
    top = max((new_h - target_h) // 2, 0)
    photo = photo.crop((left, top, left + target_w, top + target_h))

    mask = Image.new("L", (target_w, target_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, target_w, target_h), radius=70, fill=255)

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow, "RGBA")
    shadow_draw.rounded_rectangle((x1 + 18, y1 + 26, x2 + 18, y2 + 26), radius=70, fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(26))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), shadow).convert("RGB"))
    canvas.paste(photo, (x1, y1), mask)

    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle((x1, y1, x2, y2), radius=70, outline=(255, 255, 255, 120), width=4)


def _draw_generated_presenter(canvas, box: tuple[int, int, int, int]):
    from PIL import ImageDraw

    x1, y1, x2, y2 = box
    draw = ImageDraw.Draw(canvas, "RGBA")
    cx = (x1 + x2) // 2

    draw.ellipse((cx - 190, y1 + 40, cx + 190, y1 + 420), fill=(250, 210, 178, 255))
    draw.pieslice((cx - 215, y1 + 10, cx + 215, y1 + 270), 180, 360, fill=(45, 38, 48, 255))
    draw.ellipse((cx - 96, y1 + 205, cx - 54, y1 + 247), fill=(30, 35, 48, 255))
    draw.ellipse((cx + 54, y1 + 205, cx + 96, y1 + 247), fill=(30, 35, 48, 255))
    draw.rounded_rectangle((cx - 62, y1 + 318, cx + 62, y1 + 342), radius=12, fill=(166, 58, 72, 255))

    draw.polygon([(cx - 260, y1 + 500), (cx + 260, y1 + 500), (cx + 360, y2), (cx - 360, y2)], fill=(38, 88, 160, 255))
    draw.polygon([(cx - 92, y1 + 500), (cx + 92, y1 + 500), (cx + 54, y1 + 710), (cx - 54, y1 + 710)], fill=(245, 245, 248, 255))
    draw.polygon([(cx - 24, y1 + 510), (cx + 24, y1 + 510), (cx + 42, y1 + 740), (cx, y1 + 805), (cx - 42, y1 + 740)], fill=(86, 192, 255, 255))
    draw.rounded_rectangle((x1, y1, x2, y2), radius=70, outline=(255, 255, 255, 105), width=4)
