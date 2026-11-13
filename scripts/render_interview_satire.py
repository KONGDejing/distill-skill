#!/usr/bin/env python3
"""Render an explicitly fictional, two-voice animated interview for review.

No account operation, public-figure voice cloning or publication occurs here.
The authored story, sources, audio timings and final media stay in storage.
"""
import argparse
import asyncio
import base64
import json
import math
from pathlib import Path
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from services.factory_media import _duration, _edge_speech
from services.factory_resources import ensure_memory_available

VOICES = {'hr': ('zh-CN-XiaoxiaoNeural', '-3%', '-3Hz'),
          'huang': ('zh-CN-YunyangNeural', '-6%', '-2Hz'),
          'narrator': ('zh-CN-YunjianNeural', '+3%', '+0Hz')}
FPS = 24


def run(args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


async def voices(story, directory):
    limit = asyncio.Semaphore(2)

    async def synthesize(index, line):
        audio = directory / f'turn-{index:02}.mp3'
        cache = audio.with_suffix('.json')
        voice, rate, pitch = VOICES[line['speaker']]
        signature = [line['text'], voice, rate, pitch]
        async with limit:
            if audio.is_file() and cache.is_file():
                saved = json.loads(cache.read_text())
                if saved['signature'] == signature:
                    return saved
            for attempt in range(3):
                try:
                    cues = await _edge_speech(line['text'], voice, audio, rate=rate, pitch=pitch)
                    result = {'signature': signature, 'cues': cues, 'duration': _duration(audio)}
                    cache.write_text(json.dumps(result, ensure_ascii=False))
                    print(f'Voice {index + 1}/{len(story["dialogue"])} ready', flush=True)
                    return result
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1 + attempt)

    return await asyncio.gather(*(synthesize(index, line) for index, line in enumerate(story['dialogue'])))


def make_timeline(story, speech, directory):
    import numpy as np
    parts, timeline, cursor = [], [], 0.0
    for index, (line, voice) in enumerate(zip(story['dialogue'], speech)):
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(directory / f'turn-{index:02}.mp3'),
                                      '-ac', '1', '-ar', '24000', '-f', 's16le', '-'])
        samples = np.frombuffer(raw, dtype='<i2').copy()
        duration = len(samples) / 24000
        pause = .65 if line['shot'] in {'stamp', 'ending'} else .32
        timeline.append({**line, 'start': cursor, 'end': cursor + duration,
                         'cut_end': cursor + duration + pause,
                         'cues': [{**c, 'start': c['start'] + cursor, 'end': c['end'] + cursor} for c in voice['cues']]})
        parts.extend((samples, np.zeros(round(pause * 24000), dtype=np.int16)))
        cursor += duration + pause
    samples = np.concatenate(parts)
    # Small original UI ticks punctuate rejected template fields, below speech.
    for turn in timeline:
        if turn['shot'] not in {'screen', 'stamp'}:
            continue
        start = round((turn['start'] + .1) * 24000)
        length = 1800
        t = np.arange(length) / 24000
        tick = np.sin(t * math.tau * (310 if turn['shot'] == 'stamp' else 840)) * np.exp(-t * 65) * 1000
        samples[start:start + length] = np.clip(samples[start:start + length].astype(np.int32) + tick, -32768, 32767).astype(np.int16)
    with wave.open(str(directory / 'dialogue.wav'), 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(24000)
        output.writeframes(samples.tobytes())
    envelope = []
    for offset in range(0, len(samples), 1000):
        chunk = samples[offset:offset + 1000].astype(np.float32)
        envelope.append(min(1, float(np.sqrt(np.mean(chunk * chunk))) / 4300))
    data = {**story, 'timeline': timeline, 'envelope': envelope, 'duration': cursor}
    (directory / 'timeline.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return data


def render(data, directory):
    from playwright.sync_api import sync_playwright
    template = ROOT / 'backend/templates/interview_satire.html'
    html = template.read_text().replace('__STORY_JSON__', json.dumps(data, ensure_ascii=False).replace('</', '<\\/'))
    preview = directory / 'preview.html'
    preview.write_text(html)
    target = directory / (data['title'] + '.mp4')
    temporary = target.with_suffix('.tmp.mp4')
    command = ['ffmpeg', '-y', '-v', 'error', '-threads', '2', '-f', 'image2pipe', '-framerate', str(FPS),
               '-vcodec', 'mjpeg', '-i', '-', '-i', str(directory / 'dialogue.wav'),
               '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
               '-pix_fmt', 'yuv420p', '-threads', '2', '-c:a', 'aac', '-b:a', '192k',
               '-af', 'loudnorm=I=-16:TP=-1.5:LRA=9', '-shortest', '-movflags', '+faststart', str(temporary)]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=False,
            args=['--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--no-proxy-server'])
        try:
            page = browser.new_page(viewport={'width': 1080, 'height': 1920}, device_scale_factor=1)
            page.goto(preview.as_uri())
            page.evaluate('document.fonts.ready')
            with (directory / 'render-errors.log').open('w') as errors:
                encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors)
                try:
                    frames = math.ceil(data['duration'] * FPS)
                    for index in range(frames):
                        if index % (FPS * 10) == 0:
                            ensure_memory_available()
                            print(f'Animation {index / FPS:.0f}/{data["duration"]:.1f}s', flush=True)
                        frame = page.evaluate('(t)=>renderFrame(t)', index / FPS)
                        encoder.stdin.write(base64.b64decode(frame))
                    encoder.stdin.close()
                    if encoder.wait(timeout=120):
                        raise RuntimeError('Animation encoding failed; see private render log')
                finally:
                    if encoder.poll() is None:
                        encoder.kill(); encoder.wait()
        finally:
            browser.close()
    temporary.replace(target)
    run(['ffmpeg', '-y', '-v', 'error', '-i', str(target), '-frames:v', '1', str(directory / '封面.png')])
    print(f'Video ready: {target.name} ({_duration(target):.2f}s)', flush=True)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('story', type=Path)
    parser.add_argument('--audio-only', action='store_true')
    args = parser.parse_args()
    story = json.loads(args.story.read_text())
    if not story.get('fiction_notice') or any(t['speaker'] not in VOICES for t in story['dialogue']):
        raise ValueError('Explicit fiction notice and supported non-cloned voices are required')
    directory = args.story.resolve().parent
    ensure_memory_available()
    speech = asyncio.run(voices(story, directory))
    data = make_timeline(story, speech, directory)
    print(f'Dialogue timeline: {data["duration"]:.2f}s', flush=True)
    if not args.audio_only:
        render(data, directory)


if __name__ == '__main__':
    main()
