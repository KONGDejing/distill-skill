"""Timestamped real-video contact sheets for honest sampled visual analysis."""
from pathlib import Path
import io
import json
import subprocess

from PIL import Image,ImageDraw
from services.factory_media import _font


def sample_reference(video_path, target):
    source=Path(video_path);target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    result=subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(source)],capture_output=True,text=True,timeout=20,check=True)
    metadata=json.loads(result.stdout);duration=float(metadata['format']['duration'])
    stream=next(s for s in metadata['streams'] if s.get('codec_type')=='video')
    times=[.05,min(2,duration/4),min(7,duration/3),duration*.33,duration*.6,max(.1,duration-3)]
    canvas=Image.new('RGB',(1080,355),'#111111');draw=ImageDraw.Draw(canvas)
    for index,at in enumerate(times):
        frame=subprocess.run(['ffmpeg','-v','error','-threads','1','-ss',str(at),'-i',str(source),'-frames:v','1',
            '-vf','scale=170:302:force_original_aspect_ratio=decrease,pad=170:302:(ow-iw)/2:(oh-ih)/2',
            '-f','image2pipe','-vcodec','mjpeg','-'],capture_output=True,timeout=20,check=True)
        canvas.paste(Image.open(io.BytesIO(frame.stdout)),(index*180,35))
        draw.text((index*180+4,8),f'{at:.2f}秒',font=_font(16),fill='white')
    canvas.save(target)
    return {'image_path':str(target),'duration_seconds':duration,'width':stream['width'],'height':stream['height'],
            'sample_times_seconds':times,'scope':'six_sampled_frames_and_transcript; not_full_motion_or_audio_review'}
