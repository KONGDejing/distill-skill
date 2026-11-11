"""Workflow invariants, tested without a model, renderer, broker or live database."""
import json
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database import Base
from models.factory import FactoryConfig, FactoryEvent, FactoryItem, FactoryRun, FactoryPublication
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
    monkeypatch.setattr(factory_llm.settings, "FACTORY_MODEL_PROVIDER", "anthropic")
    monkeypatch.setattr(factory_resources, "memory_state", lambda **kwargs: {"blocked": False})
    engine = create_engine(f"sqlite:///{tmp_path / 'workflow-test.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[FactoryConfig.__table__, FactoryRun.__table__,
                                            FactoryItem.__table__, FactoryEvent.__table__, FactoryPublication.__table__])
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(factory_service, "SessionLocal", sessions)
    monkeypatch.setattr(factory_llm, "SessionLocal", sessions)
    monkeypatch.setattr(factory_service, "FACTORY_DIR", tmp_path / "factory")
    monkeypatch.setattr(factory_service, "queue_available", lambda: True)
    def fake_cover(content, target):
        Path(target).write_bytes(content["cover_text"].encode())
        return {"text_rendered": content["cover_text"], "version": 2}
    monkeypatch.setattr("services.factory_cover.render_cover", fake_cover)
    def fake_frame_cover(source, title, target, platform):
        Path(target).write_bytes(title.encode())
        return {"text_rendered": title, "version": 2, "frame_title_version": 1,
                "kind": "source_first_frame_with_title", "overlay_path": str(target)}
    monkeypatch.setattr("services.factory_cover.render_frame_cover", fake_frame_cover)
    def fake_complete(source, cover, target, **kwargs):
        Path(target).write_bytes(Path(source).read_bytes())
        return {"cover_integrated": True, "audio_preserved": True,
                "opening_mode": kwargs.get("opening_mode", "brand_card")}
    monkeypatch.setattr("services.factory_delivery_video.render_delivery_video", fake_complete)
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


@pytest.mark.parametrize("second_valid", [True, False])
def test_invalid_model_json_gets_one_budgeted_retry_without_fabricating_approval(isolated_factory, monkeypatch, second_valid):
    from types import SimpleNamespace
    run_id, _ = _seed_item(isolated_factory)
    def response(text):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5), stop_reason="end_turn")
    client = Mock()
    client.messages.create.side_effect = [response('not valid JSON'), response(
        '{"approved":false,"score":30,"issues":["证据不足"]}' if second_valid else 'still invalid')]
    monkeypatch.setattr(factory_llm.anthropic, "Anthropic", Mock(return_value=client))
    if second_valid:
        assert factory_llm.call_agent(run_id, "reviewer", {"script": "test"})["approved"] is False
    else:
        with pytest.raises(ValueError, match="JSON"):
            factory_llm.call_agent(run_id, "reviewer", {"script": "test"})
    assert client.messages.create.call_count == 2
    assert "format_feedback" in client.messages.create.call_args_list[1].kwargs["messages"][0]["content"]
    with isolated_factory() as db:
        run = db.get(FactoryRun, run_id)
        assert (run.llm_calls, run.input_tokens, run.output_tokens) == (2, 20, 10)


def test_model_usage_preserves_item_through_format_retry(isolated_factory, monkeypatch):
    from types import SimpleNamespace
    from services.factory_usage import item_usage
    run_id, item_id = _seed_item(isolated_factory)
    client = Mock()
    client.messages.create.side_effect = [SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=20, output_tokens=5, cached_input_tokens=12), stop_reason="end_turn")
        for text in ["invalid", '{"approved": true}']]
    monkeypatch.setattr(factory_llm.anthropic, "Anthropic", Mock(return_value=client))

    @item_usage("script")
    def scoped(run_id, item_id):
        return factory_llm.call_agent(run_id, "reviewer", {}, work_kind="initial")

    assert scoped(run_id, item_id)["approved"] is True
    with isolated_factory() as db:
        events = db.query(FactoryEvent).filter_by(stage="model_call").order_by(FactoryEvent.id).all()
        assert [event.detail["item_id"] for event in events] == [item_id, item_id]
        assert [event.detail["work_kind"] for event in events] == ["initial", "rework"]
        assert sum(e.detail["input_tokens"] for e in events) == db.get(FactoryRun, run_id).input_tokens


