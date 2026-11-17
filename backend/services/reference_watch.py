"""Watch public creator posts and learn from bounded, actual video samples.

Only the normal profile page is opened, in its own tab. Responses to that page
are observed, never signed/replayed. Verification stops collection. Reference
films remain reference-only and never enter publication packages as footage.
"""
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from database import SessionLocal
from models.blogger import Blogger, BloggerWatch
from models.blogger_content_dna import BloggerContentDNA
from models.source_video import SourceVideo
from services.factory_resources import ensure_memory_available, MemoryPressure
from services.video_downloader import normalize_video_url


def watch_summary(watch):
    if not watch:
        return {"enabled": False, "status": "disabled"}
    return {key: (getattr(watch, key).isoformat() + 'Z' if getattr(watch, key) else None)
            for key in ('last_checked_at', 'next_check_at', 'last_success_at', 'last_analyzed_at')} | {
        'enabled': watch.enabled, 'status': watch.status,
        'interval_seconds': watch.interval_seconds, 'daily_import_limit': watch.daily_import_limit,
        'summary': watch.summary or {}, 'error_message': watch.error_message,
        'profile_url': watch.profile_url}


def profile_link(value):
    url = normalize_video_url(value)
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in {'www.douyin.com', 'douyin.com', 'v.douyin.com'}:
        raise ValueError('自动跟踪暂支持抖音公开主页或其官方分享链接')
    if p.hostname != 'v.douyin.com' and not re.fullmatch(r'/user/[A-Za-z0-9_-]+/?', p.path):
        raise ValueError('自动跟踪需要博主主页，不能使用单条视频地址')
    return url


def select_posts(posts, sec_uid, expected_name, now=None):
    """Reject suggested videos, pinned-order ambiguity and foreign authors."""
    now = now or datetime.utcnow()
    unique = {}
    for post in posts:
        author = post.get('author') or {}
        if author.get('sec_uid') != sec_uid or author.get('nickname') != expected_name:
            continue
        identifier, timestamp = str(post.get('aweme_id', '')), post.get('create_time')
        if not re.fullmatch(r'\d{10,24}', identifier) or type(timestamp) not in {int, float}:
            continue
        try:
            published = datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)
        except (ValueError, OSError, OverflowError):
            continue
        if published > now + timedelta(minutes=5):
            continue
        unique[identifier] = {'post_id': identifier, 'source_url': 'https://www.douyin.com/video/' + identifier,
            'title': str(post.get('desc') or '')[:1024], 'published_at': published.isoformat() + 'Z',
            'timestamp': timestamp, 'reference_only': True}
    return sorted(unique.values(), key=lambda post: post['timestamp'], reverse=True)[:12]


def is_profile_posts_response(url):
    p = urlsplit(url)
    return (p.scheme == 'https' and p.hostname in {'www.douyin.com', 'douyin.com', 'www-hj.douyin.com'}
            and p.path == '/aweme/v1/web/aweme/post/')


def discover_posts(profile_url, expected_name):
    from playwright.sync_api import sync_playwright
    from services.creator_connector import _alive, platform_directory
    url = profile_link(profile_url)
    with sync_playwright() as pw:
        owned = None
        profile = platform_directory('douyin') / 'profile/DevToolsActivePort'
        if _alive('douyin') and profile.is_file():
            port = int(profile.read_text().splitlines()[0])
            browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}', timeout=10000)
            context = browser.contexts[0]
        else:
            owned = pw.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
            context = owned.new_context()
        page = context.new_page()
        posts = []

        def observe(response):
            if not is_profile_posts_response(response.url):
                return
            try:
                data = response.json()
                if data.get('status_code') == 0 and isinstance(data.get('aweme_list'), list):
                    posts.extend(data['aweme_list'][:30])
            except Exception:
                pass

        page.on('response', observe)
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=35000)
            deadline = time.monotonic() + 35
            resolved = None
            while time.monotonic() < deadline:
                p = urlsplit(page.url)
                match = re.fullmatch(r'/user/([A-Za-z0-9_-]+)/?', p.path)
                if p.hostname in {'www.douyin.com', 'douyin.com'} and match:
                    resolved = 'https://www.douyin.com/user/' + match.group(1)
                    selected = select_posts(posts, match.group(1), expected_name)
                    if selected:
                        return resolved, selected
                page.wait_for_timeout(1000)
            raise ValueError('公开主页未取得可核验作者和发布时间的作品列表；可能需要官方验证。本轮未采集推荐栏，也未绕过验证。')
        finally:
            page.close()
            if owned:
                owned.close()


