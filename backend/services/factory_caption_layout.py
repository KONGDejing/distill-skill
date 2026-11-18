"""Conservative project layout for collapsed short-video platform controls.

Coordinates are our rendering policy, not a claim of a platform-certified mask.
Expanded descriptions and comments can cover additional pixels in any player.
"""
CAPTION_SAFE_AREA_VERSION = 1
CYBERPUNK_STYLES = {'cyberpunk_news', 'cyberpunk_news_focus', 'cyberpunk_paper'}
SAFE_STYLES = {'science_editorial_safe_v3', 'science_editorial_focus_safe_v3', 'cyberpunk_news', 'cyberpunk_news_focus'}
CAPTION_LEFT = 80
CAPTION_RIGHT = 880
CAPTION_BOTTOM = 1380
CAPTION_FONT = 52
CAPTION_MAX_LINES = 3
VIDEO_TOP = 460
VIDEO_HEIGHT = 720
ATTRIBUTION_Y = 1420


def safe_caption_lines(text):
    from services.factory_media import _font, _wrap
    for size in range(CAPTION_FONT, 35, -2):
        rows = _wrap(text, _font(size, True), CAPTION_RIGHT-CAPTION_LEFT)
        if len(rows) <= CAPTION_MAX_LINES:
            return size, rows
    raise ValueError('口播字幕无法完整放入三行安全区，请重分字幕时间段，不能截断文字或挤到底部')


def caption_safety():
    return {'version': CAPTION_SAFE_AREA_VERSION, 'left': CAPTION_LEFT, 'right': CAPTION_RIGHT,
            'bottom': CAPTION_BOTTOM, 'bottom_reserved': 1920-CAPTION_BOTTOM,
            'right_reserved': 1080-CAPTION_RIGHT, 'max_lines': CAPTION_MAX_LINES,
            'scope': '项目保守排版；用于避让常规底部文案及右侧按钮，不是官方界面实测或展开评论层保证。'}


def require_caption_safety(media, variant):
    quality = media.get('quality') or {}
    box = quality.get('caption_safe_area') or {}
    final = variant.get('delivery_quality') or {}
    if (quality.get('caption_safe_area_version') != CAPTION_SAFE_AREA_VERSION
            or final.get('caption_safe_area_version') != CAPTION_SAFE_AREA_VERSION
            or box.get('version') != CAPTION_SAFE_AREA_VERSION
            or box.get('bottom', 1920) > CAPTION_BOTTOM
            or box.get('right', 1080) > CAPTION_RIGHT
            or box.get('max_lines', 99) > CAPTION_MAX_LINES):
        raise ValueError('暂缓提交：本条尚未通过口播字幕安全区重排，请先重制并核对底部文案与右侧按钮避让')


def preferred_douyin_variant(item):
    """The corrected selected export is the preview/download source, not XHS."""
    if not (((getattr(item, 'brief', None) or {}).get('distribution') or {}).get('douyin_selected')):
        return None
    return next((p for p in item.packages or [] if p.get('platform') == 'douyin'
                 and (p.get('delivery_quality') or {}).get('caption_safe_area_version')
                 == CAPTION_SAFE_AREA_VERSION), None)
