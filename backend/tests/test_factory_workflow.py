"""Workflow invariants, tested without a model, renderer, broker or live database."""
import json
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database import Base
from models.factory import FactoryConfig, FactoryEvent, FactoryItem, FactoryRun
from services import factory_llm, factory_service, factory_resources


PLATFORMS = ["douyin", "xiaohongshu", "kuaishou", "bilibili"]
SOURCE = {
    "id": "official-source", "title": "Official tutorial",
    "url": "https://huggingface.co/docs/transformers/en/pipeline_tutorial",
    "published_at": None, "fetched_at": "2026-11-09T00:00:00Z",
    "text": "A source document for the independently reviewed tutorial.",
    "kind": "evergreen",
}


@pytest.fixture
def isolated_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(factory_resources, "memory_state", lambda **kwargs: {"blocked": False})
    engine = create_engine(f"sqlite:///{tmp_path / 'workflow-test.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[FactoryConfig.__table__, FactoryRun.__table__,
                                            FactoryItem.__table__, FactoryEvent.__table__])
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(factory_service, "SessionLocal", sessions)
    monkeypatch.setattr(factory_llm, "SessionLocal", sessions)
    monkeypatch.setattr(factory_service, "FACTORY_DIR", tmp_path / "factory")
    monkeypatch.setattr(factory_service, "queue_available", lambda: True)
    yield sessions
    engine.dispose()


def _seed_item(sessions, **updates):
    with sessions() as db:
        run = FactoryRun(target_count=1, sources=[SOURCE], settings_snapshot={
            "max_revisions": 1, "max_llm_calls": 12, "platforms": PLATFORMS,
        })
        db.add(run)
        db.flush()
        item = FactoryItem(run_id=run.id, position=0, brief={
            "title": "一个有来源的 AI 教程", "source_ids": [SOURCE["id"]],
            "category": "实操教程",
        }, **updates)
        db.add(item)
        db.commit()
        return run.id, item.id


def test_malformed_script_is_returned_to_writer_with_bounded_feedback(isolated_factory, monkeypatch):
    run_id, item_id = _seed_item(isolated_factory)
    valid = {"title": "有出处的指南", "script": "这是来源支持的介绍，建议关注具体使用边界。" * 6,
             "source_ids": [SOURCE["id"]]}
    calls = Mock(side_effect=[{"script": "太短"}, valid, {"approved": True, "score": 90}])
    monkeypatch.setattr(factory_service, "call_agent", calls)
    factory_service._write_and_review(run_id, item_id)
    assert [call.args[1] for call in calls.call_args_list] == ["writer", "writer", "reviewer"]
    assert "80–650" in calls.call_args_list[1].args[2]["review_feedback"]["reason"]
    assert calls.call_args_list[2].args[2]["task_mode"] == "script_review"
    with isolated_factory() as db:
        assert db.get(FactoryItem, item_id).review["approved"] is True


def test_json_extraction_preserves_nested_objects_and_rejects_non_json():
    text = '模型输出：\n```json\n{"approved": false, "issues": [{"reason": "缺少 {来源}"}]}\n```'
    assert factory_llm.parse_json(text) == {
        "approved": False, "issues": [{"reason": "缺少 {来源}"}],
    }
    with pytest.raises(ValueError, match="JSON"):
        factory_llm.parse_json("本次没有有效的结构化结果，只有 { 一个括号")


def test_source_validation_rejects_mixed_invented_ids_and_wrong_shape():
    assert factory_service._sources_for([SOURCE["id"]], [SOURCE]) == [SOURCE]
    for source_ids in (["invented"], [SOURCE["id"], "invented"], SOURCE["id"], []):
        with pytest.raises(ValueError):
            factory_service._sources_for(source_ids, [SOURCE])


