"""Import real public reference videos; normalize share text and validate media.

A screenshot is never counted as a downloaded video. Browser fallback reads the
ordinary player in a separate tab, preserving any active publishing form.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

from config import settings

MAX_VIDEO_BYTES = 150_000_000


def normalize_video_url(value):
    if not isinstance(value, str) or len(value) > 20000:
        raise ValueError('请粘贴一个有效的视频链接或平台分享文案')
    match = re.search(r'https?://[^\s<>\u3000]+', value.strip())
    if not match:
        raise ValueError('分享内容中没有找到http或https视频链接')
    url = match.group(0).rstrip('.,;!，。；！？、)]}）】>"\'')
    parts = urlsplit(url)
    host = parts.hostname or ''
    if (parts.scheme not in {'http','https'} or not host or parts.username or parts.password
            or host.lower() in {'localhost','localhost.localdomain'} or '.' not in host
            or parts.port not in {None,80,443} or len(url) > 2048):
        raise ValueError('只支持公开网站的视频链接，不支持本机地址、账号密码或特殊端口')
    try:
        address = ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError('不支持内网或本机视频地址')
    return url


def valid_video(path):
    try:
        file = Path(path)
        if not file.is_file() or not 0 < file.stat().st_size <= MAX_VIDEO_BYTES:
            return False
        result = subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(file)],
                                capture_output=True,text=True,timeout=20)
        value = json.loads(result.stdout)
        kinds = {stream.get('codec_type') for stream in value.get('streams',[])}
        return result.returncode == 0 and {'video','audio'} <= kinds and float(value['format']['duration']) > 1
    except (OSError,ValueError,TypeError,KeyError,subprocess.TimeoutExpired):
        return False


def download_video(url, output_dir, video_id):
    try:
        url = normalize_video_url(url)
    except ValueError as error:
        return None,str(error)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', video_id):
        return None,'视频编号无效'
    directory=Path(output_dir);directory.mkdir(parents=True,exist_ok=True)
    output=directory/(video_id+'.mp4')
    if valid_video(output):
        return str(output),None
    first=_download_with_ytdlp(url,str(output))
    if first is None and valid_video(output):
        return str(output),None
    second=_download_with_playwright(url,str(output))
    if second is None and valid_video(output):
        return str(output),None
    return None,f'视频导入未完成。下载工具：{first or "未取得有效音视频"}；浏览器：{second or "未取得有效音视频"}'


def _download_with_ytdlp(url, output_path):
    try:
        command=[sys.executable,'-m','yt_dlp','--ignore-config','--no-playlist','--no-progress',
                 '-f','best[ext=mp4]/best','--remux-video','mp4','-o',output_path,
                 '--max-filesize',str(MAX_VIDEO_BYTES),'--socket-timeout','12','--retries','1',
                 '--print','after_move:%(title)j',url]
        result=subprocess.run(command,capture_output=True,text=True,timeout=90)
        if result.returncode == 0 and valid_video(output_path):
            try:
                title=json.loads(result.stdout.strip().splitlines()[-1])
                Path(output_path).with_suffix('.metadata.json').write_text(json.dumps({'title':title,'source_url':url,'import_method':'yt_dlp'},ensure_ascii=False))
            except (ValueError,IndexError):
                pass
            return None
        error=(result.stderr or '').lower()
        if 'no module named yt_dlp' in error:
            return '当前Python环境未安装yt-dlp'
        if any(term in error for term in ['cookie','login','sign in','captcha','verify']):
            return '平台要求正常登录或验证，公开链接下载未成功'
        return '平台未提供可解析的视频流'
    except subprocess.TimeoutExpired:
        return '连接平台超时'
    except OSError:
        return '下载进程无法启动'


def _read_complete_media(request, url):
    """Join ordinary HTTP byte ranges; reject gaps, oversized or repeated chunks."""
    deadline = time.monotonic() + 60
    data = bytearray(); total = None
    for _ in range(32):
        if time.monotonic() >= deadline:
            break
        response = request.get(url, timeout=20000, headers={'Range': f'bytes={len(data)}-'})
        if response.status not in {200,206}:
            return None
        block = response.body()
        if not block or len(data) + len(block) > MAX_VIDEO_BYTES:
            return None
        if response.status == 200:
            return block if not data else None
        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('content-range',''))
        if not match:
            return None
        start,end,size = map(int,match.groups())
        if start != len(data) or end-start+1 != len(block) or size > MAX_VIDEO_BYTES or (total is not None and total != size):
            return None
        total=size;data.extend(block)
        if len(data)==total:
            return bytes(data)
    return None


def _download_with_playwright(url, output_path):
    from playwright.sync_api import sync_playwright
    page=None;owned_browser=None
    try:
        with sync_playwright() as pw:
            context=None
            if urlsplit(url).hostname == 'douyin.com' or (urlsplit(url).hostname or '').endswith('.douyin.com'):
                from services.creator_connector import _alive,platform_directory
                profile=platform_directory('douyin')/'profile/DevToolsActivePort'
                if _alive('douyin') and profile.is_file():
                    port=int(profile.read_text().splitlines()[0])
                    browser=pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}',timeout=10000)
                    context=browser.contexts[0]
            if context is None:
                owned_browser=pw.chromium.launch(headless=True,args=['--disable-dev-shm-usage'])
                context=owned_browser.new_context(viewport={'width':1080,'height':1920})
            page=context.new_page();media=[]
            def observe(response):
                kind=response.headers.get('content-type','')
                if (response.status in {200,206} and response.url.startswith('https://')
                        and (response.request.resource_type=='media' or kind.startswith(('video/','audio/')))):
                    media.append(response.url)
            page.on('response',observe)
            try:
                page.goto(url,wait_until='domcontentloaded',timeout=35000)
                video=page.locator('video').first
                video.wait_for(state='attached',timeout=18000)
                video.evaluate('el=>{el.muted=true;el.play().catch(()=>{});}');page.wait_for_timeout(2000)
                source=video.evaluate('el=>el.currentSrc||el.src')
                candidates=list(dict.fromkeys(([source] if source.startswith('https://') else [])+media))
                for source in candidates[:6]:
                    data=_read_complete_media(context.request,source)
                    if not data:
                        continue
                    temporary=Path(output_path).with_suffix('.browser.mp4');temporary.write_bytes(data)
                    if valid_video(temporary):
                        temporary.replace(output_path)
                        Path(output_path).with_suffix('.metadata.json').write_text(json.dumps({
                            'title':page.title(),'source_url':url,'webpage_url':page.url.split('?')[0],
                            'import_method':'ordinary_browser_player'},ensure_ascii=False))
                        return None
                    temporary.unlink(missing_ok=True)
                return '正常播放器未提供完整可读取的音视频，未用截图冒充视频'
            finally:
                page.close();page=None
                if owned_browser:owned_browser.close()
    except Exception as error:
        return f'正常播放未完成（{type(error).__name__}），可能需要平台本人验证；未绕过验证'


def extract_audio(video_path, audio_output_path):
    try:
        result=subprocess.run(['ffmpeg','-v','error','-threads','2','-i',video_path,'-vn','-acodec','mp3',
                               '-ab','192k','-ar','44100','-y',audio_output_path],capture_output=True,text=True,timeout=120)
        return None if result.returncode==0 and Path(audio_output_path).is_file() else '视频音轨提取失败'
    except (OSError,subprocess.TimeoutExpired):
        return '音轨提取进程未完成'