def test_source_validation_rejects_mixed_invented_ids_and_wrong_shape():
    assert factory_service._sources_for([SOURCE["id"]], [SOURCE]) == [SOURCE]
    for source_ids in (["invented"], [SOURCE["id"], "invented"], SOURCE["id"], []):
        with pytest.raises(ValueError):
            factory_service._sources_for(source_ids, [SOURCE])


@pytest.mark.parametrize("matched", [True, False])
def test_paper_requires_fulltext_before_writing_and_never_substitutes_background(isolated_factory, monkeypatch, matched):
    from services import factory_papers
    run_id, item_id = _seed_item(isolated_factory)
    visual = {**SOURCE, "id": "visual", "visual_ready": True, "visual_assets": []}
    with isolated_factory() as db:
        run = db.get(FactoryRun, run_id)
        run.sources = [{**SOURCE, "kind": "paper"}, visual]
        run.settings_snapshot = {**run.settings_snapshot, "visual_style": "tech_montage"}
        db.commit()
    document = {"id": "test-paper", "url": "https://arxiv.org/pdf/0000.00000", "pages": [{"page": 1, "text": "Original paper"}], "sha256": "test"}
    monkeypatch.setattr(factory_papers, "collect_paper", Mock(return_value=document, side_effect=None if matched else ValueError("原文不足")))
    call = Mock()
    monkeypatch.setattr(factory_service, "call_agent", call)
    if not matched:
        with pytest.raises(ValueError, match="原文不足"):
            factory_service._ensure_visual_support(run_id, item_id)
    else:
        factory_service._ensure_visual_support(run_id, item_id)
        with isolated_factory() as db:
            brief = db.get(FactoryItem, item_id).brief
            assert brief["source_ids"] == [SOURCE["id"]]
            assert brief["visual_source_ids"] == []
            assert brief["background_visuals"] is False
            assert brief["paper_document"] == document
    call.assert_not_called()


def test_director_assigns_papers_to_specialist_and_resume_keeps_ownership(isolated_factory):
    from services.factory_team import assign_editors, editor_id
    run_id, first = _seed_item(isolated_factory)
    with isolated_factory() as db:
        for position in range(1, 10):
            db.add(FactoryItem(run_id=run_id, position=position, brief={"title": f"Topic {position}",
                "category": "论文讲解" if position in [1, 4, 7] else "科技动态"}))
        db.commit()
        assert assign_editors(db, run_id) == {"producer": 4, "producer2": 3, "producer3": 3}
        before = {i.id: editor_id(i) for i in db.query(FactoryItem).filter_by(run_id=run_id)}
        assign_editors(db, run_id)
        assert before == {i.id: editor_id(i) for i in db.query(FactoryItem).filter_by(run_id=run_id)}
        assert db.query(FactoryEvent).filter_by(stage="editor_assignment").count() == 1


def test_new_editors_do_not_take_credit_for_started_or_historical_videos(isolated_factory):
    from services.factory_team import assign_editors, editor_id
    run_id, first = _seed_item(isolated_factory, status="rendering")
    with isolated_factory() as db:
        for position in range(1, 4):
            db.add(FactoryItem(run_id=run_id, position=position, brief={"title": f"Topic {position}"}))
        db.commit()
        assign_editors(db, run_id)
        assert editor_id(db.get(FactoryItem, first)) == "producer"
        other = db.query(FactoryItem).filter_by(run_id=run_id, position=1).one()
        assert editor_id(other) == "producer3"


def test_calibrated_duration_feedback_reports_actual_characters(isolated_factory, monkeypatch):
    run_id, item_id = _seed_item(isolated_factory)
    with isolated_factory() as db:
        run, item = db.get(FactoryRun, run_id), db.get(FactoryItem, item_id)
        run.settings_snapshot = {**run.settings_snapshot, "target_duration_seconds": 150}
        item.brief = {**item.brief, "target_script_characters": 700}
        db.commit()
    script = {"title": "已校准的讲解", "source_ids": [SOURCE["id"]]}
    calls = Mock(side_effect=[{**script, "script": "文" * 600}, {**script, "script": "文" * 700}, {"approved": True, "score": 90}])
    monkeypatch.setattr(factory_service, "call_agent", calls)
    factory_service._write_and_review(run_id, item_id)
    assert calls.call_args_list[0].args[2]["suggested_script_characters"] == 700
    assert "实测600字符" in calls.call_args_list[1].args[2]["review_feedback"]["reason"]


