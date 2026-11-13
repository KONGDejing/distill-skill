#!/usr/bin/env python3
"""Render disclosed fictional interviews using a separately installed SadTalker.

Run one actor worker per GPU, then compose. Models and generated media stay out
of version control. The supplied timeline and audio must already be reviewed.
"""
import argparse
import gc
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from services.factory_resources import ensure_memory_available


def run(command):
    subprocess.run([str(x) for x in command], check=True)


def validate_timeline(story):
    cursor = 0.0
    if '虚构' not in story.get('fiction_notice', ''):
        raise ValueError('Explicit fiction disclosure required')
    turns = story.get('dialogue')
    if not isinstance(turns, list) or not turns:
        raise ValueError('Dialogue cannot be empty')
    for turn in turns:
        if (turn.get('speaker') not in {'huang', 'hr', 'narrator'}
                or not isinstance(turn.get('text'), str) or not turn['text'].strip()
                or not turn.get('headline')):
            raise ValueError('Each turn requires a supported actor, text and headline')
        if abs(turn['start'] - cursor) > .005 or not cursor < turn['end'] <= turn['cut_end']:
            raise ValueError('Dialogue timeline has a gap, overlap or invalid speech interval')
        if turn['speaker'] != 'narrator' and turn['cut_end'] - cursor > 12:
            raise ValueError('Split long utterances before actor rendering')
        cursor = turn['cut_end']
    if abs(cursor - story['duration']) > .005:
        raise ValueError('Timeline duration does not match its last turn')


def actor(args, story):
    engine = args.engine.resolve()
    sys.path.insert(0, str(engine)); os.chdir(engine)
    import torch
    from src.utils.preprocess import CropAndExtract
    from src.test_audio2coeff import Audio2Coeff
    from src.facerender.animate import AnimateFromCoeff
    from src.generate_batch import get_data
    from src.generate_facerender_batch import get_facerender_data
    from src.utils.init_path import init_path
    torch.set_num_threads(4)
    directory = args.directory.resolve()
    work = directory / 'talking-shots' / args.actor; work.mkdir(parents=True, exist_ok=True)
    portrait = directory / ('老黄正反打特写.png' if args.actor == 'huang' else 'HR正反打特写.png')
    paths = init_path(str(engine / 'checkpoints'), str(engine / 'src/config'), 512, False, 'full')
    preprocess = CropAndExtract(paths, 'cuda')
    audio_model = Audio2Coeff(paths, 'cuda')
    animator = AnimateFromCoeff(paths, 'cuda')
    frame_dir = work / 'portrait';frame_dir.mkdir(exist_ok=True)
    first, crop, info = preprocess.generate(str(portrait), str(frame_dir), 'full', source_image_flag=True, pic_size=512)
    if first is None: raise RuntimeError('No usable face detected')
    del preprocess;gc.collect();torch.cuda.empty_cache()
    for index, turn in enumerate(story['dialogue']):
        if turn['speaker'] != args.actor: continue
        ensure_memory_available()
        target = work / f'turn-{index:02}.mp4'
        if target.is_file(): continue
        segment = work / f'turn-{index:02}'; segment.mkdir(exist_ok=True)
        audio = segment / 'speech.wav'
        duration = turn['cut_end'] - turn['start']
        if duration > 12: raise ValueError('Split long utterances before actor rendering')
        run(['ffmpeg','-y','-v','error','-ss',str(turn['start']),'-i',directory / '写实版完整对话配音.wav',
             '-t',str(duration),'-ac','1','-ar','16000',audio])
        batch = get_data(first, str(audio), 'cuda', None, still=False)
        coefficients = audio_model.generate(batch, str(segment), index % 4, None)
        data = get_facerender_data(coefficients, crop, first, str(audio), 2, None, None, None,
                                  expression_scale=1., still_mode=False, preprocess='full', size=512)
        result = animator.generate(data, str(segment), str(portrait), info, preprocess='full', img_size=512)
        Path(result).replace(target)
        del batch, data;gc.collect();torch.cuda.empty_cache()
        print('ACTOR_TURN_READY',args.actor,index,flush=True)


def ass_time(value):
    cs=round(value*100);return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'


