from pathlib import Path
import hashlib
import subprocess

from PIL import Image
import pytest
from services import factory_delivery_video as delivery


def test_cover_is_in_opening_and_voice_timing_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, 'ensure_memory_available', lambda: None)
    source, cover, target = (tmp_path / f for f in ('source.mp4', 'cover.png', 'complete.mp4'))
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=160x284:r=24:d=2',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:v', 'libx264', '-threads', '1',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(source)], check=True)
    Image.new('RGB', (160, 284), (0, 255, 0)).save(cover)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    quality = delivery.render_delivery_video(source, cover, target)
    assert quality['cover_integrated'] and quality['audio_preserved']
    assert abs(quality['duration'] - 2) <= .2
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    def pixel(at):
        return subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(target),
                                        '-vf', 'scale=1:1', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    first, later = pixel(.1), pixel(1.2)
    assert first[1] > 200 and first[2] < 40
    assert later[2] > 200 and later[1] < 40


@pytest.mark.parametrize('platform', ['xiaohongshu', 'kuaishou'])
def test_real_first_frame_title_keeps_moving_footage_and_audio(tmp_path, monkeypatch, platform):
    from services.factory_cover import render_frame_cover
    import numpy as np

    monkeypatch.setattr(delivery, 'ensure_memory_available', lambda: None)
    source, cover, target = (tmp_path / f for f in ('source.mp4', 'cover.png', 'complete.mp4'))
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=270x480:r=24:d=6',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6', '-c:v', 'libx264', '-threads', '1',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(source)], check=True)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    title = 'AI断网能说话？三步判断离线边界'
    design = render_frame_cover(source, title, cover, platform)
    quality = delivery.render_delivery_video(source, cover, target, opening_mode='first_frame_title',
                                            title_overlay=design['overlay_path'])
    assert quality['source_motion_preserved'] and quality['audio_preserved']
    assert abs(quality['duration'] - 6) < .2
    assert design['text_rendered'] == title
    assert design['lines'] == ['AI断网能说话？', '三步判断离线边界']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before

    def frame(path, at):
        data = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-ss', str(at),
                                        '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
        return np.frombuffer(data, dtype=np.uint8).reshape(480, 270, 3).astype(float)
    first = frame(target, 0)
    assert np.array_equal(np.asarray(Image.open(cover)), first)
    # The headline changes only the lower band. Content moves throughout the
    # opening, and is still the corresponding source frame, not a frozen poster.
    for at in (0, .5, 1.5):
        original, output = frame(source, at), frame(target, at)
        assert abs(original[:395] - output[:395]).mean() < 5
        assert abs(original[410:460] - output[410:460]).mean() > 5
    assert abs(frame(target, .5)[:395] - first[:395]).mean() > 5
    assert abs(frame(source, 3) - frame(target, 3)).mean() < 5
