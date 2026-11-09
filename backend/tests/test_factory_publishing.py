"""Publication state and idempotency checks with no browser or network access."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.factory import FactoryEvent, FactoryItem, FactoryPublication, FactoryRun
from services import factory_publishing, factory_service, xiaohongshu_connector


@pytest.fixture
def publishing(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'publishing.db'}")
    Base.metadata.create_all(engine, tables=[FactoryRun.__table__, FactoryItem.__table__,
                                            FactoryPublication.__table__, FactoryEvent.__table__])
    sessions = sessionmaker(bind=engine)
    results = {}

    def enqueue(action, payload=None):
        command_id = str(uuid.uuid4())
        result = {"command_id": command_id, "action": action, "status": "queued",
                  "message": "Test command queued"}
        results[command_id] = result
        return dict(result)

    submit = Mock(side_effect=enqueue)
    get_result = Mock(side_effect=lambda command_id: dict(results[command_id]))
    monkeypatch.setattr(factory_publishing, "SessionLocal", sessions)
    monkeypatch.setattr(factory_service, "SessionLocal", sessions)
    monkeypatch.setattr(xiaohongshu_connector, "submit_command", submit)
    monkeypatch.setattr(xiaohongshu_connector, "command_result", get_result)
    monkeypatch.setattr(xiaohongshu_connector, "connection_state", lambda: {"authenticated": True})

    video, cover = tmp_path / "fixture.mp4", tmp_path / "cover.png"
    video.write_bytes(b"fixture, never uploaded")
    cover.write_bytes(b"fixture, never uploaded")
    with sessions() as db:
        run = FactoryRun(status="completed", target_count=1, completed_count=1,
                         report={"production": {"completed": 1, "published": 0, "submitted": 0}})
        db.add(run)
        db.flush()
        item = FactoryItem(run_id=run.id, position=0, status="completed",
                           brief={"title": "AI 教程", "category": "教程"},
                           content={"title": "AI 教程", "script": "经过审核的测试文案"},
                           media={"video_path": str(video), "cover_path": str(cover)},
                           packages=[{"platform": "xiaohongshu", "title": "AI 教程",
                                      "caption": "使用指南", "tags": ["AI"],
                                      "status": "package_ready", "published": False}])
        db.add(item)
        db.commit()
        run_id, item_id = run.id, item.id
    yield SimpleNamespace(sessions=sessions, results=results, submit=submit, get_result=get_result,
                          run_id=run_id, item_id=item_id)
    engine.dispose()


def _prepared_job(env, db):
    job = factory_publishing.begin_publication(db, env.item_id, "prepare")
    env.results[job.command_id] = {"command_id": job.command_id, "status": "prepared",
                                   "message": "已上传并填写，未发布", "uploaded": True,
                                   "published": False, "ai_disclosure": "笔记含AI合成内容"}
    return factory_publishing.refresh_job(db, job)


def test_prepared_is_not_counted_as_submitted_or_public(publishing):
    env = publishing
    with env.sessions() as db:
        job = _prepared_job(env, db)
        item = db.get(FactoryItem, env.item_id)
        detail = factory_service.run_detail(db, db.get(FactoryRun, env.run_id))
        assert job.status == "prepared"
        assert item.packages[0]["status"] == "prepared"
        assert item.packages[0]["published"] is False
        assert "submission_evidence" not in item.packages[0]
        assert detail["report"]["production"]["submitted"] == 0
        assert detail["report"]["production"]["published"] == 0
        assert detail["items"][0]["xiaohongshu_job"]["status"] == "prepared"


def test_repeated_item_action_queues_only_one_command_across_sessions(publishing):
    env = publishing
    with env.sessions() as first:
        first_id = factory_publishing.begin_publication(first, env.item_id, "prepare").id
    with env.sessions() as second:
        repeated = factory_publishing.begin_publication(second, env.item_id, "prepare")
        assert repeated.id == first_id
        assert second.query(FactoryPublication).count() == 1
    env.submit.assert_called_once()
    assert env.submit.call_args.args[0] == "prepare"
    assert env.submit.call_args.args[1]["item_id"] == env.item_id


def test_media_quality_hold_prevents_any_upload_or_submission(publishing):
    env = publishing
    with env.sessions() as db:
        item = db.get(FactoryItem, env.item_id)
        item.media = {**item.media, "publication_hold": "型号口播待复核"}
        db.commit()
        for action in ("prepare", "publish"):
            with pytest.raises(ValueError, match="暂缓提交"):
                factory_publishing.begin_publication(db, env.item_id, action)
    env.submit.assert_not_called()


@pytest.mark.parametrize("prepare_status", [None, "queued", "running", "failed"])
def test_publish_requires_a_confirmed_prepared_job(publishing, prepare_status):
    env = publishing
    with env.sessions() as db:
        if prepare_status is not None:
            command_id = str(uuid.uuid4())
            env.results[command_id] = {"status": prepare_status, "message": "not prepared"}
            db.add(FactoryPublication(item_id=env.item_id, action="prepare", status=prepare_status,
                                      command_id=command_id))
            db.commit()
        with pytest.raises(ValueError, match="先上传"):
            factory_publishing.begin_publication(db, env.item_id, "publish")
        assert db.query(FactoryPublication).filter_by(action="publish").count() == 0
    env.submit.assert_not_called()


def test_confirmed_submission_persists_evidence_without_claiming_public_visibility(publishing):
    env = publishing
    evidence = {"type": "success_page", "text": "发布成功",
                "url": "https://creator.xiaohongshu.com/publish/success"}
    with env.sessions() as db:
        _prepared_job(env, db)
        job = factory_publishing.begin_publication(db, env.item_id, "publish")
        env.results[job.command_id] = {"status": "submitted", "message": "平台确认已提交，等待审核",
                                       "item_id": env.item_id, "evidence": evidence}
        job = factory_publishing.refresh_job(db, job)
        item = db.get(FactoryItem, env.item_id)
        detail = factory_service.run_detail(db, db.get(FactoryRun, env.run_id))
        assert job.status == "submitted"
        assert job.result["evidence"] == evidence
        assert item.packages[0]["status"] == "submitted"
        assert item.packages[0]["submission_evidence"] == evidence
        assert item.packages[0]["published"] is False
        assert detail["report"]["production"]["submitted"] == 1
        assert detail["report"]["production"]["published"] == 0
        # A newly rendered preview must never inherit this version's receipt.
        from config import settings
        original_media = dict(item.media)
        item.media = {**original_media, "preview": {
            "video_path": str(settings.STORAGE_DIR / "factory/preview/new-video.mp4"),
            "duration": 48, "style": "tech_montage", "published": True,
        }}
        db.commit()
        versioned = factory_service.run_detail(db, db.get(FactoryRun, env.run_id))
        assert versioned["items"][0]["preview"]["published"] is False
        assert versioned["items"][0]["preview"]["video_url"].endswith("/preview/new-video.mp4")
        assert item.media["video_path"] == original_media["video_path"]
        assert versioned["items"][0]["packages"][0]["status"] == "submitted"
        # Polling and a duplicate submission request must not enqueue or log again.
        events = db.query(FactoryEvent).count()
        assert factory_publishing.begin_publication(db, env.item_id, "publish").id == job.id
        assert db.query(FactoryEvent).count() == events
    assert env.submit.call_count == 2  # One prepare, one publish.


def test_submitted_status_without_any_receipt_is_not_reported_as_success(publishing):
    env = publishing
    with env.sessions() as db:
        _prepared_job(env, db)
        job = factory_publishing.begin_publication(db, env.item_id, "publish")
        env.results[job.command_id] = {"status": "submitted", "message": "clicked", "evidence": {}}
        factory_publishing.refresh_job(db, job)
        item = db.get(FactoryItem, env.item_id)
        assert job.status != "submitted"
        assert item.packages[0]["status"] != "submitted"
        assert item.packages[0]["published"] is False


def test_failure_proven_before_submit_can_prepare_and_retry_without_new_job(publishing):
    env = publishing
    with env.sessions() as db:
        prepared = _prepared_job(env, db)
        job = factory_publishing.begin_publication(db, env.item_id, "publish")
        first_id, first_command = job.id, job.command_id
        env.results[first_command] = {"status": "failed", "submission_attempted": False,
                                      "message": "未找到控件，尚未点击"}
        factory_publishing.refresh_job(db, job)
        reupload = factory_publishing.begin_publication(db, env.item_id, "prepare")
        assert reupload.id == prepared.id
        env.results[reupload.command_id] = {"status": "prepared", "message": "已重新准备"}
        factory_publishing.refresh_job(db, reupload)
        retry = factory_publishing.begin_publication(db, env.item_id, "publish")
        assert retry.id == first_id
        assert retry.command_id != first_command
        assert retry.status == "queued"
        assert db.query(FactoryPublication).count() == 2
        event = db.query(FactoryEvent).filter_by(stage="platform_retry").one()
        assert event.detail["previous_command_id"] == first_command
    assert env.submit.call_count == 4


@pytest.mark.parametrize("result", [
    {"status": "failed"},
    {"status": "failed", "submission_attempted": True},
    {"status": "failed", "submission_attempted": False, "submission_uncertain": True},
])
def test_unknown_or_attempted_submission_is_never_retried(publishing, result):
    env = publishing
    with env.sessions() as db:
        _prepared_job(env, db)
        job = factory_publishing.begin_publication(db, env.item_id, "publish")
        command_id = job.command_id
        env.results[command_id] = result
        factory_publishing.refresh_job(db, job)
        assert factory_publishing.begin_publication(db, env.item_id, "publish").command_id == command_id
        with pytest.raises(ValueError, match="禁止重复发布"):
            factory_publishing.begin_publication(db, env.item_id, "prepare")
    assert env.submit.call_count == 2


def test_connector_rejects_unsafe_paths_bad_titles_and_non_uuid_ids(tmp_path, monkeypatch):
    # Exercise validation only. No process may start, even if a future regression
    # accidentally gets past the first validation layer.
    monkeypatch.setattr(xiaohongshu_connector, "PROJECT_ROOT", tmp_path)
    start = Mock(side_effect=AssertionError("A validation test must not start a browser"))
    monkeypatch.setattr(xiaohongshu_connector, "_ensure_daemon", start)
    storage = tmp_path / "backend" / "storage"
    storage.mkdir(parents=True)
    video = storage / "video.mp4"
    video.write_bytes(b"not a real upload")
    outside = tmp_path / "private.mp4"
    outside.write_bytes(b"private fixture")
    symlink = storage / "escape.mp4"
    symlink.symlink_to(outside)
    valid = {"item_id": "test-item", "video_path": str(video), "title": "标题", "caption": "正文"}
    for payload in ({**valid, "video_path": str(outside)}, {**valid, "video_path": str(symlink)},
                    {**valid, "title": "长" * 21}, {**valid, "item_id": "../../private"}):
        with pytest.raises(ValueError):
            xiaohongshu_connector.submit_command("prepare", payload)
    with pytest.raises(ValueError):
        xiaohongshu_connector.command_result("../../state.json")
    start.assert_not_called()
