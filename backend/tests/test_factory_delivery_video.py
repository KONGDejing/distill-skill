from pathlib import Path
import hashlib
import subprocess

from PIL import Image
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