def captions(story, target):
    head='''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Subtitle,Noto Sans CJK SC,51,&H00FFFFFF,&H00FFFFFF,&H00201A13,&H90000000,1,0,0,0,100,100,1,0,1,3,1,2,80,80,360,1
Style: Label,Noto Sans CJK SC,31,&H00FFFFFF,&H00FFFFFF,&H00201A13,&H90000000,0,0,0,0,100,100,0,0,3,1,0,7,58,58,100,1
Style: Heading,Noto Sans CJK SC,65,&H00EFFFFF,&H00FFFFFF,&H00201A13,&H90000000,1,0,0,0,100,100,2,0,1,3,1,8,65,65,310,1
[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
'''
    events=[f"Dialogue: 2,0:00:00.00,{ass_time(story['duration'])},Label,,0,0,0,,AI声像模拟·虚构面试 · 非真实招聘"]
    for index, turn in enumerate(story['dialogue']):
        text=turn['text'].replace('{','').replace('}','')
        pieces=[text[n:n+18] for n in range(0,len(text),18)]
        for n,piece in enumerate(pieces):
            start=turn['start']+(turn['end']-turn['start'])*n/len(pieces)
            end=turn['start']+(turn['end']-turn['start'])*(n+1)/len(pieces)
            events.append(f'Dialogue: 1,{ass_time(start)},{ass_time(end)},Subtitle,,0,0,0,,{piece}')
        title='假如黄仁勋来面试\\N简历先被模板刷掉？' if index==0 else turn['headline']
        events.append(f"Dialogue: 1,{ass_time(turn['start'])},{ass_time(turn['cut_end'])},Heading,,0,0,0,,{title}")
    target.write_text(head+'\n'.join(events)+'\n')


def compose(args, story):
    directory=args.directory.resolve();output=directory/'假如黄仁勋来面试，简历先被模板刷掉？.mp4'
    shotdir=directory/'composed-shots';shotdir.mkdir(exist_ok=True)
    listing=[]
    for index, turn in enumerate(story['dialogue']):
        ensure_memory_available()
        duration=turn['cut_end']-turn['start'];frames=round(turn['cut_end']*25)-round(turn['start']*25)
        target=shotdir/f'turn-{index:02}.mp4'
        if not target.is_file():
            if turn['speaker'] in ('hr','huang'):
                source=directory/'talking-shots'/turn['speaker']/f'turn-{index:02}.mp4'
                deadline = time.monotonic() + 1800
                while not source.is_file() and args.wait_for_shots and time.monotonic() < deadline:
                    ensure_memory_available()
                    print('WAITING_FOR_ACTOR_TURN', turn['speaker'], index, flush=True)
                    time.sleep(10)
                if not source.is_file():raise FileNotFoundError(source)
                inputs=['-i',source]
                filters='scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=25'
            else:
                source=directory/'写实角色定妆图.png'
                inputs=['-loop','1','-framerate','25','-i',source]
                filters=f"scale=1120:1980:force_original_aspect_ratio=increase,crop=1120:1980,zoompan=z='1+0.00008*on':x='20+6*sin(on/80)':y='30':d=1:s=1080x1920:fps=25"
            run(['ffmpeg','-y','-v','error',*inputs,'-vf',filters,'-frames:v',str(frames),'-an',
                 '-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p','-threads','2',target])
        listing.append("file '"+str(target).replace("'", "'\\''")+"'")
    concat=directory/'talking-shots.txt';concat.write_text('\n'.join(listing))
    subtitles=directory/'写实版字幕.ass';captions(story,subtitles)
    run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',concat,'-i',directory/'写实版完整对话配音.wav',
         '-vf',f'ass={subtitles}','-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','fast','-crf','19',
         '-threads','2','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-af','loudnorm=I=-16:TP=-1.5:LRA=9',
         '-t',str(story['duration']),'-movflags','+faststart',output])
    run(['ffmpeg','-y','-v','error','-i',output,'-frames:v','1',directory/'写实版视频首帧.png'])
    print('REALISTIC_VIDEO_READY',output,flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path)
    p.add_argument('--engine',type=Path,default=ROOT/'backend/engines/SadTalker')
    p.add_argument('--actor',choices=['hr','huang']);p.add_argument('--compose',action='store_true');p.add_argument('--wait-for-shots',action='store_true');args=p.parse_args()
    args.directory=args.directory.resolve()
    story=json.loads((args.directory/'写实版对话时间线.json').read_text())
    validate_timeline(story)
    if args.actor:actor(args,story)
    elif args.compose:compose(args,story)
    else:p.error('Choose --actor or --compose')

if __name__=='__main__':main()