def test_montage_plan_does_not_assign_papers_without_usable_visuals():
    sources = [{'id': 'paper'}, {'id': 'demo', 'visual_ready': True}]
    missing = {'title': '只有论文正文', 'source_ids': ['paper']}
    direct = {'title': '项目演示', 'source_ids': ['paper', 'demo']}
    background = {'title': '相关概念解读', 'source_ids': ['paper'], 'visual_source_ids': ['demo'],
                  'visual_support_reason': '同主题背景，不能当作论文实测'}
    forged = {**background, 'visual_source_ids': ['unknown']}
    no_reason = {**background, 'visual_support_reason': ''}
    assert factory_service._eligible_candidates([missing, direct, background, forged, no_reason], sources, 'tech_montage') == [direct, background]
    assert factory_service._eligible_candidates([missing], sources, 'cards') == [missing]


def test_targeted_recovery_does_not_retry_unfixed_items(isolated_factory, monkeypatch):
    from routers import factory as router
    from tasks.factory_tasks import produce_run
    run_id, item_id = _seed_item(isolated_factory, status="failed")
    with isolated_factory() as db:
        run = db.get(FactoryRun, run_id);run.status="failed";run.active_slot=None
        other=FactoryItem(run_id=run_id,position=1,status="failed",brief={"title":"缺少素材"},error="无匹配画面")
        db.add(other);db.commit();other_id=other.id
        monkeypatch.setattr(router, "queue_available", lambda: True)
        monkeypatch.setattr(produce_run, "delay", Mock(return_value=Mock(id="recovery")))
        router.retry_run(run_id, db, router.RetryRun(item_ids=[item_id]))
        assert db.get(FactoryItem, other_id).status == "failed"
        assert db.get(FactoryItem, other_id).error == "无匹配画面"
    _fake_lock(monkeypatch)
    monkeypatch.setattr(factory_service, "_make_plan", lambda *_: None)
    monkeypatch.setattr(factory_service, "_write_and_review", lambda *_: None)
    render=Mock();monkeypatch.setattr(factory_service, "_render", render)
    monkeypatch.setattr(factory_service, "_package", lambda *_: None)
    monkeypatch.setattr(factory_service, "_finish_report", lambda *_: None)
    factory_service.execute_run(run_id)
    render.assert_called_once_with(run_id, item_id)


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


def _package_media(directory):
    directory.mkdir(parents=True)
    media = {}
    for field, filename in {"video_path": "video.mp4", "audio_path": "audio.mp3",
                            "cover_path": "cover.png", "subtitle_path": "subtitles.ass"}.items():
        path = directory / filename
        path.write_bytes(f"fixture-only:{filename}".encode())
        media[field] = str(path)
    return media


