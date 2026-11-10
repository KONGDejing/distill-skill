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
