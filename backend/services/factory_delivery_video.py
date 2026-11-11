"""Finish an uploadable MP4 with its lettered opening, preserving voice timing."""
from pathlib import Path
import os

from services.factory_media import _probe, _run
from services.factory_resources import ensure_memory_available


def render_delivery_video(source, cover, target, *, opening_mode="brand_card", title_overlay=None):
    ensure_memory_available()
    source, cover, target = Path(source), Path(cover), Path(target)
    info = _probe(source)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    if not any(s['codec_type'] == 'audio' for s in info['streams']):
        raise ValueError('完整成片必须包含口播音轨')
    duration = float(info['format']['duration'])
    # The opening appears while the existing hook is spoken. No silence is
    # prepended and all narration/subtitle timestamps remain unchanged.
    if opening_mode not in {"brand_card", "first_frame_title"}:
        raise ValueError("未知视频开场模式")
    if opening_mode == "first_frame_title" and not title_overlay:
        raise ValueError("真实首帧开场必须提供透明标题层")
    opening_seconds = min(2.4 if opening_mode == "first_frame_title" else 0.9, duration / 2)
    layer = Path(title_overlay) if opening_mode == "first_frame_title" else cover
    if opening_mode == "first_frame_title":
        from PIL import Image
        with Image.open(layer) as image:
            if image.mode != "RGBA" or image.getchannel("A").getextrema()[0] != 0:
                raise ValueError("首帧标题层必须保留透明区域，不能替换真实画面")
    width, height = video['width'], video['height']
    temp = target.with_name(target.stem + '.tmp.mp4')
    try:
        _run(['ffmpeg', '-y', '-v', 'error', '-threads', '2', '-i', str(source),
              '-loop', '1', '-framerate', '24', '-i', str(layer), '-filter_complex_threads', '1',
              '-filter_complex', f'[1:v]scale={width}:{height},setsar=1[c];[0:v][c]overlay=enable=\'lt(t,{opening_seconds})\':shortest=1[v]',
              '-map', '[v]', '-map', '0:a:0', '-t', str(duration), '-c:v', 'libx264', '-preset', 'veryfast',
              '-crf', '20', '-pix_fmt', 'yuv420p', '-threads', '2', '-c:a', 'copy', '-movflags', '+faststart', str(temp)], timeout=1200)
        output = _probe(temp)
        if abs(float(output['format']['duration']) - duration) > .2:
            raise ValueError('完整成片时长发生异常变化')
        def audio_hash(path):
            return _run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0', '-c:a', 'copy',
                         '-f', 'hash', '-hash', 'sha256', '-']).stdout.strip()
        if audio_hash(source) != audio_hash(temp):
            raise ValueError('完整成片音轨与已审核口播不一致')
        if opening_mode == "first_frame_title":
            # The downloadable poster is the decoded first frame of this MP4.
            _run(['ffmpeg', '-y', '-v', 'error', '-i', str(temp), '-frames:v', '1', str(cover)])
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return {'cover_integrated': True, 'opening_mode': opening_mode,
            'source_motion_preserved': opening_mode == 'first_frame_title',
            'opening_seconds': opening_seconds, 'audio_preserved': True,
            'duration': float(output['format']['duration'])}