@pytest.mark.parametrize("single_platform", [False, True])
def test_archives_contain_manual_handoff_and_only_target_assigned_platform(isolated_factory, monkeypatch, single_platform):
    from services.factory_handoff import HANDOFF_VERSION

    approved_copy = {"title": "AI 工具使用指南：怎样核对边界？", "script": "这是经过审核的测试文案。", "sources": [SOURCE],
                     "hook": "怎样核对工具的使用边界？", "follow_prompt": "欢迎一起对照原文看证据。"}
    content = {**approved_copy, "research_packet": {"raw": "RESEARCH_PACKET_NOT_FOR_PUBLISHER" * 1000},
               "paper_document": {"pages": [{"text": "FULL_PDF_NOT_FOR_PUBLISHER" * 2000}]},
               "scenes": [{"paper_highlights": [{"text": "SCENE_HIGHLIGHT_NOT_FOR_PUBLISHER" * 1000}]}]}
    run_id, item_id = _seed_item(isolated_factory, content=content)
    directory = factory_service.FACTORY_DIR / run_id / item_id
    media = _package_media(directory)
    factory_service._save_item(item_id, media=media)
    platforms = ["xiaohongshu"] if single_platform else PLATFORMS
    if single_platform:
        with isolated_factory() as db:
            run = db.get(FactoryRun, run_id)
            run.settings_snapshot = {**run.settings_snapshot, "distribution_policy": "single-platform-v1"}
            item = db.get(FactoryItem, item_id)
            item.brief = {**item.brief, "distribution": {"platform": "xiaohongshu", "day": "2026-11-11", "policy": "single-platform-v1"}}
            db.commit()
    variants = [{"platform": platform, "title": f"{platform} 标题", "caption": "平台简介",
                 "tags": ["AI"], "published": True, "status": "published",
                 "manual_handoff": {"cover_text": f"{platform} 封面文字", "pinned_comment": "一起对照所列原文。",
                                    "hooks": ["先看原文里的条件。", "这条内容能说明什么？"]}}
                for platform in platforms]
    model = Mock(return_value={"platforms": variants})
    monkeypatch.setattr(factory_service, "call_agent", model)

    factory_service._package(run_id, item_id)
    # Redelivery must reuse complete archives instead of calling the model again.
    factory_service._package(run_id, item_id)
    assert model.call_count == 1
    payload = model.call_args.args[2]
    assert model.call_args.args[1] == "publisher"
    assert payload["platforms"] == platforms and payload["delivery_mode"] == "manual"
    assert payload["content"] == approved_copy
    assert all(marker not in json.dumps(payload) for marker in (
        "RESEARCH_PACKET_NOT_FOR_PUBLISHER", "FULL_PDF_NOT_FOR_PUBLISHER", "SCENE_HIGHLIGHT_NOT_FOR_PUBLISHER"))

    with isolated_factory() as db:
        item = db.get(FactoryItem, item_id)
        assert item.status == "completed"
        assert {package["platform"] for package in item.packages} == set(platforms)
        for package in item.packages:
            assert package["status"] == "package_ready"
            assert package.get("published", False) is False
            assert package["handoff_version"] == HANDOFF_VERSION
            assert package["video_filename"] == "AI 工具使用指南：怎样核对边界？.mp4"
            assert Path(package["path"]).is_file()
            with zipfile.ZipFile(package["path"]) as archive:
                assert {"AI 工具使用指南：怎样核对边界？.mp4", "audio.mp3", "cover.png", "subtitles.ass", "发布文案.txt",
                        "互动回复模板.txt", "资料来源.txt", "手动发布说明.txt", "发布标题.txt", "正文描述.txt", "发布平台.txt"}.issubset(archive.namelist())
                assert "video.mp4" not in archive.namelist()
                assert archive.read(package["video_filename"]) == b"fixture-only:video.mp4"
                assert archive.getinfo(package["video_filename"]).flag_bits & 0x800
                metadata = json.loads(archive.read("metadata.json"))
                assert archive.read("发布标题.txt").decode().strip() == package["title"]
                assert archive.read("正文描述.txt").decode().strip() == package["manual_handoff"]["description"]
                assert metadata["platform"] == package["platform"]
                assert metadata["video_filename"] == package["video_filename"]
                assert metadata["published"] is False
                assert metadata["status"] == "package_ready"
                assert archive.read("script.txt").decode() == "这是经过审核的测试文案。"
                handoff = metadata["manual_handoff"]
                assert handoff == package["manual_handoff"]
                assert handoff["platform"] == package["platform"]
                assert handoff["title"] == package["title"] and handoff["caption"] == "平台简介"
                if package["platform"] in {"xiaohongshu", "kuaishou"}:
                    assert handoff["cover_text"] == content["title"]
                    assert package["delivery_quality"]["opening_mode"] == "first_frame_title"
                    assert package["cover_design"]["frame_title_version"] == 1
                else:
                    assert handoff["cover_text"] == f"{package['platform']} 封面文字"
                assert archive.read("cover.png").decode() == handoff["cover_text"]
                assert handoff["hooks"][:2] == ["先看原文里的条件。", "这条内容能说明什么？"]
                assert handoff["pinned_comment"] == "一起对照所列原文。"
                assert len(handoff["reply_templates"]) >= 3
                assert "#AI" in archive.read("发布文案.txt").decode("utf-8")
                assert "AI辅助整理文案、配音和剪辑" in archive.read("发布文案.txt").decode("utf-8")
                assert "不表示已收到这些留言" in archive.read("互动回复模板.txt").decode("utf-8")
                assert SOURCE["url"] in archive.read("资料来源.txt").decode("utf-8")
                assert Path(package["copy_path"]).read_bytes() == archive.read("发布文案.txt")
                if single_platform:
                    assert handoff["distribution"]["platform"] == "xiaohongshu"
                    assert "2026-11-11" in archive.read("手动发布说明.txt").decode("utf-8")


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu", "kuaishou"])
def test_first_frame_upgrade_reuses_approved_copy_and_leaves_douyin_cached(isolated_factory, monkeypatch, platform):
    content = {"title": "真实画面里的机器人", "script": "审核完成的完整口播。", "sources": [SOURCE]}
    run_id, item_id = _seed_item(isolated_factory, content=content)
    directory = factory_service.FACTORY_DIR / run_id / item_id
    media = _package_media(directory)
    media["fingerprint"] = "approved-source-montage"
    factory_service._save_item(item_id, media=media)
    with isolated_factory() as db:
        run = db.get(FactoryRun, run_id)
        run.settings_snapshot = {**run.settings_snapshot, "platforms": [platform]}
        db.commit()
    model = Mock(return_value={"platforms": [{"platform": platform, "title": "已定稿发布标题", "caption": "已定稿正文", "tags": ["机器人"]}]})
    monkeypatch.setattr(factory_service, "call_agent", model)
    factory_service._package(run_id, item_id)
    # Simulate the old shared-card package with the current handoff/design
    # version, exactly the case a global version-only cache would miss.
    with isolated_factory() as db:
        item = db.get(FactoryItem, item_id)
        package = dict(item.packages[0])
        old_path = directory / "old-shared-card.zip"
        old_bytes = Path(package["path"]).read_bytes()
        old_path.write_bytes(old_bytes)
        package.update(path=str(old_path), cover_design={"version": 2, "kind": "original_svg_title_design"},
                       delivery_quality={"cover_integrated": True, "audio_preserved": True})
        item.packages = [package]
        db.commit()
    model.side_effect = AssertionError("A finishing repair must not call a model")
    from services.factory_delivery_video import render_delivery_video
    finisher = Mock(wraps=render_delivery_video)
    monkeypatch.setattr("services.factory_delivery_video.render_delivery_video", finisher)
    factory_service._package(run_id, item_id)
    factory_service._package(run_id, item_id)
    assert model.call_count == 1
    assert old_path.read_bytes() == old_bytes
    with isolated_factory() as db:
        item = db.get(FactoryItem, item_id)
        assert item.content == content and item.media == media
        package = item.packages[0]
        assert package["title"] == "已定稿发布标题" and package["caption"] == "已定稿正文"
        if platform == "douyin":
            finisher.assert_not_called()
            assert package["path"] == str(old_path)
        else:
            assert finisher.call_count == 1
            assert finisher.call_args.args[0] == media["video_path"]
            assert package["path"] != str(old_path)
            assert package["delivery_quality"]["opening_mode"] == "first_frame_title"
            assert package["manual_handoff"]["cover_text"] == content["title"]


