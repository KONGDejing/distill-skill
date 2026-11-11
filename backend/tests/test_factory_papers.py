from copy import deepcopy

import pytest

from services.factory_papers import is_paper_story, require_paper_publication, validate_paper_scenes


def evidence():
    assets = [{"id": f"paper-{i}", "kind": "paper_page", "paper_id": "test",
               "paper_page": i, "page_text": f"Original scientific evidence on page {i}, not generated."}
              for i in [1, 4, 9]]
    scenes = [{"asset_ids": [a["id"]], "evidence_quote": a["page_text"], "paper_focus": [0, 0, 1, 1],
               "paper_highlights": [{"text": "Original scientific evidence", "narration_exact": "对应论文原文"}]}
              for a in assets]
    return scenes, assets


def test_paper_contract_requires_identifiable_original_and_matching_page_quotes():
    scenes, assets = evidence()
    assert validate_paper_scenes(scenes, assets)["pages"] == [1, 4, 9]
    missing = deepcopy(scenes)
    missing[1]["evidence_quote"] = "An invented result which is not present on this page."
    with pytest.raises(ValueError, match="原文依据"):
        validate_paper_scenes(missing, assets)
    with pytest.raises(ValueError, match="标题作者"):
        validate_paper_scenes(scenes[::-1], assets)
    with pytest.raises(ValueError, match="不能反复回放"):
        validate_paper_scenes([*scenes, scenes[0]], assets)


def test_paper_demos_need_a_page_reference_and_crops_cannot_leave_original():
    scenes, assets = evidence()
    demo = {"asset_ids": ["demo"], "paper_evidence_page": 9, "evidence_quote": assets[-1]["page_text"]}
    assert validate_paper_scenes([*scenes, demo], assets)
    demo["paper_evidence_page"] = 20
    with pytest.raises(ValueError, match="原文页码"):
        validate_paper_scenes([*scenes, demo], assets)
    scenes[1]["paper_focus"] = [.8, 0, .5, .5]
    with pytest.raises(ValueError, match="裁切区域"):
        validate_paper_scenes(scenes, assets)


def announcement_evidence():
    scenes, assets = evidence()
    for asset in assets:
        asset["document_kind"] = "technical_report"
    url = "https://deepmind.google/blog/official-model-announcement/"
    quote = "The demonstration uses an explicitly documented task."
    sources = [{"id": "official-release", "url": url,
                "text": "Original announcement.\nThe demonstration uses an explicitly\n documented task.\nMore context."}]
    assets.extend([{"id": "demo-direct", "kind": "official_video", "reference_source_url": url},
                   {"id": "demo-chain", "kind": "official_video",
                    "source_chain": ["https://deepmind.google/models/gemini-robotics/", url,
                                     "https://storage.googleapis.com/gdm-deepmind-com-prod-public/media/demo.webm"]}])
    scenes.append({"asset_ids": ["demo-direct", "demo-chain"], "evidence_kind": "official_announcement",
                   "evidence_source_id": "official-release", "evidence_url": url, "evidence_quote": quote})
    return scenes, assets, sources


def test_report_demo_accepts_original_announcement_quote_and_records_only_evidence_hashes():
    scenes, assets, sources = announcement_evidence()
    original = deepcopy((scenes, assets, sources))
    result = validate_paper_scenes(scenes, assets, evidence_sources=sources)
    assert result["pages"] == [1, 4, 9]
    proof = result["official_announcement_evidence"][0]
    assert proof["asset_ids"] == ["demo-direct", "demo-chain"]
    assert proof["source_id"] == "official-release" and proof["url"] == sources[0]["url"]
    assert len(proof["source_text_sha256"]) == len(proof["quote_sha256"]) == 64
    assert "not_semantic_review" in result["announcement_evidence_scope"]
    assert "不等于口播语义" in result["announcement_evidence_note"]
    assert "text" not in proof and "quote" not in proof
    assert (scenes, assets, sources) == original


@pytest.mark.parametrize("bad", ["ordinary-paper", "mixed-document", "page-scene", "missing-page-highlights"])
def test_announcement_branch_cannot_relax_paper_or_report_page_contracts(bad):
    scenes, assets, sources = announcement_evidence()
    if bad == "ordinary-paper":
        for asset in assets[:3]:
            asset.pop("document_kind")
    elif bad == "mixed-document":
        assets[1]["document_kind"] = "paper"
    elif bad == "page-scene":
        scenes[-1]["asset_ids"] = ["paper-9"]
    else:
        scenes[1].pop("paper_highlights")
    with pytest.raises(ValueError):
        validate_paper_scenes(scenes, assets, evidence_sources=sources)


