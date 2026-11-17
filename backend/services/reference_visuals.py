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


def sample_factory_comparison(db, target):
    """Compare the latest completed daily work, one film per non-paper track."""
    from models.factory import FactoryItem, FactoryRun
    from config import settings
    from services.video_downloader import valid_video

    rows = db.query(FactoryItem, FactoryRun).join(FactoryRun, FactoryRun.id == FactoryItem.run_id).filter(
        FactoryItem.status == 'completed').order_by(FactoryRun.created_at.desc(), FactoryItem.position).limit(36).all()
    selected_run, selected, pictures, measurements = None, {}, [], []
    for item, run in rows:
        if run.settings_snapshot.get('production_kind') in {'weekly_papers', 'superseded_daily'}:
            continue
        owner = item.content.get('editor_id') or item.brief.get('editor_id')
        if owner not in {'producer', 'producer3'}:
            continue
        package = next((p for p in item.packages or [] if p.get('platform') == 'douyin'), None)
        if not package or not package.get('video_path'):
            continue
        source = Path(package['video_path']).resolve()
        if not source.is_relative_to(settings.STORAGE_DIR.resolve()) or not valid_video(source):
            continue
        if selected_run is None:
            selected_run = run.id
        if run.id != selected_run:
            break
        if owner not in selected or item.content.get('presentation_style') == 'science_editorial_focus_v2':
            selected[owner] = (item, source)
    for owner in ('producer', 'producer3'):
        if owner not in selected:
            continue
        item, source = selected[owner]
        try:
            sampled = sample_reference(source, Path(target) / (item.id + '.jpg'))
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        pictures.append(sampled.pop('image_path'))
        measurements.append({**sampled, 'sample_role': 'factory_film', 'item_id': item.id,
            'editor_id': owner, 'title': item.content.get('title'),
            'approved_script': str(item.content.get('script') or '')[:8000],
            'scope': '本工厂实际抖音交付成片的六点抽帧和已审核口播；不是完整运动或音频试听。'})
    if len(pictures) > 1:
        # Keep the model's existing four-attachment bound: three reference
        # sheets plus one labelled factory sheet, with both tracks retained.
        sheets = [Image.open(path).convert('RGB') for path in pictures]
        width = max(sheet.width for sheet in sheets)
        canvas = Image.new('RGB', (width, sum(sheet.height + 44 for sheet in sheets)), '#111111')
        draw, top = ImageDraw.Draw(canvas), 0
        for index, (sheet, metadata) in enumerate(zip(sheets, measurements)):
            draw.text((12, top + 8), '本厂成片：' + str(metadata['title'])[:35], font=_font(20), fill='white')
            canvas.paste(sheet, (0, top + 44))
            metadata['image_panel'] = '上半部分' if index == 0 else '下半部分'
            top += sheet.height + 44
            sheet.close()
        combined = Path(target) / 'factory-comparison-combined.jpg'
        canvas.save(combined)
        pictures = [str(combined)]
    return pictures, measurements