@pytest.mark.parametrize("old_version", [None, 1], ids=["legacy", "manual-v1"])
def test_existing_archive_upgrade_reuses_video_and_preserves_uncertain_publication(isolated_factory, monkeypatch, old_version):
    from services.factory_handoff import HANDOFF_VERSION

    run_id, item_id = _seed_item(isolated_factory, status="completed", content={
        "title": "已有定稿", "script": "这是已经审核的原始口播。", "sources": [SOURCE],
        "hook": "先核对原文。", "follow_prompt": "一起看清来源与边界。",
    })
    directory = factory_service.FACTORY_DIR / run_id / item_id
    media = _package_media(directory)
    old_zip = directory / ("douyin-manual.zip" if old_version else "douyin.zip")
    with zipfile.ZipFile(old_zip, "w") as archive:
        archive.writestr("metadata.json", json.dumps({"handoff_version": old_version}))
        archive.write(media["video_path"], "video.mp4")
    original_zip = old_zip.read_bytes()
    original_files = {path: Path(path).read_bytes() for path in media.values()}
    with isolated_factory() as db:
        run = db.get(FactoryRun, run_id)
        run.settings_snapshot = {**run.settings_snapshot, "platforms": ["douyin"],
                                 "distribution_policy": "single-platform-v1"}
        item = db.get(FactoryItem, item_id)
        item.brief = {**item.brief, "distribution": {"platform": "douyin", "day": "2026-11-12",
                                                    "policy": "single-platform-v1"}}
        item.media = media
        publication = FactoryPublication(item_id=item_id, platform="douyin", action="publish", status="failed",
            command_id="historical-command", result={"submission_attempted": True, "submission_uncertain": True,
                                                      "message": "已点击提交，尚无官方回执"})
        db.add(publication)
        db.flush()
        existing = {"platform": "douyin", "title": "沿用旧标题", "caption": "沿用已定稿正文", "tags": ["AI"],
                    "status": "prepared", "published": False, "path": str(old_zip),
                    "publication_job_id": publication.id}
        if old_version:
            existing.update(handoff_version=old_version,
                            manual_handoff={"version": old_version, "cover_text": "保留已交付封面文字"})
        item.packages = [existing]
        db.commit()
        old_publication = deepcopy({column.name: getattr(publication, column.name)
                                    for column in FactoryPublication.__table__.columns})
        old_content = deepcopy(item.content)
        old_counters = (run.llm_calls, run.input_tokens, run.output_tokens)
    model = Mock(side_effect=AssertionError("Archive upgrades must not call a model"))
    render = Mock(side_effect=AssertionError("Archive upgrades must not render again"))
    monkeypatch.setattr(factory_service, "call_agent", model)
    monkeypatch.setattr(factory_service, "_render", render)

    factory_service._package(run_id, item_id)
    factory_service._package(run_id, item_id)

    model.assert_not_called()
    render.assert_not_called()
    assert old_zip.read_bytes() == original_zip
    assert {path: Path(path).read_bytes() for path in original_files} == original_files
    with isolated_factory() as db:
        item, run = db.get(FactoryItem, item_id), db.get(FactoryRun, run_id)
        assert item.content == old_content and item.media == media
        assert (run.llm_calls, run.input_tokens, run.output_tokens) == old_counters
        assert db.query(FactoryPublication).count() == 1
        publication = db.get(FactoryPublication, old_publication["id"])
        assert {column.name: getattr(publication, column.name)
                for column in FactoryPublication.__table__.columns} == old_publication
        package = item.packages[0]
        assert package["platform"] == "douyin" and Path(package["path"]) != old_zip
        assert package["published"] is False
        assert package["handoff_version"] == HANDOFF_VERSION == 4
        assert package["video_filename"] == "已有定稿.mp4"
        with zipfile.ZipFile(package["path"]) as archive:
            assert "video.mp4" not in archive.namelist()
            assert archive.read("已有定稿.mp4") == original_files[media["video_path"]]
            metadata = json.loads(archive.read("metadata.json"))
            assert metadata["video_filename"] == package["video_filename"]
            handoff = metadata["manual_handoff"]
            assert handoff["version"] == HANDOFF_VERSION
            assert handoff["title"] == "沿用旧标题" and handoff["caption"] == "沿用已定稿正文"
            if old_version:
                assert handoff["cover_text"] == "保留已交付封面文字"
            assert "结果未确认" in handoff["publication_warning"]
            instructions = archive.read("手动发布说明.txt").decode("utf-8")
            assert "请本人先在官方作品管理确认" in instructions and "不能重复提交" in instructions
            assert "package_ready" in instructions and "不代表已提交" in instructions


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