@pytest.mark.parametrize("bad", ["none", "scene-self-proof", "missing-text", "unknown-id", "duplicate-id"])
def test_model_authored_proof_cannot_substitute_for_caller_supplied_original_text(bad):
    scenes, assets, sources = announcement_evidence()
    if bad == "none":
        sources = None
    elif bad == "scene-self-proof":
        scenes[-1].update(proof=True, evidence_sources=sources)
        sources = None
    elif bad == "missing-text":
        sources[0].pop("text")
    elif bad == "unknown-id":
        scenes[-1]["evidence_source_id"] = "invented-id"
    else:
        sources.append(deepcopy(sources[0]))
    with pytest.raises(ValueError, match="原文"):
        validate_paper_scenes(scenes, assets, evidence_sources=sources)


@pytest.mark.parametrize("bad", ["url-mismatch", "untrusted-host", "invented-quote", "changed-case", "short-quote", "long-quote"])
def test_announcement_requires_exact_url_and_bounded_verbatim_quote(bad):
    scenes, assets, sources = announcement_evidence()
    if bad == "url-mismatch":
        scenes[-1]["evidence_url"] += "?different=1"
    elif bad == "untrusted-host":
        scenes[-1]["evidence_url"] = sources[0]["url"] = "https://example.org/announcement/"
    elif bad == "invented-quote":
        scenes[-1]["evidence_quote"] = "The demonstration proves every robot is perfectly reliable."
    elif bad == "changed-case":
        scenes[-1]["evidence_quote"] = scenes[-1]["evidence_quote"].upper()
    elif bad == "short-quote":
        scenes[-1]["evidence_quote"] = "uses"
    else:
        scenes[-1]["evidence_quote"] = sources[0]["text"] = "x" * 241
    with pytest.raises(ValueError):
        validate_paper_scenes(scenes, assets, evidence_sources=sources)


@pytest.mark.parametrize("bad", ["unrelated-direct", "unrelated-chain", "only-last-chain-url", "unknown-video", "non-video", "empty-selection"])
def test_every_selected_video_must_be_bound_to_the_same_announcement(bad):
    scenes, assets, sources = announcement_evidence()
    if bad == "unrelated-direct":
        assets[3]["reference_source_url"] = "https://deepmind.google/blog/unrelated-announcement/"
    elif bad == "unrelated-chain":
        assets[4]["source_chain"][1] = "https://deepmind.google/blog/unrelated-announcement/"
    elif bad == "only-last-chain-url":
        assets[4]["source_chain"] = ["https://deepmind.google/models/gemini-robotics/", sources[0]["url"]]
    elif bad == "unknown-video":
        scenes[-1]["asset_ids"].append("invented-video")
    elif bad == "non-video":
        assets[4]["kind"] = "schematic_animation"
    else:
        scenes[-1]["asset_ids"] = []
    with pytest.raises(ValueError):
        validate_paper_scenes(scenes, assets, evidence_sources=sources)


def test_project_only_paper_story_also_cannot_publish_background_as_evidence():
    sources = [{"url": "https://roboforpc.github.io/", "kind": "evergreen"}]
    assert is_paper_story({}, sources)
    with pytest.raises(ValueError, match="论文原文"):
        require_paper_publication({"sources": sources}, {}, {"quality": {"dynamic_coverage": 1}})


def test_highlight_boxes_are_extracted_from_real_pdf_glyphs(tmp_path):
    from services.factory_papers import highlight_rectangles
    # A small real PDF with known text, no network or model-generated coordinates.
    stream = b"BT /F1 18 Tf 50 700 Td (Original result: 68.2 percent) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    path = tmp_path / "original.pdf"
    path.write_bytes(data)
    asset = {"paper_path": str(path), "paper_page": 1}
    boxes = highlight_rectangles(asset, "68.2")
    assert boxes and all(0 < x < .6 and .09 < y < .15 and w > 0 and h > 0 for x, y, w, h in boxes)
    for text, focus in [("invented improvement", None), ("68.2", [0, .5, 1, .5])]:
        with pytest.raises(ValueError, match="找不到"):
            highlight_rectangles(asset, text, focus)


def test_tool_documentation_is_not_mislabeled_as_a_paper():
    sources=[{'url':'https://allenai.github.io/ToyBlocks/','kind':'evergreen'}]
    assert not is_paper_story({'category':'工具指南'},sources)
    assert is_paper_story({'category':'论文讲解'},sources)


def test_project_forward_link_accepts_pdf_line_wrap_but_not_similar_host_or_path():
    from services.factory_papers import _has_project_link
    project='https://ai4ce.github.io/VICTR/'
    assert _has_project_link('Code: https://ai4ce.github.io/\nVICTR/',project)
    assert _has_project_link('<a href="https://ai4ce.github.io/VICTR/">Project</a>',project)
    assert not _has_project_link('https://fake-ai4ce.github.io/VICTR/',project)
    assert not _has_project_link('https://ai4ce.github.io/VICTR-other/',project)
