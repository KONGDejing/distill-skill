"""Private, verified owner attribution. No competitor identity is a default."""
import hashlib
import json
from pathlib import Path
import re

BRANDING_FILE = Path(__file__).resolve().parents[2] / '.runtime/factory-branding.json'


def branding_for(platform):
    if platform != 'douyin':
        return {}
    try:
        value=json.loads(BRANDING_FILE.read_text())
    except (OSError,ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    name=value.get('display_name');handle=value.get('handle')
    if (value.get('platform')!='douyin' or value.get('verification_source')!='official_creator_home'
            or not isinstance(name,str) or not 1<=len(name)<=60
            or any(ord(char) < 32 for char in name)
            or not isinstance(handle,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,32}',handle)):
        return {}
    identity={'display_name':name,'handle':handle,'platform':'douyin'}
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {**identity,'signature':digest,'label':'制作：@'+name,'account_label':'抖音号：'+handle}


def write_branding_subtitle(brand,duration,target,*,start=0,position_y=700):
    if not 0 <= position_y <= 1830:
        raise ValueError('署名位置超出画面')
    def escape(text):
        return str(text).replace('\\','＼').replace('{','｛').replace('}','｝').replace('\n',' ')
    def stamp(value):
        centiseconds = round(value * 100)
        whole, fraction = divmod(centiseconds, 100)
        return f'{whole//3600}:{whole//60%60:02d}:{whole%60:02d}.{fraction:02d}'
    lines=['[Script Info]','ScriptType: v4.00+','PlayResX: 1080','PlayResY: 1920','',
           '[V4+ Styles]','Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
           f'Style: Brand,Noto Sans CJK SC,28,&H30FFFFFF,&H000000FF,&H40202020,&H00000000,1,0,0,0,100,100,0,0,1,1,0,7,60,70,{position_y},1','',
           '[Events]','Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    text=escape(brand['label'])+r'\N{\fs23}'+escape(brand['account_label'])
    lines.append(f'Dialogue: 0,{stamp(start)},{stamp(duration)},Brand,,0,0,0,,{text}')
    Path(target).write_text('\n'.join(lines),encoding='utf-8')
    return {'source_attribution_integrated':True,'branding_signature':brand['signature'],
            'owner_display_name':brand['display_name'],'owner_handle':brand['handle'],
            'attribution_scope':'our_script_voice_and_editing; original_footage_sources_remain_credited'}


def require_owner_attribution(variant):
    brand = branding_for('douyin')
    quality = variant.get('delivery_quality') or {}
    if not brand:
        raise ValueError('抖音账号署名尚未从官方后台核验，未提交')
    if (quality.get('source_attribution_integrated') is not True
            or quality.get('branding_signature') != brand['signature']):
        raise ValueError('抖音成片尚未烧录当前账号来源署名，请先重新打包，未提交')
    # Bind the finishing evidence to the actual final upload, not merely flags.
    path = Path(variant.get('video_path') or '')
    expected = quality.get('delivery_sha256')
    if not path.is_file() or not expected or file_sha256(path) != expected:
        raise ValueError('抖音署名成片已变化或验收证据缺失，未提交')


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
