"""Read and display the actual cited paper; no generated substitute images."""
import hashlib
import json
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx

PDF_LOCK = threading.Lock()  # PDFium is not thread safe.
PROJECTS = {"sumo048.github.io", "roboforpc.github.io", "ai4ce.github.io",
            "weirdlabuw.github.io", "zju3dv.github.io"}


def is_paper_story(brief, sources):
    return ("论文" in str(brief.get("category", "")) or any(
        s.get("kind") == "paper" or urlsplit(s.get("url", "")).hostname == "arxiv.org"
        or urlsplit(s.get("url", "")).hostname in PROJECTS for s in sources))


def _paper_id(url):
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname == "arxiv.org":
        match = re.fullmatch(r"/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?", parts.path)
        if match:
            return match[1]
    return None


def _has_project_link(original, candidate):
    origin = urlsplit(candidate)
    needle = (origin.netloc + origin.path).rstrip("/")
    normalized = re.sub(r"\s+", "", original)
    return bool(re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(needle) + r'(?=[/<>"\x27),.;]|$)', normalized))


def collect_paper(sources, directory):
    from services.factory_research import _fetch_url
    from services.factory_montage import _sha256
    identifiers = {_paper_id(s.get("url", "")) for s in sources} - {None}
    project_urls = []
    project_candidates = []
    for source in sources:
        if urlsplit(source.get("url", "")).hostname not in PROJECTS:
            continue
        document, url = _fetch_url(source["url"])
        project_candidates.append(url.rstrip("/"))
        linked = {m for m in re.findall(r'https://arxiv.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)', document)}
        if not identifiers and len(linked) == 1:
            identifiers.update(linked)
        if {re.sub(r"v\d+$", "", i) for i in linked} & {re.sub(r"v\d+$", "", i) for i in identifiers}:
            project_urls.append(url.rstrip("/"))
    if len(identifiers) != 1:
        raise ValueError("论文讲解需要明确一篇真实论文及原文；不能用其他研究演示或AI画面代替")
    identifier = next(iter(identifiers))
    directory = Path(directory).resolve() / identifier
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "paper.pdf"
    url = "https://arxiv.org/pdf/" + identifier
    if not target.is_file():
        # Fixed public host and canonical path; never follow a redirect off-host.
        with httpx.Client(timeout=45, follow_redirects=False) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 50_000_000:
                        raise ValueError("论文PDF超过50MB上限")
        if not data.startswith(b"%PDF-"):
            raise ValueError("未取得真实论文PDF，停止制作")
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
    import pypdfium2 as pdfium
    with PDF_LOCK, pdfium.PdfDocument(target) as pdf:
        if not 3 <= len(pdf) <= 80:
            raise ValueError("论文页数超出当前支持范围（3–80页），需要人工整理原文")
        pages = []
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            textpage.close()
            page.close()
            pages.append({"page": index + 1, "text": text})
    if sum(len(p["text"]) for p in pages) < 1000:
        raise ValueError("论文无法可靠提取正文，不能仅凭摘要讲实验")
    # Authors sometimes link a local PDF/OpenReview instead of arXiv on the
    # project site. Verify the forward link on the actual paper/abstract too.
    proof = {"pdf_first_pages": "".join(p["text"] for p in pages[:2])}
    if set(project_candidates) - set(project_urls):
        try:
            proof["arxiv_abstract"], _ = _fetch_url("https://arxiv.org/abs/" + identifier)
        except (ValueError, OSError, httpx.HTTPError):
            pass  # Missing forward evidence never upgrades an unrelated project.
    project_verification = []
    for candidate in project_candidates:
        for kind, original in proof.items():
            if _has_project_link(original, candidate):
                if candidate not in project_urls:
                    project_urls.append(candidate)
                project_verification.append({"project_url": candidate, "evidence_type": kind,
                    "evidence_url": "https://arxiv.org/abs/" + identifier if kind == "arxiv_abstract" else url})
                break
    result = {"id": identifier, "url": url, "path": str(target), "sha256": _sha256(target),
              "pages": pages, "verified_project_urls": project_urls, "project_verification": project_verification}
    (directory / "document.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def paper_assets(document, directory):
    """Rasterize authentic pages. Text is supplied for narration evidence checks."""
    from services.factory_montage import _sha256
    from services.factory_resources import ensure_memory_available
    import pypdfium2 as pdfium
    if _sha256(Path(document["path"])) != document["sha256"]:
        raise ValueError("论文原文发生变化，需要重新核对")
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    assets = []
    document_kind = document.get('kind', 'paper')
    if document_kind == 'official_document':
        label = '官方原文资料 ' + document.get('title', document['id'])
    elif document_kind == 'technical_report':
        label = '官方技术报告 ' + document.get('title', document['id'])
    else:
        label = f"论文原文 arXiv:{document['id']}"
    with PDF_LOCK, pdfium.PdfDocument(document["path"]) as pdf:
        for entry in document["pages"]:
            ensure_memory_available()
            index = entry["page"]
            path = directory / f"page-{index:03d}.png"
            page = pdf[index - 1]
            bitmap = page.render(scale=min(2.5, 1700 / page.get_width()))
            bitmap.to_pil().convert("RGB").save(path)
            bitmap.close()
            page.close()
            assets.append({"id": f"paper-{index}", "kind": "paper_page", "path": str(path),
                "paper_id": document["id"], "paper_page": index, "paper_path": document["path"],
                "paper_sha256": document["sha256"], "page_sha256": _sha256(path),
                "source_url": entry.get('source_url') or document["url"], "source_title": f"{label} · 第{index}页",
                "credit": f"{label} · 第{index}页", "document_kind": document.get("kind", "paper"),
                "description": entry["text"][:7000], "page_text": entry["text"],
                "clips": [{"start": 0, "end": 35}], "topic_tags": ["paper_original"]})
    return assets


def validate_paper_scenes(scenes, assets, evidence_sources=None):
    """Check page evidence, plus tightly attributed report announcement demos.

    The caller must supply the server's original source records separately from
    model-authored scenes. Quote/source matching is not a semantic review of the
    narration or proof that the depicted demonstration achieved its claims.
    """
    pages = {a["id"]: a for a in assets if a.get("kind") == "paper_page"}
    if not pages:
        raise ValueError("论文视频缺少真实论文页面，不能用背景演示替代")
    by_id = {a["id"]: a for a in assets}
    reports_only = all(a.get("document_kind") in {'technical_report', 'official_document'} for a in pages.values())
    seen, announcements = set(), []
    compact = lambda s: re.sub(r"\s+", "", s).lower()
    for scene_index, scene in enumerate(scenes):
        selected = [pages[i] for i in scene.get("asset_ids", []) if i in pages]
        if scene.get("evidence_kind") == "official_announcement":
            from services.factory_research import _validated_url

            if not reports_only:
                raise ValueError("官方公告演示依据仅适用于全部原文页均为官方技术报告或官方原文资料的内容")
            identifiers = scene.get("asset_ids")
            if (selected or not isinstance(identifiers, list) or not identifiers
                    or any(not isinstance(i, str) or i not in by_id
                           or by_id[i].get("kind") != "official_video" for i in identifiers)):
                raise ValueError("官方公告依据只能用于已存在的官方视频，不能替代原文页与高亮验证")
            source_id = scene.get("evidence_source_id")
            if not isinstance(source_id, str) or not source_id.strip() or not isinstance(evidence_sources, list):
                raise ValueError("官方公告演示需要调用方提供对应 source ID 的真实来源原文")
            matches = [s for s in evidence_sources if isinstance(s, dict) and s.get("id") == source_id]
            if len(matches) != 1 or not isinstance(matches[0].get("text"), str) or not matches[0]["text"].strip():
                raise ValueError("官方公告 source ID 必须唯一对应调用方提供的真实原文")
            source = matches[0]
            url = scene.get("evidence_url")
            if not isinstance(url, str) or not url or url != source.get("url"):
                raise ValueError("官方公告 evidence_url 必须与指定来源 URL 精确一致")
            if _validated_url(url) != url:
                raise ValueError("官方公告必须使用经域名校验的确切原文 URL")
            quote = scene.get("evidence_quote")
            normalize = lambda text: re.sub(r"\s+", " ", text).strip()
            if (not isinstance(quote, str) or not 12 <= len(quote.strip()) <= 240
                    or len(normalize(quote)) < 12 or normalize(quote) not in normalize(source["text"])):
                raise ValueError("官方公告 evidence_quote 必须为12–240字符并逐字存在于调用方原文")
            for identifier in identifiers:
                video = by_id[identifier]
                chain = video.get("source_chain")
                bound = (isinstance(chain, list) and all(isinstance(part, str) for part in chain)
                         and url in chain[:-1])
                if video.get("reference_source_url") != url and not bound:
                    raise ValueError("每个演示视频的引用或来源前链都必须对应同一官方公告，不能用不相关公告背书")
            announcements.append({"scene_index": scene_index, "source_id": source_id,
                "url": url, "asset_ids": list(identifiers),
                "source_text_sha256": hashlib.sha256(source["text"].encode("utf-8")).hexdigest(),
                "quote_sha256": hashlib.sha256(normalize(quote).encode("utf-8")).hexdigest()})
            continue
        if not selected:
            asset = next((a for a in pages.values() if a["paper_page"] == scene.get("paper_evidence_page")), None)
            if not asset:
                raise ValueError("作者演示镜头也需要paper_evidence_page对应原文页码")
        elif len(selected) != 1 or len(scene["asset_ids"]) != 1:
            raise ValueError("每段论文口播请选择一个明确的原文页面")
        else:
            asset = selected[0]
            if asset["paper_page"] in seen:
                raise ValueError("同一论文页不能反复回放凑时长；请合并同页讲解或选择新证据")
            seen.add(asset["paper_page"])
            highlights = scene.get("paper_highlights")
            if (not isinstance(highlights, list) or not 1 <= len(highlights) <= 8
                    or any(not isinstance(h, dict) or not isinstance(h.get("text"), str)
                           or not 2 <= len(h["text"]) <= 160 or not isinstance(h.get("narration_exact"), str)
                           or len(h["narration_exact"]) < 4 for h in highlights)):
                raise ValueError("论文页须提供与口播对应的原文高亮paper_highlights")
        quote = compact(str(scene.get("evidence_quote", "")))
        if len(quote) < 12 or quote not in compact(asset["page_text"]):
            raise ValueError("论文口播需提供所在页可核对的evidence_quote原文依据，不能编造实验")
        rect = scene.get("paper_focus", [0, 0, 1, 1])
        if (not isinstance(rect, list) or len(rect) != 4 or any(type(v) not in (int, float) for v in rect)
                or min(rect) < 0 or min(rect[2:]) < .15 or rect[0] + rect[2] > 1.001 or rect[1] + rect[3] > 1.001):
            raise ValueError("论文裁切区域无效")
    if len(seen) < 3 or scenes[0]["asset_ids"] != [next(k for k, a in pages.items() if a["paper_page"] == 1)]:
        raise ValueError("论文视频应先展示标题作者页，并至少展示三页不同的原文证据")
    result = {"paper_id": next(iter(pages.values()))["paper_id"], "pages": sorted(seen),
              "scope": "original_pdf_and_page_quotes; semantic_alignment_requires_editorial_review"}
    if all(a.get('document_kind') == 'official_document' for a in pages.values()):
        result.update(document_kind='official_document',
                      source_urls=list(dict.fromkeys(a['source_url'] for a in pages.values())))
    if announcements:
        result.update(official_announcement_evidence=announcements,
            announcement_evidence_scope="caller_supplied_original_source_exact_quote_and_video_provenance; not_semantic_review",
            announcement_evidence_note="公告原文逐字及视频来源关联校验，不等于口播语义或演示效果审核。")
    return result


def require_paper_publication(content, brief, media):
    if is_paper_story(brief, content.get("sources", [])) and not media.get("quality", {}).get("paper_evidence"):
        raise ValueError("暂缓提交：论文视频未展示对应论文原文与图表，需要按原文重做")


def highlight_rectangles(asset, text, focus=None):
    """Locate real PDF glyphs; never let a model invent a highlight's coordinates."""
    import pypdfium2 as pdfium
    if not isinstance(text, str) or not 2 <= len(text) <= 160:
        raise ValueError("高亮内容须为原文2–160字符")
    with PDF_LOCK, pdfium.PdfDocument(asset["paper_path"]) as pdf:
        page = pdf[asset["paper_page"] - 1]
        width, height = page.get_size()
        textpage = page.get_textpage()
        searcher = textpage.search(text)
        rectangles = []
        while found := searcher.get_next():
            textpage.count_rects()
            count = textpage.count_rects(*found)
            rectangles = []
            for index in range(count):
                left, bottom, right, top = textpage.get_rect(index)
                rectangles.append([left / width, 1 - top / height, (right - left) / width, (top - bottom) / height])
            if not focus or all(focus[0] <= x and focus[1] <= y and x+w <= focus[0]+focus[2]
                                and y+h <= focus[1]+focus[3] for x,y,w,h in rectangles):
                break
            rectangles = []
        merged = []
        for x, y, w, h in rectangles:
            if merged:
                px, py, pw, ph = merged[-1]
                if abs(y + h / 2 - py - ph / 2) <= max(h, ph) * .7 and px <= x <= px + pw + .02:
                    merged[-1] = [px, min(y, py), max(x + w, px + pw) - px, max(y + h, py + ph) - min(y, py)]
                    continue
            merged.append([x, y, w, h])
        rectangles = merged
        searcher.close()
        textpage.close()
        page.close()
    if not rectangles:
        raise ValueError("原论文当前取景中找不到需高亮的文字，不能画假证据框")
    return rectangles
