"""A native SVG title design, separate from the subtitled video frames."""
from html import escape
from pathlib import Path

from services.factory_media import WIDTH, HEIGHT, _font, _wrap


def render_cover(content, target):
    from playwright.sync_api import sync_playwright

    title = str(content.get("title", "AI热点讲明白"))[:36]
    lines = _wrap(title, _font(112, bold=True), 880)
    if len(lines) > 4:
        raise ValueError("封面标题过长，请缩短标题")
    text = "".join(f'<text x="100" y="{430 + i * 152}" class="title">{escape(line)}</text>'
                   for i, line in enumerate(lines))
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
    <defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#101d35"/><stop offset="1" stop-color="#060b14"/></linearGradient>
    <radialGradient id="glow"><stop stop-color="#3ee6bb" stop-opacity=".18"/><stop offset="1" stop-color="#3ee6bb" stop-opacity="0"/></radialGradient></defs>
    <style>text{{font-family:"Noto Sans CJK SC",sans-serif;fill:#f3f8ff}}.title{{font-size:112px;font-weight:700}}</style>
    <rect width="1080" height="1920" fill="url(#bg)"/><circle cx="820" cy="1200" r="600" fill="url(#glow)"/>
    <rect x="100" y="165" width="10" height="48" rx="5" fill="#5ef0c5"/>
    <text x="134" y="203" font-size="36" letter-spacing="5">AI · 看懂科技变化</text>
    {text}
    <g transform="translate(540 1210)" stroke="#5ef0c5" fill="none"><rect x="-165" y="-165" width="330" height="330" rx="45" stroke-width="3"/>
    <rect x="-215" y="-215" width="430" height="430" rx="65" opacity=".25"/>
    <path d="M-320 0H-165 M165 0H320 M0 -320V-165 M0 165V320" stroke-width="3"/>
    <circle cx="-320" cy="0" r="12"/><circle cx="320" cy="0" r="12"/><circle cx="0" cy="-320" r="12"/><circle cx="0" cy="320" r="12"/></g>
    <text x="540" y="1260" text-anchor="middle" font-size="150" font-weight="700">AI</text>
    <path d="M100 1640H980" stroke="#ffffff" opacity=".18"/>
    <text x="100" y="1720" font-size="35" fill="#5ef0c5">看事实 · 看演示 · 看影响</text>
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
    return {"kind": "original_svg_title_design", "design_path": str(svg_path)}