def _keys(video):
    extra = video.extra_data or {}
    keys = {extra.get('reference_post_id')}
    for url in (video.source_url, extra.get('webpage_url')):
        match = re.search(r'/video/(\d+)', url or '')
        if match:
            keys.add(match.group(1))
    return keys - {None}


def import_discovered(db, blogger_id, posts, now, daily_limit, *, max_new=3):
    """Durable dedup before downloads; retries cannot consume new daily slots."""
    from sqlalchemy import update
    db.execute(update(Blogger).where(Blogger.id == blogger_id).values(updated_at=Blogger.updated_at))
    existing = db.query(SourceVideo).filter_by(blogger_id=blogger_id).all()
    known = set().union(*(_keys(v) for v in existing)) if existing else set()
    start = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).replace(
        hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    used = sum(v.created_at >= start and (v.extra_data or {}).get('auto_reference') is True for v in existing)
    remaining = max(0, min(3, max_new, daily_limit - used))
    added = []
    for post in posts:
        if post['post_id'] in known or len(added) >= remaining:
            continue
        video = SourceVideo(blogger_id=blogger_id, source_url=post['source_url'], title=post['title'],
            status='pending', created_at=now, extra_data={
                'auto_reference': True, 'reference_only': True, 'reference_post_id': post['post_id'],
                'reference_published_at': post['published_at']})
        db.add(video); db.flush(); added.append(video.id); known.add(post['post_id'])
    db.commit()
    return added


def _sample_signature(db, blogger_id):
    values = db.query(SourceVideo).filter_by(blogger_id=blogger_id, status='transcribed').order_by(
        SourceVideo.created_at.desc()).limit(12).all()
    keys = sorted({next(iter(_keys(v)), v.source_url or v.id) for v in values if v.transcript})
    return hashlib.sha256(json.dumps(keys).encode()).hexdigest() if keys else None


def check_watch(blogger_id):
    now = datetime.utcnow()
    with SessionLocal() as db:
        b, w = db.get(Blogger, blogger_id), db.get(BloggerWatch, blogger_id)
        if not b or not w or not w.enabled:
            return
        name, url, limit = b.name, w.profile_url or b.profile_url, w.daily_import_limit
        w.status, w.last_checked_at = 'checking', now
        w.next_check_at = now + timedelta(seconds=w.interval_seconds)
        db.commit()
    try:
        ensure_memory_available()
        resolved, posts = discover_posts(url, name)
        with SessionLocal() as db:
            w = db.get(BloggerWatch, blogger_id)
            if not w or not w.enabled:
                return
            w.profile_url, w.last_success_at = resolved, datetime.utcnow()
            pending = [v.id for v in db.query(SourceVideo).filter_by(blogger_id=blogger_id).filter(
                SourceVideo.status.in_(['pending', 'downloading', 'downloaded', 'extracting', 'transcribing'])).order_by(
                SourceVideo.created_at.desc()).all() if (v.extra_data or {}).get('auto_reference')][:3]
            added = import_discovered(db, blogger_id, posts, now, limit, max_new=3-len(pending))
            processing = pending + added
            w.summary = {'discovered_count': len(posts), 'new_count': len(added), 'latest_posts': posts,
                         'imported_ids': added, 'processing_ids': processing,
                         'scope': '公开主页实际作品；按真实发布时间排序，已排除推荐栏和置顶顺序影响。'}
            w.status, w.error_message = 'importing' if processing else 'up_to_date', None
            db.commit()
        from tasks.celery_app import run_download_and_transcribe, run_distill_analysis
        failures = []
        for identifier in processing:
            with SessionLocal() as db:
                current = db.get(BloggerWatch, blogger_id)
                if not current or not current.enabled:
                    return
            ensure_memory_available()
            result = run_download_and_transcribe.run(identifier)
            if result.get('error'):
                failures.append(identifier)
        with SessionLocal() as db:
            w = db.get(BloggerWatch, blogger_id)
            signature = _sample_signature(db, blogger_id)
            changed = signature and signature != w.analysis_signature
            if not w.enabled:
                return
            w.status = 'analyzing' if changed else 'up_to_date'
            w.summary = {**w.summary, 'failed_import_ids': failures}
            db.commit()
        if changed:
            ensure_memory_available()
            result = run_distill_analysis.run(blogger_id)
            if result.get('error'):
                raise ValueError('新样本分析尚未完成；保留上一版有效风格分析，稍后重试。')
        with SessionLocal() as db:
            w = db.get(BloggerWatch, blogger_id)
            if not w or not w.enabled:
                return
            if changed:
                w.analysis_signature, w.last_analyzed_at = signature, datetime.utcnow()
                dna = db.query(BloggerContentDNA).filter_by(blogger_id=blogger_id).first()
                if dna:
                    try:
                        usage = json.loads(dna.raw_analysis or '{}').get('_model_usage')
                    except (ValueError, AttributeError):
                        usage = None
                    w.summary = {**w.summary, 'analysis_version': dna.version, 'analysis_usage': usage}
            w.status = 'partial' if failures else 'up_to_date'
            w.error_message = '部分新视频未完成正常下载或转写，已保留错误记录；没有把失败样本算作已学习。' if failures else None
            db.commit()
    except Exception as error:
        with SessionLocal() as db:
            w = db.get(BloggerWatch, blogger_id)
            if w:
                w.status = 'paused_memory' if isinstance(error, MemoryPressure) else 'blocked'
                w.error_message = str(error)[:900] if isinstance(error, (ValueError, MemoryPressure)) else f'对标检查未完成（{type(error).__name__}），保留上次有效分析。'
                db.commit()


def reference_brief(agent_id, session_factory=None):
    """Current distilled style is input data, never evidence for today's news."""
    if agent_id not in {'director', 'writer', 'producer', 'producer3', 'reviewer'}:
        return []
    from sqlalchemy import inspect
    with (session_factory or SessionLocal)() as db:
        if not inspect(db.get_bind()).has_table(BloggerWatch.__tablename__):
            return []
        rows = db.query(BloggerWatch, BloggerContentDNA, Blogger).join(
            BloggerContentDNA, BloggerContentDNA.blogger_id == BloggerWatch.blogger_id).join(
            Blogger, Blogger.id == BloggerWatch.blogger_id).filter(BloggerWatch.enabled.is_(True)).limit(4).all()
        return [{'name': b.name, 'version': dna.version,
            'last_analyzed_at': w.last_analyzed_at.isoformat() + 'Z' if w.last_analyzed_at else None,
            'last_check_status': w.status,
            'application': '按本条题材选择相关样本的方法，保持科技场景、人物新闻两条风格独立；不要把多位博主机械混成一种固定模板。只用于未发布新作与试验，不改写已提交作品、文件或回执。',
            'methods': json.dumps({'factory_improvements': ((dna.viral_techniques or {}).get('factory_comparison') or {}).get('priority_actions'),
                'narrative': (dna.viral_techniques or {}).get('narrative_structure'),
                'rhythm': (dna.viral_techniques or {}).get('rhythm_control'),
                'visual_style': (dna.viral_techniques or {}).get('visual_style'),
                'language': dna.language_style}, ensure_ascii=False)[:2500],
            'scope': '只借鉴实际样本支持的表达方法；不是本期新闻事实或可直接发布的素材，不复制原稿、账号标识或署名。'}
            for w, dna, b in rows]


def serve():
    import fcntl
    from database import init_db
    init_db()
    directory = Path(__file__).resolve().parents[2] / '.runtime'
    directory.mkdir(exist_ok=True, mode=0o700)
    with (directory / 'reference-watch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            with SessionLocal() as db:
                due = [w.blogger_id for w in db.query(BloggerWatch).filter(
                    BloggerWatch.enabled.is_(True), BloggerWatch.status != 'paused_memory',
                    (BloggerWatch.next_check_at.is_(None)) | (BloggerWatch.next_check_at <= datetime.utcnow())).all()]
            for identifier in due:
                check_watch(identifier)
            time.sleep(30)


if __name__ == '__main__':
    serve()