def test_model_quota_error_stops_batch_without_failing_every_remaining_video(isolated_factory, monkeypatch):
    import redis
    run_id, item_id = _seed_item(isolated_factory)
    with isolated_factory() as db:
        second = FactoryItem(run_id=run_id, position=1, brief={'title': '下一条'})
        db.add(second); db.commit()
        second_id = second.id
    client = Mock()
    client.lock.return_value.acquire.return_value = True
    monkeypatch.setattr(redis.Redis, 'from_url', Mock(return_value=client))
    monkeypatch.setattr(factory_service, '_make_plan', lambda _: None)
    write = Mock(side_effect=factory_llm.ModelServiceUnavailable('模型服务额度不足'))
    monkeypatch.setattr(factory_service, '_write_and_review', write)
    render = Mock(); monkeypatch.setattr(factory_service, '_render', render)
    assert factory_service.execute_run(run_id)['status'] == 'model_service_blocked'
    write.assert_called_once()
    render.assert_not_called()
    with isolated_factory() as db:
        run = db.get(FactoryRun, run_id)
        assert run.active_slot is None and '额度不足' in run.error
        assert db.get(FactoryItem, second_id).status == 'planned'
        assert db.get(FactoryItem, item_id).status != 'failed'


@pytest.mark.parametrize('status,message,expected', [
    (401, '额度不足 secret-do-not-echo', '额度不足'),
    (429, 'insufficient quota secret-do-not-echo', '额度不足'),
    (401, 'invalid credential secret-do-not-echo', '授权未通过'),
])
def test_account_errors_report_action_without_echoing_provider_private_details(status, message, expected):
    from types import SimpleNamespace
    result = factory_llm._account_error(SimpleNamespace(status_code=status, body={'error': {'message': message}}))
    assert expected in result and 'secret-do-not-echo' not in result


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


