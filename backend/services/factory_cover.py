"""Platform openings: a branded card or titles over the actual source frame."""
from html import escape
from pathlib import Path
import re

from services.factory_media import WIDTH, HEIGHT, _font, _wrap

COVER_DESIGN_VERSION = 2
FRAME_TITLE_VERSION = 2


def render_frame_cover(source, title, target, platform):
    """Letter the actual first frame; export the same transparent title for motion.

    The upper-middle headline avoids bottom captions and right-side controls.
    It is an overlay, never a substitute illustration or a frozen clip.
    """
    from PIL import Image, ImageDraw
    import hashlib
    from services.factory_media import _run

    if platform not in {"xiaohongshu", "kuaishou"}:
        raise ValueError("首帧标题仅用于小红书和快手")
    target = Path(target)
    frame_path = target.with_name(target.stem + "-source.png")
    overlay_path = target.with_name(target.stem + "-title.png")
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(source),
          "-frames:v", "1", str(frame_path)])
    with Image.open(frame_path) as frame:
        background = frame.convert("RGBA")
    # Draw in a common coordinate system and scale only the transparent layer.
    overlay = Image.new("RGBA", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(overlay)
    title = str(title).strip()
    if not title:
        raise ValueError("首帧缺少视频标题")
    for size in range(78 if platform == "kuaishou" else 70, 47, -2):
        font = _font(size, bold=True)
        lines = _wrap(title, font, 800)
        if len(lines) > 1:
            # Prefer a real clause boundary to splitting words such as 离线边界.
            candidates = [(title[:m.end()], title[m.end():]) for m in re.finditer(r"[？?，,：:]", title)
                          if m.end() < len(title)]
            candidates = [parts for parts in candidates if all(font.getlength(s) <= 800 for s in parts)]
            if candidates:
                lines = list(min(candidates, key=lambda parts: abs(font.getlength(parts[0]) - font.getlength(parts[1]))))
        if len(lines) <= 2 and not any(len(s.strip()) == 1 for s in lines):
            break
    else:
        raise ValueError("首帧标题过长，请压缩标题")
    top, bottom = 380, 650
    if platform == "xiaohongshu":
        draw.rounded_rectangle((56, top, 944, bottom), radius=24, fill=(255, 252, 249, 244))
        draw.rounded_rectangle((78, top + 30, 86, bottom - 30), radius=4, fill="#d94d76")
        x, fill, stroke = 114, "#202736", 0
    else:
        # Plain, outlined headline: the original footage remains the background.
        x, fill, stroke = 72, "#ffffff", 4
    line_height = size + 18
    y = top + (bottom - top - len(lines) * line_height) // 2
    for n, line in enumerate(lines):
        color = "#ffc45e" if platform == "kuaishou" and n == len(lines) - 1 else fill
        draw.text((x, y + n * line_height), line, font=font, fill=color,
                  stroke_width=stroke, stroke_fill="#111111", anchor="lt")
    if background.size != overlay.size:
        overlay = overlay.resize(background.size, Image.Resampling.LANCZOS)
    overlay.save(overlay_path)
    Image.alpha_composite(background, overlay).convert("RGB").save(target)
    return {"kind": "source_first_frame_with_title", "version": COVER_DESIGN_VERSION,
            "frame_title_version": FRAME_TITLE_VERSION, "platform": platform,
            "overlay_path": str(overlay_path), "source_frame_sha256": hashlib.sha256(frame_path.read_bytes()).hexdigest(),
            "text_rendered": title, "font_size": size, "lines": lines,
            "title_position": "upper_middle",
            "title_bounds_normalized": [56 / WIDTH, top / HEIGHT, 944 / WIDTH, bottom / HEIGHT]}


def cover_layout(title):
    paragraphs = [p.strip() for p in title.splitlines() if p.strip()]
    for size in range(112, 63, -2):
        lines = [line for paragraph in paragraphs for line in _wrap(paragraph, _font(size, bold=True), 880)]
        if len(paragraphs) > 1 and len(lines) != len(paragraphs):
            continue
        if len(lines) <= 4 and not any(len(line.strip()) == 1 for line in lines if len(lines) > 1):
            return size, lines
    raise ValueError("封面标题过长或断句不合适，请缩短标题并调整分行")


def render_cover(content, target):
    from playwright.sync_api import sync_playwright

    style = content.get("platform_style") or {}
    accent = style.get("accent", "#5ef0c5")
    label = escape(style.get("label", "AI · 看懂科技变化"))
    title = str(content.get("cover_text") or content.get("title") or "AI热点讲明白").strip()
    size, lines = cover_layout(title)
    text = "".join(f'<text x="100" y="{430 + i * (size + 40)}" class="title" style="fill:{accent if i == len(lines)-1 else "#f3f8ff"}">{escape(line)}</text>'
                   for i, line in enumerate(lines))
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
    <defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#101d35"/><stop offset="1" stop-color="#060b14"/></linearGradient>
    <radialGradient id="glow"><stop stop-color="#3ee6bb" stop-opacity=".18"/><stop offset="1" stop-color="#3ee6bb" stop-opacity="0"/></radialGradient></defs>
    <style>text{{font-family:"Noto Sans CJK SC",sans-serif;fill:#f3f8ff}}.title{{font-size:{size}px;font-weight:700}}</style>
    <rect width="1080" height="1920" fill="url(#bg)"/><circle cx="820" cy="1200" r="600" fill="url(#glow)"/>
    <rect x="100" y="165" width="10" height="48" rx="5" fill="{accent}"/>
    <text x="134" y="203" font-size="36" letter-spacing="5">{label}</text>
    {text}
    <g transform="translate(540 1210)" stroke="{accent}" fill="none"><rect x="-165" y="-165" width="330" height="330" rx="45" stroke-width="3"/>
    <rect x="-215" y="-215" width="430" height="430" rx="65" opacity=".25"/>
    <path d="M-320 0H-165 M165 0H320 M0 -320V-165 M0 165V320" stroke-width="3"/>
    <circle cx="-320" cy="0" r="12"/><circle cx="320" cy="0" r="12"/><circle cx="0" cy="-320" r="12"/><circle cx="0" cy="320" r="12"/></g>
    <text x="540" y="1260" text-anchor="middle" font-size="150" font-weight="700">AI</text>
    <path d="M100 1640H980" stroke="#ffffff" opacity=".18"/>
    <text x="100" y="1720" font-size="35" fill="{accent}">看事实 · 看演示 · 看影响</text>
    </svg>'''
    svg_path = Path(target).with_suffix(".svg")
    svg_path.write_text(svg, encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
            page.goto(svg_path.resolve().as_uri())
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(target))
        finally:
            browser.close()
    return {"kind": "original_svg_title_design", "design_path": str(svg_path), "version": COVER_DESIGN_VERSION,
            "font_size": size, "text_rendered": title, "lines": lines, "title_position": "upper_middle"}
