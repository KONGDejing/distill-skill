"""Traceable official documents and labelled reading excerpts, never fake PDFs.

Original company PDFs retain their actual pages. HTML excerpts are explicitly
labelled as our reading layout; their quoted text must exist in saved sources.
"""
from hashlib import sha256
from html import escape
from pathlib import Path
import json
import re

from services.factory_papers import PDF_LOCK
from services.factory_research import _validated_url
from services.factory_resources import ensure_memory_available


def _digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _manifest(path, title, records, pages, method):
    result = {'id': 'official-' + _digest(path)[:16], 'title': title, 'kind': 'official_document',
              'path': str(Path(path).resolve()), 'sha256': _digest(path), 'url': records[0]['url'],
              'pages': pages, 'verified_project_urls': [], 'preparation_method': method,
              'original_sources': [{key: record[key] for key in ('id', 'title', 'url', 'published_at') if record.get(key)}
                                   for record in records]}
    Path(path).with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def merge_original_pdfs(records, target, title):
    """Concatenate actual first-party PDF pages with per-page attribution."""
    import pypdfium2 as pdfium
    ensure_memory_available()
    if not records:
        raise ValueError('至少提供一份真实来源PDF')
    target = Path(target).resolve(); target.parent.mkdir(parents=True, exist_ok=True)
    pages = []
    with PDF_LOCK, pdfium.PdfDocument.new() as output:
        for record in records:
            _validated_url(record['url'])
            path = Path(record['path']).resolve()
            if not path.is_file() or not path.read_bytes().startswith(b'%PDF'):
                raise ValueError('官方原始PDF不存在或格式错误')
            original_hash = _digest(path)
            with pdfium.PdfDocument(path) as source:
                offset = len(pages)
                output.import_pages(source)
                for number, page in enumerate(source):
                    text = page.get_textpage().get_text_range()
                    pages.append({'page': offset + number + 1, 'text': text,
                                  'source_id': record['id'], 'source_url': record['url'],
                                  'original_page': number + 1, 'original_sha256': original_hash})
        output.save(target)
    return _manifest(target, title, records, pages, 'unaltered_original_pdf_pages_with_source_map')


def make_reading_excerpts(records, excerpts, target, title):
    """Render attributed exact excerpts, explicitly not a screenshot/system card."""
    from playwright.sync_api import sync_playwright
    import pypdfium2 as pdfium
    ensure_memory_available()
    if not records or not excerpts or len({record['id'] for record in records}) != len(records):
        raise ValueError('阅读摘录需要非空页面和唯一来源标识')
    by_id = {record['id']: record for record in records}
    normalize = lambda value: re.sub(r'\s+', ' ', value).strip()
    sections, origins, used = [], [], set()
    for excerpt in excerpts:
        source = by_id[excerpt['source_id']]
        _validated_url(source['url'])
        quote = normalize(excerpt['quote'])
        if not 2 <= len(quote) <= 700 or quote not in normalize(source['text']):
            raise ValueError('阅读摘录必须逐字存在于保存的真实来源中')
        identity = (source['id'], quote.casefold())
        if identity in used:
            raise ValueError('不能把同一摘录重复排版后当新页面')
        used.add(identity); origins.append(source)
        heading = excerpt.get('heading', '核对原文')
        table_html = ''
        note_html = ''
        reading_note = excerpt.get('reading_note')
        if reading_note:
            if not isinstance(reading_note, str) or len(reading_note) > 450:
                raise ValueError('阅读解读须简短，并经独立事实审核')
            note_html = '<aside><b>编辑解读 · 非原文引语</b><p>' + escape(reading_note) + '</p></aside>'
        table = excerpt.get('table')
        if table:
            rows = table.get('rows', [])
            if (not rows or len(rows) > 8 or any(not isinstance(row, list) or not 1 <= len(row) <= 5 for row in rows)
                    or any(not isinstance(cell, str) or not cell.strip() or normalize(cell) not in normalize(source['text'])
                           for row in rows for cell in row)):
                raise ValueError('整理表中的每个原文值都必须存在于真实来源，行对应关系仍须独立事实审核')
            table_html = '<table>' + ''.join('<tr>' + ''.join('<td>' + escape(cell) + '</td>' for cell in row) + '</tr>' for row in rows) + '</table>'
        sections.append(f'''<section><div class="label">官方原文摘录 · 阅读排版</div>
          <h1>{escape(heading)}</h1><h2>{escape(source['title'])}</h2>
          <blockquote>{escape(quote)}</blockquote>{table_html}{note_html}
          <div class="note">只展示对应原文，不代表本账号实测。此为阅读排版，不是官网截图。</div>
          <footer>{escape(source['url'])}<br>来源日期：{escape(str(source.get('published_at') or '原文未标明'))}</footer></section>''')
    target = Path(target).resolve(); target.parent.mkdir(parents=True, exist_ok=True)
    html = target.with_suffix('.html')
    html.write_text('''<!doctype html><meta charset="utf-8"><style>
      @page{size:800px 1120px;margin:0}*{box-sizing:border-box}body{margin:0;font-family:"Noto Sans CJK SC",sans-serif;color:#172c3b}
      section{height:1120px;padding:60px 54px;background:#fffdf8;position:relative;break-after:page}
      .label{font-size:21px;color:#47867a;border-bottom:2px solid #98bdb3;padding-bottom:16px}
      h1{font-size:46px;margin:35px 0 25px}h2{font-size:23px;font-weight:500;line-height:1.6}
      blockquote{margin:40px 0;padding:27px;border-left:7px solid #46b6a0;background:#eef6ef;font-size:32px;line-height:1.65;overflow-wrap:anywhere}
      aside{font-size:24px;line-height:1.6;margin-top:28px;padding:22px;background:#eff3f5}aside b{font-size:18px;color:#647780}aside p{margin:10px 0 0}table{width:100%;border-collapse:collapse;font-size:24px;margin-top:25px}td{padding:18px 10px;border-bottom:2px solid #cadbd1}tr:first-child{font-weight:700;background:#eef6ef}
      .note{position:absolute;bottom:145px;left:54px;right:54px;font-size:18px;color:#677a82}
      footer{position:absolute;bottom:40px;left:54px;right:54px;font-size:15px;line-height:1.6;overflow-wrap:anywhere}
      </style>''' + ''.join(sections))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=False,
                                    args=['--headless=new', '--no-sandbox', '--disable-dev-shm-usage'])
        try:
            page = browser.new_page();page.goto(html.as_uri());page.evaluate('document.fonts.ready')
            page.pdf(path=str(target), print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()
    pages = []
    with PDF_LOCK, pdfium.PdfDocument(target) as pdf:
        if len(pdf) != len(excerpts):
            raise ValueError('原文摘录分页溢出，请压缩排版')
        for index, page in enumerate(pdf):
            source = origins[index]
            pages.append({'page': index + 1, 'text': page.get_textpage().get_text_range(),
                          'source_id': source['id'], 'source_url': source['url'],
                          'source_text_sha256': sha256(source['text'].encode()).hexdigest(),
                          'original_quote': excerpts[index]['quote'],
                          'reading_note': excerpts[index].get('reading_note', '')})
    return _manifest(target, title, records, pages, 'labelled_exact_source_excerpts_not_official_pdf_or_screenshot')
