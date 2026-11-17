"""Read an ordinary public WeChat Channels preview, without claiming a feed.

The currently observed official preview supplies a caption, author name, cover
and date, but neither a playable video nor an account's continuous post list.
Keep these capabilities explicit; caption metadata is not an audio transcript.
"""
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs

from services.video_downloader import normalize_video_url


def wechat_share_url(value):
    import re
    url = normalize_video_url(value)
    p = urlsplit(url)
    valid = (p.hostname == 'weixin.qq.com' and re.fullmatch(r'/sph/[A-Za-z0-9_-]+/?', p.path)) or (
        p.hostname == 'channels.weixin.qq.com' and p.path == '/finder-preview/pages/sph' and parse_qs(p.query).get('id'))
    if p.scheme != 'https' or not valid:
        raise ValueError('需要微信视频号的官方作品分享链接')
    return url


def preview_metadata(value, expected_name, source_url):
    if not isinstance(value, dict) or value.get('errCode') != 0:
        raise ValueError('视频号官方预览未返回有效作品')
    data = value.get('data') or {}
    author, feed = data.get('authorInfo') or {}, data.get('feedInfo') or {}
    if author.get('nickname') != expected_name:
        raise ValueError('视频号预览作者与指定对标账号不一致')
    timestamp = feed.get('createtime')
    if type(timestamp) not in {int, float} or not feed.get('description'):
        raise ValueError('视频号预览缺少可核验发布时间或作品文字')
    try:
        published = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        raise ValueError('视频号预览发布时间无效') from None
    return {'author_name': author['nickname'], 'description': str(feed['description'])[:8000],
        'published_at': published, 'source_url': source_url, 'reference_only': True,
        'metadata_available': True, 'video_available': False, 'account_feed_available': False,
        'scope': '官方单条预览的文字及发布时间；未取得可播放音视频或账号持续作品列表，不能作为画面或声音风格验收。'}


def inspect_preview(url, expected_name):
    from playwright.sync_api import sync_playwright
    from services.factory_resources import ensure_memory_available
    ensure_memory_available()
    url = wechat_share_url(url)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = browser.new_page()
        results = []

        def observe(response):
            p = urlsplit(response.url)
            if p.hostname == 'channels.weixin.qq.com' and p.path == '/finder-preview/api/feed/get_feed_info':
                try:
                    results.append(response.json())
                except Exception:
                    pass
        page.on('response', observe)
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=35000)
            for _ in range(25):
                if results:
                    return preview_metadata(results[-1], expected_name, url)
                page.wait_for_timeout(1000)
            raise ValueError('未读取到官方视频号预览，保留待接入状态')
        finally:
            browser.close()


def record_preview(db, blogger_id, metadata):
    from models.blogger import BloggerWatch
    from models.source_video import SourceVideo
    from datetime import datetime
    existing = db.query(SourceVideo).filter_by(blogger_id=blogger_id, source_url=metadata['source_url']).first()
    if not existing:
        existing = SourceVideo(blogger_id=blogger_id, source_url=metadata['source_url'])
        db.add(existing)
    existing.title = metadata['description'].split('\n')[0][:100]
    existing.status, existing.extra_data = 'preview_only', metadata
    # A caption is not a transcript. Never enqueue normal video analysis here.
    watch = db.get(BloggerWatch, blogger_id)
    if not watch:
        watch = BloggerWatch(blogger_id=blogger_id)
        db.add(watch)
    watch.enabled, watch.status = False, 'awaiting_feed'
    watch.profile_url, watch.last_checked_at = metadata['source_url'], datetime.utcnow()
    watch.error_message = f"已核对本条{metadata['author_name']}官方视频号预览及发布时间；该公开页面仅提供单条文字与扫码入口，未提供可播放视频和账号连续作品列表。持续自动追踪尚未接通。"
    watch.summary = {'preview': metadata, 'discovered_count': 1, 'new_count': 0,
                     'video_import_count': 0, 'account_feed_available': False}
    db.commit()
    return existing