@pytest.mark.parametrize('recover', [True, False])
def test_malformed_single_topic_decision_gets_one_retry_without_changing_original_early(isolated_factory, monkeypatch, recover):
    run_id, item_id = _seed_item(isolated_factory, content={'script': '原稿'}, review={'approved': False})
    with isolated_factory() as db:
        db.get(FactoryRun, run_id).plan = {'candidates': [{'title': '备用选题', 'source_ids': [SOURCE['id']]}]}
        db.commit()
    calls = []
    def decide(run, agent, payload, **kwargs):
        calls.append(payload)
        with isolated_factory() as db:
            assert db.get(FactoryItem, item_id).content['script'] == '原稿'
        if len(calls) == 2 and recover:
            return {'selected': [{'candidate_index': 0}]}
        return {'selected': [{'candidate_index': 0}, {'candidate_index': 0}]}
    monkeypatch.setattr(factory_service, 'call_agent', decide)
    if recover:
        assert factory_service._replace_rejected_topic(run_id, item_id)
    else:
        with pytest.raises(ValueError, match='重试一次'):
            factory_service._replace_rejected_topic(run_id, item_id)
        with isolated_factory() as db:
            assert db.get(FactoryItem, item_id).content == {'script': '原稿'}
    assert len(calls) == 2 and calls[1]['target'] == 1 and 'format_feedback' in calls[1]


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


@pytest.mark.parametrize("already_complete", [False, True])
def test_manual_delivery_never_auto_submits_even_with_old_snapshot(isolated_factory, monkeypatch, already_complete):
    import redis
    from services import factory_publishing

    run_id, item_id = _seed_item(isolated_factory, status="completed" if already_complete else "planned")
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
    package.side_effect = lambda *_: factory_service._save_item(item_id, status="completed")
    monkeypatch.setattr(factory_service, "_ensure_visual_support", Mock())
    monkeypatch.setattr(factory_service, "_write_and_review", review)
    monkeypatch.setattr(factory_service, "_render", render)
    monkeypatch.setattr(factory_service, "_package", package)
    monkeypatch.setattr(factory_publishing, "auto_submit", submit)

    factory_service.execute_run(run_id)

    for step in (review, render, package):
        if already_complete:
            step.assert_not_called()
        else:
            step.assert_called_once_with(run_id, item_id)
    submit.assert_not_called()
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