def test_single_active_lane_and_daily_schedule_are_database_invariants(isolated_factory):
    sessions = isolated_factory
    with sessions() as db:
        run = FactoryRun(schedule_key="2026-11-09")
        db.add(run)
        db.commit()
        # Application-level rejection must not enqueue a second run.
        with pytest.raises(ValueError, match="已有"):
            factory_service.start_run(db, target_count=3)
        # Even a racing insert that bypasses start_run cannot occupy the lane.
        db.add(FactoryRun())
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        run.active_slot = None
        run.status = "completed"
        db.commit()
        # Releasing the lane does not allow a second run for the same daily key.
        db.add(FactoryRun(schedule_key="2026-11-09"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(FactoryRun(schedule_key="2026-11-10"))
        db.commit()
        assert db.query(FactoryRun).count() == 2


def test_rejected_script_never_reaches_rendering_or_packaging(isolated_factory, monkeypatch):
    import redis

    run_id, item_id = _seed_item(isolated_factory)
    called_agents = []

    def fake_agent(_run_id, agent_id, payload, **kwargs):
        called_agents.append(agent_id)
        if agent_id == "writer":
            return {"title": "有待核实的脚本", "script": "这是测试脚本，关键事实必须核验。" * 8,
                    "source_ids": [SOURCE["id"]], "scenes": []}
        if agent_id == "reviewer":
            # A high score must never override an explicit rejection.
            return {"approved": False, "score": 95, "reason": "关键事实缺少证据", "issues": ["缺少证据"]}
        if agent_id == "analyst":
            return {"summary": "零条通过审核，未生成视频。", "recommendations": []}
        raise AssertionError(f"Unexpected agent: {agent_id}")

    lock = Mock()
    lock.acquire.return_value = True
    client = Mock()
    client.lock.return_value = lock
    monkeypatch.setattr(redis.Redis, "from_url", Mock(return_value=client))
    monkeypatch.setattr(factory_service, "_make_plan", lambda _run_id: None)
    monkeypatch.setattr(factory_service, "call_agent", fake_agent)
    render, package = Mock(), Mock()
    monkeypatch.setattr(factory_service, "_render", render)
    monkeypatch.setattr(factory_service, "_package", package)

    factory_service.execute_run(run_id)

    render.assert_not_called()
    package.assert_not_called()
    assert called_agents.count("writer") == 2
    assert called_agents.count("reviewer") == 2
    with isolated_factory() as db:
        item, run = db.get(FactoryItem, item_id), db.get(FactoryRun, run_id)
        assert item.status == "failed"
        assert "返工上限" in item.error
        assert run.status == "failed"
        assert run.completed_count == 0
        assert run.active_slot is None
        assert run.report["production"]["published"] == 0
    lock.release.assert_called_once()


def test_four_platform_archives_contain_assets_and_cannot_claim_publication(isolated_factory, monkeypatch):
    run_id, item_id = _seed_item(isolated_factory, content={
        "title": "AI 工具使用指南", "script": "这是经过审核的测试文案。", "sources": [SOURCE],
    })
    directory = factory_service.FACTORY_DIR / run_id / item_id
    directory.mkdir(parents=True)
    media = {}
    file_names = {"video_path": "video.mp4", "audio_path": "audio.mp3",
                  "cover_path": "cover.png", "subtitle_path": "subtitles.ass"}
    for field, filename in file_names.items():
        path = directory / filename
        path.write_bytes(f"fixture-only:{filename}".encode())
        media[field] = str(path)
    factory_service._save_item(item_id, media=media)
    variants = [{"platform": platform, "title": f"{platform} 标题", "caption": "平台简介",
                 "tags": ["AI"], "published": True, "status": "published"} for platform in PLATFORMS]
    model = Mock(return_value={"platforms": variants})
    monkeypatch.setattr(factory_service, "call_agent", model)

    factory_service._package(run_id, item_id)
    # Redelivery must reuse complete archives instead of calling the model again.
    factory_service._package(run_id, item_id)
    assert model.call_count == 1

    with isolated_factory() as db:
        item = db.get(FactoryItem, item_id)
        assert item.status == "completed"
        assert {package["platform"] for package in item.packages} == set(PLATFORMS)
        for package in item.packages:
            assert package["status"] == "package_ready"
            assert package.get("published", False) is False
            assert Path(package["path"]).is_file()
            with zipfile.ZipFile(package["path"]) as archive:
                assert set(file_names.values()).issubset(archive.namelist())
                assert archive.read("video.mp4") == b"fixture-only:video.mp4"
                metadata = json.loads(archive.read("metadata.json"))
                assert metadata["platform"] == package["platform"]
                assert metadata["published"] is False
                assert metadata["status"] == "package_ready"
                assert archive.read("script.txt").decode() == "这是经过审核的测试文案。"


def test_model_budget_stops_before_any_network_request(isolated_factory, monkeypatch):
    run_id, _ = _seed_item(isolated_factory)
    with isolated_factory() as db:
        db.get(FactoryRun, run_id).llm_calls = 12
        db.commit()
    client = Mock(side_effect=AssertionError("A model client must not be created after budget exhaustion"))
    monkeypatch.setattr(factory_llm.anthropic, "Anthropic", client)
    with pytest.raises(RuntimeError, match="上限"):
        factory_llm.call_agent(run_id, "writer", {})
    client.assert_not_called()


def test_replacement_excludes_assigned_and_rejected_topics_and_only_happens_once(isolated_factory, monkeypatch):
    run_id, item_id = _seed_item(isolated_factory, content={"script": "被拒绝的旧文案"},
                                 review={"approved": False, "reason": "缺少事实证据"}, revisions=1)
    current_title = "一个有来源的 AI 教程"
    assigned_title, rejected_title = "已安排给另一位置", "此前已被否决"
    reserve_titles = ["有证据的备用教程", "另一条可用备用教程"]
    with isolated_factory() as db:
        db.get(FactoryRun, run_id).plan = {
            "candidates": [{"title": title, "source_ids": [SOURCE["id"]]}
                           for title in [current_title, assigned_title, rejected_title, *reserve_titles]],
            "rejected_titles": [rejected_title],
        }
        db.add(FactoryItem(run_id=run_id, position=1,
                           brief={"title": assigned_title, "source_ids": [SOURCE["id"]]}))
        db.commit()
    decisions = []

    def choose_reserve(actual_run_id, agent_id, payload, **kwargs):
        decisions.append(payload)
        assert actual_run_id == run_id
        assert agent_id == "director"
        return {"selected": [{"candidate_index": 0}], "reason": "备用选题证据充分"}

    monkeypatch.setattr(factory_service, "call_agent", choose_reserve)
    assert factory_service._replace_rejected_topic(run_id, item_id) is True
    assert [candidate["title"] for candidate in decisions[0]["candidates"]] == reserve_titles
    assert set(decisions[0]["recent_titles"]) == {current_title, assigned_title, rejected_title}
    assert decisions[0]["rejected_title"] == current_title
    assert decisions[0]["review_feedback"]["reason"] == "缺少事实证据"
    with isolated_factory() as db:
        item, run = db.get(FactoryItem, item_id), db.get(FactoryRun, run_id)
        assert item.brief["title"] == reserve_titles[0]
        assert item.brief["replacement_count"] == 1
        assert item.content == item.review == {}
        assert item.revisions == 0
        assert item.status == "planned"
        assert set(run.plan["rejected_titles"]) == {current_title, rejected_title}
    # A remaining reserve does not permit another substitution at this position.
    assert factory_service._replace_rejected_topic(run_id, item_id) is False
    assert len(decisions) == 1


def test_rejected_replacement_stops_before_rendering_even_with_more_reserves(isolated_factory, monkeypatch):
    import redis

    run_id, item_id = _seed_item(isolated_factory)
    with isolated_factory() as db:
        db.get(FactoryRun, run_id).plan = {
            "candidates": [{"title": title, "source_ids": [SOURCE["id"]]}
                           for title in ["一个有来源的 AI 教程", "备用一", "备用二"]],
        }
        db.commit()
    calls = []

    def fake_agent(_run_id, agent_id, payload, **kwargs):
        calls.append((agent_id, payload))
        if agent_id == "director":
            return {"selected": [{"candidate_index": 0}]}
        if agent_id == "analyst":
            return {"summary": "原选题及一次备用选题均未通过审核，停止该位置生产。", "recommendations": []}
        raise AssertionError(f"Unexpected agent {agent_id}")

    lock = Mock()
    lock.acquire.return_value = True
    client = Mock()
    client.lock.return_value = lock
    monkeypatch.setattr(redis.Redis, "from_url", Mock(return_value=client))
    monkeypatch.setattr(factory_service, "_make_plan", lambda _run_id: None)
    monkeypatch.setattr(factory_service, "call_agent", fake_agent)
    review = Mock(side_effect=factory_service.ReviewRejected("选题返工后仍缺少事实证据"))
    render, package = Mock(), Mock()
    monkeypatch.setattr(factory_service, "_write_and_review", review)
    monkeypatch.setattr(factory_service, "_render", render)
    monkeypatch.setattr(factory_service, "_package", package)

    factory_service.execute_run(run_id)

    assert review.call_count == 2  # Original topic plus one substitute, no third attempt.
    assert sum(agent_id == "director" for agent_id, _ in calls) == 1
    render.assert_not_called()
    package.assert_not_called()
    with isolated_factory() as db:
        item, run = db.get(FactoryItem, item_id), db.get(FactoryRun, run_id)
        assert item.status == "failed"
        assert item.brief["title"] == "备用一"
        assert item.brief["replacement_count"] == 1
        assert run.status == "failed"
        assert run.completed_count == 0
        assert run.active_slot is None


def test_resume_completed_video_recovers_submission_without_rendering(isolated_factory, monkeypatch):
    import redis
    from services import factory_publishing

    run_id, item_id = _seed_item(isolated_factory, status="completed")
    with isolated_factory() as db:
        run = db.get(FactoryRun, run_id)
        run.settings_snapshot = {**run.settings_snapshot, "auto_publish_xiaohongshu": True}
        db.commit()
    lock = Mock()
    lock.acquire.return_value = True
    client = Mock()
    client.lock.return_value = lock
    monkeypatch.setattr(redis.Redis, "from_url", Mock(return_value=client))
    monkeypatch.setattr(factory_service, "_make_plan", lambda _run_id: None)
    monkeypatch.setattr(factory_service, "call_agent", lambda *args, **kwargs: {"summary": "已恢复提交阶段"})
    render, review, package, submit = Mock(), Mock(), Mock(), Mock()
    monkeypatch.setattr(factory_service, "_write_and_review", review)
    monkeypatch.setattr(factory_service, "_render", render)
    monkeypatch.setattr(factory_service, "_package", package)
    monkeypatch.setattr(factory_publishing, "auto_submit", submit)

    factory_service.execute_run(run_id)

    review.assert_not_called()
    render.assert_not_called()
    package.assert_not_called()
    submit.assert_called_once_with(item_id, run_id)
    with isolated_factory() as db:
        run = db.get(FactoryRun, run_id)
        assert run.status == "completed"
        assert "完成 1/1 条原创视频" in run.report["summary"]
        assert "已恢复提交阶段" not in run.report["summary"]


def test_montage_without_real_footage_stops_before_speech_or_card_fallback(isolated_factory, monkeypatch):
    import sys
    from types import SimpleNamespace
    from services import factory_media

    run_id, item_id = _seed_item(isolated_factory, content={"title": "动态演示", "script": "真实脚本", "sources": [SOURCE]})
    with isolated_factory() as db:
        run = db.get(FactoryRun, run_id)
        run.settings_snapshot = {**run.settings_snapshot, "visual_style": "tech_montage"}
        db.commit()
    collector = Mock(return_value={"status": "insufficient", "assets": []})
    monkeypatch.setitem(sys.modules, "services.factory_visuals", SimpleNamespace(collect_visuals=collector))
    director, cards = Mock(), Mock()
    monkeypatch.setattr(factory_service, "call_agent", director)
    monkeypatch.setattr(factory_media, "render_factory_video", cards)
    with pytest.raises(ValueError, match="动态演示素材不足"):
        factory_service._render(run_id, item_id)
    collector.assert_called_once()
    director.assert_not_called()
    cards.assert_not_called()
    with isolated_factory() as db:
        assert not db.get(FactoryItem, item_id).media


def _fake_lock(monkeypatch):
    import redis
    lock = Mock()
    lock.acquire.return_value = True
    client = Mock()
    client.lock.return_value = lock
    monkeypatch.setattr(redis.Redis, 'from_url', Mock(return_value=client))


def test_memory_pressure_before_start_records_pause_without_enqueuing(isolated_factory, monkeypatch):
    from tasks import factory_tasks
    enqueue = Mock()
    monkeypatch.setattr(factory_tasks.produce_run, 'delay', enqueue)
    monkeypatch.setattr(factory_resources, 'memory_state', lambda **kwargs: {'blocked': True, 'reason': '工程内存达到上限'})
    with isolated_factory() as db:
        run = factory_service.start_run(db, target_count=1)
        assert run.status == 'paused_memory' and run.active_slot == 'factory'
        assert run.llm_calls == 0 and run.finished_at is None
        with pytest.raises(ValueError, match='内存暂停'):
            factory_service.start_run(db, target_count=1)
    enqueue.assert_not_called()


def test_memory_pause_preserves_delivery_review_and_lane_until_explicit_resume(isolated_factory, monkeypatch):
    from fastapi import HTTPException
    from routers import factory as router
    from tasks import factory_tasks
    from datetime import datetime, timedelta

    run_id, completed_id = _seed_item(isolated_factory, status='completed',
        content={'script': '已完成文案'}, media={'video_path': 'existing-reviewed-video.mp4'},
        packages=[{'platform': 'xiaohongshu', 'status': 'submitted'}])
    with isolated_factory() as db:
        run = db.get(FactoryRun, run_id)
        run.target_count = 2
        pending = FactoryItem(run_id=run_id, position=1, status='planned', revisions=1,
            content={'script': '保留这段已审核的文案'}, review={'approved': True}, media={'audio_path': 'verified-voice.mp3'})
        db.add(pending)
        db.commit()
        pending_id = pending.id
    _fake_lock(monkeypatch)
    monkeypatch.setattr(factory_service, '_make_plan', lambda _: None)
    monkeypatch.setattr(factory_service, '_write_and_review', lambda *_: None)
    pressure = factory_resources.MemoryPressure({'blocked': True, 'reason': '机器可用内存不足'})
    render = Mock(side_effect=pressure)
    package, report = Mock(), Mock()
    monkeypatch.setattr(factory_service, '_render', render)
    monkeypatch.setattr(factory_service, '_package', package)
    monkeypatch.setattr(factory_service, '_finish_report', report)
    assert factory_service.execute_run(run_id)['status'] == 'paused_memory'
    render.assert_called_once_with(run_id, pending_id)
    package.assert_not_called()
    report.assert_not_called()
    with isolated_factory() as db:
        run = db.get(FactoryRun, run_id)
        assert run.completed_count == 1 and run.active_slot == 'factory'
        assert run.finished_at is None
        assert db.get(FactoryItem, completed_id).media['video_path'] == 'existing-reviewed-video.mp4'
        assert db.get(FactoryItem, completed_id).packages[0]['status'] == 'submitted'
        item = db.get(FactoryItem, pending_id)
        assert item.status == 'planned' and item.revisions == 1 and item.review['approved']
        assert db.query(FactoryEvent).filter_by(stage='item_failed').count() == 0
        run.heartbeat_at = datetime.utcnow() - timedelta(hours=1)
        db.commit()
    # Broker redelivery and stale-job recovery cannot override a deliberate pause.
    assert factory_service.execute_run(run_id)['status'] == 'paused_memory'
    render.assert_called_once()
    monkeypatch.setattr(factory_tasks, 'SessionLocal', isolated_factory)
    monkeypatch.setattr(factory_tasks, 'init_db', lambda: None)
    enqueue = Mock(return_value=Mock(id='resumed-task'))
    monkeypatch.setattr(factory_tasks.produce_run, 'delay', enqueue)
    assert factory_tasks.schedule_factory.run()['status'] == 'paused_memory'
    enqueue.assert_not_called()
    monkeypatch.setattr(router, 'queue_available', lambda: True)
    monkeypatch.setattr(factory_resources, 'memory_state', lambda **kwargs: {'blocked': True, 'reason': '内存仍然不足'})
    with isolated_factory() as db:
        with pytest.raises(HTTPException) as err:
            router.retry_run(run_id, db)
        assert err.value.status_code == 409
        assert db.get(FactoryRun, run_id).status == 'paused_memory'
    enqueue.assert_not_called()
    monkeypatch.setattr(factory_resources, 'memory_state', lambda **kwargs: {'blocked': False})
    with isolated_factory() as db:
        assert router.retry_run(run_id, db)['status'] == 'queued'
        item = db.get(FactoryItem, pending_id)
        assert item.revisions == 1 and item.review['approved']
        assert item.media['audio_path'] == 'verified-voice.mp3'
        assert db.get(FactoryItem, completed_id).status == 'completed'
    enqueue.assert_called_once_with(run_id)


def test_memory_pause_can_be_cancelled_without_discarding_media(isolated_factory):
    from routers import factory as router
    run_id, item_id = _seed_item(isolated_factory, media={'audio_path': 'saved-audio.mp3'})
    with isolated_factory() as db:
        db.get(FactoryRun, run_id).status = 'paused_memory'
        db.commit()
        assert router.cancel_run(run_id, db)['status'] == 'cancelled'
        assert db.get(FactoryRun, run_id).active_slot is None
        assert db.get(FactoryItem, item_id).media['audio_path'] == 'saved-audio.mp3'


def test_memory_pressure_does_not_spend_model_budget(isolated_factory, monkeypatch):
    run_id, _ = _seed_item(isolated_factory)
    monkeypatch.setattr(factory_resources, 'memory_state', lambda **kwargs: {'blocked': True, 'reason': '机器内存不足'})
    client = Mock()
    monkeypatch.setattr(factory_llm.anthropic, 'Anthropic', client)
    with pytest.raises(factory_resources.MemoryPressure):
        factory_llm.call_agent(run_id, 'writer', {})
    client.assert_not_called()
    with isolated_factory() as db:
        assert db.get(FactoryRun, run_id).llm_calls == 0
