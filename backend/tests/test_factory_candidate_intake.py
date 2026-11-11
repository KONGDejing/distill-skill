"""Bounded candidate intake and rejection audit; no network/model/live database."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.factory import FactoryEvent, FactoryItem, FactoryRun
from services import factory_research as research, factory_service as service, factory_visuals as visuals


def source(identifier, **extra):
    return {"id": identifier, "title": identifier, "url": "https://openai.com/news/" + identifier,
            "text": "Original source paragraph. " * 20, "kind": "evergreen", **extra}


def asset(number):
    return {"id": f"asset-{number}", "sha256": f"{number:064x}", "description": "Verified official demonstration"}


def test_normal_intake_keeps_catalogue_tail_and_prioritizes_dated_model_news(monkeypatch):
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    news = [source(f"news-{i}", kind="news", published_at=now.isoformat()) for i in range(8)]
    news[-1].update(title="Introducing GPT-9 test fixture", published_at=(now - timedelta(days=10)).isoformat())
    papers = [source(f"paper-{i}", kind="paper", published_at=now.isoformat()) for i in range(4)]
    monkeypatch.setattr(research, "_utcnow", lambda: now)
    monkeypatch.setattr(research, "_load_feed", lambda *args: (news, None))
    monkeypatch.setattr(research, "_load_daily_papers", lambda now: (papers, None))
    monkeypatch.setattr(research, "_fetch_url", Mock(side_effect=RuntimeError("offline index fixture")))
    fetch = Mock(side_effect=lambda row: (dict(row), None))
    monkeypatch.setattr(research, "_load_source", fetch)
    result = research.collect_sources(18)
    urls = {row["url"] for row in result["sources"]}
    assert all(url in urls for _, url in research.VISUAL_SOURCE_URLS[-2:])
    assert news[-1]["url"] in urls
    assert result["large_model_search"]["source_ids"] == [news[-1]["id"]]
    assert sum(url in urls for _, url in research.VISUAL_SOURCE_URLS) <= 6
    assert any(row["kind"] == "news" for row in result["sources"])
    assert any(row["kind"] == "paper" for row in result["sources"])
    assert len(result["sources"]) <= 18 and fetch.call_count <= 18


def test_paper_project_slots_rotate_without_starving_catalogue_tail(monkeypatch):
    monkeypatch.setattr(research, "_load_feed", lambda *args: ([], None))
    monkeypatch.setattr(research, "_load_daily_papers", lambda now: ([], None))
    monkeypatch.setattr(research, "_fetch_url", Mock(side_effect=RuntimeError("offline index fixture")))
    monkeypatch.setattr(research, "_load_source", lambda row: (dict(row), None))
    seen = set()
    for day in range(len(research.VISUAL_SOURCE_URLS)):
        monkeypatch.setattr(research, "_utcnow", lambda day=day: datetime(2026, 11, 1, tzinfo=timezone.utc) + timedelta(days=day))
        result = research.collect_sources(12)
        assert len(result["sources"]) <= 12
        assert result["large_model_search"]["status"] == "gap"
        seen.update(row["url"] for row in result["sources"])
    assert all(url in seen for title, url in research.VISUAL_SOURCE_URLS if "论文作者" in title)
    assert all(url in seen for _, url in research.VISUAL_SOURCE_URLS[-2:])


def test_official_model_index_reads_at_most_two_articles_and_requires_original_date(monkeypatch):
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    index = 'https://www.anthropic.com/news'
    first = 'https://www.anthropic.com/claude-fixture-7'
    undated = 'https://www.anthropic.com/news/claude-fixture-8'
    pages = {
        index: '<a href="/claude-fixture-7">Claude Fixture 7</a><a href="/news/claude-fixture-8">Claude Fixture 8</a><a href="/news/claude-fixture-9">Claude Fixture 9</a>',
        first: '<title>Claude Fixture 7</title><main><time datetime="2026-11-09T00:00:00Z"></time>' + 'Official dated model evidence. ' * 20 + '</main>',
        undated: '<title>Claude Fixture 8</title><main>' + 'Undated model page. ' * 20 + '</main>',
    }
    fetch = Mock(side_effect=lambda url: (pages[url], url))
    monkeypatch.setattr(research, '_fetch_url', fetch)
    monkeypatch.setattr(research, '_utcnow', lambda: now)
    monkeypatch.setattr(research, '_load_feed', lambda *args: ([], None))
    monkeypatch.setattr(research, '_load_daily_papers', lambda now: ([], None))
    monkeypatch.setattr(research, '_load_source', lambda row: (dict(row), None))
    result = research.collect_sources(18)
    news = [row for row in result['sources'] if row['kind'] == 'news']
    assert len(news) == 1 and news[0]['url'] == first
    assert news[0]['published_at'] == '2026-11-09T00:00:00Z'
    assert [call.args[0] for call in fetch.call_args_list] == [index, first, undated]
    assert any('未取得近期明确日期' in warning for warning in result['warnings'])


def test_asset_ids_are_rejected_with_exact_source_id_hints():
    sources = [source("official-page", visual_ready=True, visual_assets=[asset(1)])]
    candidate = {"title": "Wrong ID namespace", "source_ids": ["asset-1"]}
    eligible, rows = service._eligible_candidates([candidate], sources, "tech_montage", with_diagnostics=True)
    assert eligible == []
    reason = rows[0]["reasons"][0]
    assert reason["code"] == "asset_ids_used_as_source_ids"
    assert reason["source_id_hints"] == {"asset-1": ["official-page"]}


def test_related_pages_can_supply_three_unique_originals_but_not_duplicate_or_uncited_assets():
    left = source("left", visual_assets=[asset(1), asset(2)])
    right = source("right", visual_assets=[asset(3)])
    unrelated = source("unrelated", visual_assets=[asset(4)])
    candidate = {"title": "Same official demonstration", "source_ids": ["left", "right"]}
    assert service._eligible_candidates([candidate], [left, right], "tech_montage") == [candidate]
    duplicate = {**right, "visual_assets": [asset(2)]}
    for sources in ([left, duplicate], [left, duplicate, unrelated]):
        eligible, rows = service._eligible_candidates([candidate], sources, "tech_montage", with_diagnostics=True)
        assert eligible == []
        assert rows[0]["reasons"][0]["verified_unique_assets"] == 2
        assert rows[0]["reasons"][0]["required"] == 3
    auxiliary = {"title": "With supporting picture", "source_ids": ["left"], "visual_source_ids": ["right"]}
    assert service._eligible_candidates([auxiliary], [left, right], "tech_montage") == []


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setattr(service, "FACTORY_DIR", tmp_path / "factory")
    monkeypatch.setattr(service, "ensure_memory_available", lambda: None)
    yield factory
    engine.dispose()


def seed(sessions, sources, *, style="tech_montage", target=1):
    with sessions() as db:
        run = FactoryRun(target_count=target, sources=deepcopy(sources), settings_snapshot={"visual_style": style})
        db.add(run); db.commit()
        return run.id


def test_preflight_reaches_new_newsroom_handoff_after_old_catalogue(sessions, monkeypatch):
    old = [source(f"old-{i}", url=url) for i, (_, url) in enumerate(research.VISUAL_SOURCE_URLS)]
    fresh = source("new-hardware", kind="news", editor_id="producer3")
    run_id = seed(sessions, [*old, fresh])
    fetched = []
    def collect(rows, path, *, limit):
        fetched.append(rows[0]["id"])
        assert limit == 3
        return {"status": "ready", "assets": [asset(1), asset(2), asset(3)]}
    monkeypatch.setattr(visuals, "collect_visuals", collect)
    candidate = {"title": "Hardware announcement", "category": "科技热点", "source_ids": [fresh["id"]]}
    calls = Mock(side_effect=[{"candidates": [candidate]}, {"selected": [{"candidate_index": 0}]}])
    monkeypatch.setattr(service, "call_agent", calls)
    service._make_plan(run_id)
    assert fresh["id"] in fetched[:3] and len(fetched) <= 6
    payload = calls.call_args_list[0].args[2]
    assert "source_id" in payload["source_id_contract"] and "asset_id" in payload["source_id_contract"]
    assert next(row for row in payload["source_catalog"] if row["source_id"] == fresh["id"])["editor_hint"] == "producer3"
    with sessions() as db:
        run = db.get(FactoryRun, run_id)
        assert any(row["visual_preflight"]["status"] == "not_checked" for row in run.sources)
        event = db.query(FactoryEvent).filter_by(run_id=run_id, stage="candidate_validation").one()
        assert event.detail["raw_candidates"] == [candidate]
        assert event.detail["diagnostics"][0]["accepted"] is True
        assert db.query(FactoryItem).filter_by(run_id=run_id).count() == 1


def test_partial_per_page_preflight_preserves_valid_joint_evidence(sessions, monkeypatch):
    sources = [source("left", kind="news"), source("right", kind="news")]
    run_id = seed(sessions, sources)
    monkeypatch.setattr(visuals, "collect_visuals", lambda rows, path, limit: {"status": "insufficient",
        "assets": [asset(1), asset(2)] if rows[0]["id"] == "left" else [asset(3)]})
    candidate = {"title": "Joint evidence", "category": "科技热点", "source_ids": ["left", "right"]}
    monkeypatch.setattr(service, "call_agent", Mock(side_effect=[{"candidates": [candidate]}, {"selected": [{"candidate_index": 0}]}]))
    service._make_plan(run_id)
    with sessions() as db:
        run = db.get(FactoryRun, run_id)
        assert all(row["visual_ready"] is False for row in run.sources)
        assert run.plan["candidates"] == [candidate]


def test_material_only_rejection_is_saved_without_model_retry(sessions, monkeypatch):
    run_id = seed(sessions, [source("empty"), source("other")])
    monkeypatch.setattr(visuals, "collect_visuals", lambda rows, path, limit: {"status": "insufficient", "assets": []}
        if rows[0]["id"] == "empty" else {"status": "ready", "assets": [asset(1), asset(2), asset(3)]})
    candidate = {"title": "No matching visual evidence", "source_ids": ["empty"]}
    calls = Mock(return_value={"candidates": [candidate]})
    monkeypatch.setattr(service, "call_agent", calls)
    with pytest.raises(ValueError, match="有效选题0"):
        service._make_plan(run_id)
    assert calls.call_count == 1
    with sessions() as db:
        event = db.query(FactoryEvent).filter_by(run_id=run_id, stage="candidate_validation").one()
        assert event.detail["raw_candidates"] == [candidate]
        assert event.detail["diagnostics"][0]["reasons"][0]["code"] == "insufficient_verified_visuals"
        assert db.query(FactoryItem).count() == 0


def test_id_format_retry_has_exact_feedback_and_stops_after_two_attempts(sessions, monkeypatch):
    run_id = seed(sessions, [source("page", visual_assets=[asset(1)])], style="cards")
    candidate = {"title": "Mislabelled reference", "source_ids": ["asset-1"]}
    calls = Mock(return_value={"candidates": [candidate]})
    monkeypatch.setattr(service, "call_agent", calls)
    with pytest.raises(ValueError, match="有效选题0"):
        service._make_plan(run_id)
    assert calls.call_count == 2
    feedback = calls.call_args_list[1].args[2]["candidate_feedback"]
    assert feedback[0]["reasons"][0]["source_id_hints"] == {"asset-1": ["page"]}
    with sessions() as db:
        events = db.query(FactoryEvent).filter_by(run_id=run_id, stage="candidate_validation").all()
        assert len(events) == 2 and all(event.detail["raw_candidates"] == [candidate] for event in events)
        assert db.query(FactoryItem).count() == 0


def test_coverage_uses_cited_original_evidence_not_model_tags_or_edge_app_labels(sessions, monkeypatch):
    run_id = seed(sessions, [source("phonon", title="Phonon端侧语音合成", text="On-device speech synthesis application; runs offline on Apple M4 CPU and NPU at 40 TOPS.")], style="cards")
    candidate = {"title": "Phonon端侧语音合成应用", "category": "AI工具应用", "source_ids": ["phonon"],
                 "topics": ["大模型", "端侧芯片", "算力基础设施"]}
    calls = Mock(side_effect=[{"candidates": [candidate]}, {"selected": [{"candidate_index": 0}]}])
    monkeypatch.setattr(service, "call_agent", calls)
    service._make_plan(run_id)
    with sessions() as db:
        coverage = db.get(FactoryRun, run_id).plan["topic_coverage"]
        assert coverage["candidate_topics"]["AI工具应用"]
        assert {"大模型", "端侧芯片", "算力基础设施"} <= set(coverage["candidate_gaps"])
        assert {"大模型", "端侧芯片", "算力基础设施"} <= set(coverage["source_gaps"])
        event = db.query(FactoryEvent).filter_by(run_id=run_id, stage="topic_coverage").one()
        assert len(event.detail["source_topics"]) == 7
    assert "每日必须主动检查通用大模型" in calls.call_args_list[0].args[2]["coverage_instruction"]


def test_one_candidate_can_cover_multiple_topics_only_with_cited_text(sessions, monkeypatch):
    record = source("model-chip", title="GPT-9 model release and Snapdragon 8 fixture chip architecture", kind="news",
        published_at="2026-11-11T00:00:00Z", text="Introducing GPT-9 fixture model. The Snapdragon 8 fixture chip has 3 nm silicon and 40 TOPS.")
    run_id = seed(sessions, [record], style="cards")
    candidate = {"title": "大语言模型与Snapdragon 8芯片", "source_ids": [record["id"]]}
    monkeypatch.setattr(service, "call_agent", Mock(side_effect=[{"candidates": [candidate]}, {"selected": [{"candidate_index": 0}]}]))
    service._make_plan(run_id)
    with sessions() as db:
        coverage = db.get(FactoryRun, run_id).plan["topic_coverage"]
        assert coverage["candidate_topics"]["大模型"][0]["candidate_index"] == 0
        assert coverage["candidate_topics"]["端侧芯片"][0]["candidate_index"] == 0
        assert "40 TOPS" in coverage["source_topics"]["端侧芯片"][0]["evidence"]


def test_generic_gemini_or_world_model_is_not_large_model_evolution(sessions, monkeypatch):
    record = source("genie", title="Genie world model uses Gemini", text="This world model demonstration mentions Gemini and model performance.",
                    published_at="2026-11-11T00:00:00Z", kind="news")
    run_id = seed(sessions, [record], style="cards")
    candidate = {"title": "Gemini模型世界演示", "source_ids": [record["id"]], "topics": ["大模型"]}
    monkeypatch.setattr(service, "call_agent", Mock(side_effect=[{"candidates": [candidate]}, {"selected": [{"candidate_index": 0}]}]))
    service._make_plan(run_id)
    with sessions() as db:
        coverage = db.get(FactoryRun, run_id).plan["topic_coverage"]
        assert "大模型" in coverage["source_gaps"] and "大模型" in coverage["candidate_gaps"]