def test_newsroom_and_director_handoff_precedes_writer_and_is_persisted(isolated_factory, monkeypatch):
    from services import factory_newsroom
    run_id,item_id=_seed_item(isolated_factory)
    plan={'hooks':['为什么好看的演示不能代表可靠？','看懂这个边界，才不会被演示误导。'],'selected':0,
          'viewer_value':'分清演示能力与实际使用限制','payoff':'讲清为何演示不能等同实测','opening_visual':'展示本条来源的实际操作说明',
          'follow_value':'持续核对AI能力与使用边界','experiment':'同平台同系列只比较开场变化','source_ids':[SOURCE['id']]}
    packet={'facts':[{'source_id':SOURCE['id'],'claim':'这是一份指南资料','evidence_quote':SOURCE['text'][:50]}],
            'event_date':'未确认','limitations':'不代表已经实测','visual_requests':[{'source_id':SOURCE['id'],'needed_picture':'对应指南的实际界面'}]}
    answer='这是使用指南而不是现场测试，仍需检查实际效果。'
    prompt='想持续看懂AI的能力与边界，可以关注这个系列。'
    content={'title':'看懂演示边界','hook':plan['hooks'][0], 'script':plan['hooks'][0]+answer*5+prompt,
             'follow_prompt':prompt,'follow_value':plan['follow_value'],'payoff_excerpt':answer,'source_ids':[SOURCE['id']]}
    with isolated_factory() as db:
        run=db.get(FactoryRun,run_id);run.settings_snapshot={**run.settings_snapshot,'director_hook_required':True,'engagement_required':True}
        item=db.get(FactoryItem,item_id);item.brief={**item.brief,'editor_id':'producer3'};db.commit()
    calls=Mock(side_effect=[packet,plan,content,{'approved':True,'score':90}]);monkeypatch.setattr(factory_service,'call_agent',calls)
    factory_service._write_and_review(run_id,item_id)
    assert [c.args[1] for c in calls.call_args_list]==['researcher3','director','writer','reviewer']
    with isolated_factory() as db:
        item=db.get(FactoryItem,item_id)
        assert item.brief['research_packet']==packet and item.content['engagement_plan']==plan
    factory_service._write_and_review(run_id,item_id)
    assert calls.call_count==4  # resume never spends again on an accepted script


def test_observed_token_budget_stops_next_model_call_and_retains_progress(isolated_factory, monkeypatch):
    run_id,_=_seed_item(isolated_factory)
    with isolated_factory() as db:
        r=db.get(FactoryRun,run_id);r.input_tokens=90;r.output_tokens=20;r.settings_snapshot={**r.settings_snapshot,'max_total_tokens':100};db.commit()
    network=Mock();monkeypatch.setattr(factory_llm.anthropic,'Anthropic',network)
    with pytest.raises(factory_llm.ModelServiceUnavailable,match='token预算'):
        factory_llm.call_agent(run_id,'writer',{})
    network.assert_not_called()
    with isolated_factory() as db:
        assert db.get(FactoryRun,run_id).llm_calls==0


def test_zero_token_limit_allows_authorized_work_above_one_million(isolated_factory):
    run_id,_=_seed_item(isolated_factory)
    with isolated_factory() as db:
        r=db.get(FactoryRun,run_id);r.input_tokens=1_500_000;r.output_tokens=200_000
        r.settings_snapshot={**r.settings_snapshot,'max_total_tokens':0};db.commit()
    factory_llm._reserve_call(run_id)
    with isolated_factory() as db:
        assert db.get(FactoryRun,run_id).llm_calls==1


def test_same_day_duplicate_narration_is_not_packaged_for_another_platform(isolated_factory):
    run_id, item_id = _seed_item(isolated_factory, content={"title": "第一条", "script": "同一段内容。"})
    with isolated_factory() as db:
        item = db.get(FactoryItem, item_id)
        item.brief = {**item.brief, 'distribution': {'platform': 'douyin', 'day': '2026-11-12'}}
        db.add(FactoryItem(run_id=run_id, position=1, brief={'distribution': {'platform': 'xiaohongshu', 'day': '2026-11-12'}},
                           content={'title': '改一个标题', 'script': '同一段内容！'}))
        db.commit()
    with pytest.raises(ValueError, match='相同口播'):
        factory_service._package(run_id, item_id)
