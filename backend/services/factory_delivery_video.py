"""Finish an uploadable MP4 with its lettered opening, preserving voice timing."""
from pathlib import Path
import os

from services.factory_media import _probe, _run
from services.factory_resources import ensure_memory_available


def render_delivery_video(source, cover, target):
    ensure_memory_available()
    source, cover, target = Path(source), Path(cover), Path(target)
    info = _probe(source)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    if not any(s['codec_type'] == 'audio' for s in info['streams']):
        raise ValueError('完整成片必须包含口播音轨')
    duration = float(info['format']['duration'])
    # The opening appears while the existing hook is spoken. No silence is
    # prepended and all narration/subtitle timestamps remain unchanged.
    opening_seconds = min(0.9, duration / 2)
    width, height = video['width'], video['height']
    temp = target.with_name(target.stem + '.tmp.mp4')
    try:
        _run(['ffmpeg', '-y', '-v', 'error', '-threads', '2', '-i', str(source),
              '-loop', '1', '-framerate', '24', '-i', str(cover), '-filter_complex_threads', '1',
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
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return {'cover_integrated': True, 'opening_seconds': opening_seconds, 'audio_preserved': True,
            'duration': float(output['format']['duration'])}
