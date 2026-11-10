"""Factory application service and durable production workflow."""
import json
import os
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy.exc import IntegrityError
from config import settings
from database import SessionLocal
from models.factory import FactoryConfig, FactoryRun, FactoryItem, FactoryEvent, FactoryPublication
from agents.registry import AGENTS, AGENT_MAP, PLATFORMS
from services.factory_llm import call_agent, RunCancelled
from services.factory_resources import MemoryPressure, ensure_memory_available
from services.factory_editorial import STRATEGY, validate_editorial_scenes, validate_follow_prompt
from services.factory_team import assign_editors, editor_id

TERMINAL = {"completed", "partial", "failed", "cancelled", "paused_memory"}
FACTORY_DIR = settings.STORAGE_DIR / "factory"


class ReviewRejected(ValueError):
    pass


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def config_dict(config):
    keys = ("daily_target", "target_duration_seconds", "sector", "schedule_enabled", "schedule_time", "timezone", "platforms", "max_revisions", "max_llm_calls", "auto_publish_xiaohongshu", "visual_style", "publish_enabled", "publish_times")
    return {key: getattr(config, key) for key in keys}


def get_config(db):
    config = db.get(FactoryConfig, 1)
    if not config:
        config = FactoryConfig(id=1)
        db.add(config)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            config = db.get(FactoryConfig, 1)
    return config


def storage_url(path):
    if not path:
        return None
    try:
        return "/storage/" + Path(path).resolve().relative_to(settings.STORAGE_DIR.resolve()).as_posix()
    except ValueError:
        return None


def run_dict(run):
    return {"id": run.id, "status": run.status, "stage": run.stage,
            "target_count": run.target_count, "completed_count": run.completed_count,
            "trigger": run.trigger, "created_at": iso(run.created_at), "finished_at": iso(run.finished_at),
            "heartbeat_at": iso(run.heartbeat_at), "error": run.error,
            "cancel_requested": run.cancel_requested,
            "llm_calls": run.llm_calls, "input_tokens": run.input_tokens, "output_tokens": run.output_tokens}


def production_summary(production):
    return (f"完成 {production.get('completed', 0)}/{production.get('target', 0)} 条原创视频，"
            f"交付 {production.get('packages', 0)} 份平台素材包，"
            f"已向平台提交 {production.get('submitted', 0)} 次。"
            "素材包是各平台的输出版本；公开状态仍需以平台审核为准。")


def run_detail(db, run):
    result = run_dict(run)
    result["report"] = run.report or {}
    result["plan"] = run.plan or {}
    result["items"] = []
    for item in db.query(FactoryItem).filter_by(run_id=run.id).order_by(FactoryItem.position):
        content, media = item.content or {}, item.media or {}
        preview = media.get("preview") or {}
        packages = [{**package, "download_url": f"/api/factory/items/{item.id}/packages/{package['platform']}"} for package in item.packages or []]
        publication = db.query(FactoryPublication).filter_by(item_id=item.id, platform="xiaohongshu").order_by(FactoryPublication.created_at.desc()).first()
        from services.factory_publishing import job_dict
        publications = db.query(FactoryPublication).filter_by(item_id=item.id).order_by(
            FactoryPublication.created_at.desc()).all()
        publications.sort(key=lambda job: (job.result or {}).get("updated_at") or iso(job.created_at) or "", reverse=True)
        platform_jobs = {}
        for job in publications:
            platform_jobs.setdefault(job.platform, job_dict(job))
        result["items"].append({
            "id": item.id, "title": content.get("title") or item.brief.get("title", "待生成"),
            "editor_id": editor_id(item), "editor_name": AGENT_MAP[editor_id(item)]["name"],
            "category": item.brief.get("category", "AI 科技"), "status": item.status,
            "script": content.get("script", ""), "hook": content.get("hook", ""),
            "scenes": content.get("scenes", []), "sources": content.get("sources", []),
            "review": item.review or {}, "media_quality": media.get("quality", {}),
            "video_url": storage_url(media.get("video_path")), "webm_url": storage_url(media.get("webm_path")),
            "visual_style": media.get("style") or media.get("renderer_kind", "cards"),
            "audio_url": storage_url(media.get("audio_path")),
            "cover_url": storage_url(media.get("cover_path")), "duration": media.get("duration"),
            "tts_engine": media.get("tts_engine"), "packages": packages, "error": item.error,
            "preview": {"video_url": storage_url(preview.get("video_path")),
                        "webm_url": storage_url(preview.get("webm_path")),
                        "cover_url": storage_url(preview.get("cover_path")),
                        "audio_url": storage_url(preview.get("audio_path")),
                        "duration": preview.get("duration"), "style": preview.get("style", "tech_montage"),
                        "published": False} if preview.get("video_path") else None,
            "xiaohongshu_job": {"id": publication.id, "status": publication.status,
                                 "message": (publication.result or {}).get("message", "")} if publication else None,
            "platform_jobs": platform_jobs,
            "publications": [job_dict(job) for job in publications],
        })
    if result["report"]:
        result["report"] = {**result["report"], "production": {**result["report"].get("production", {}),
            "submitted": sum(p.get("status") == "submitted" for item in result["items"] for p in item["packages"])}}
        result["report"]["summary"] = production_summary(result["report"]["production"])
        result["report"].update(llm_calls=run.llm_calls, input_tokens=run.input_tokens, output_tokens=run.output_tokens)
    result["events"] = [{"id": event.id, "agent_id": event.agent_id,
                         "agent_name": AGENT_MAP.get(event.agent_id, {}).get("name", event.agent_id),
                         "stage": event.stage, "status": event.status, "message": event.message,
                         "created_at": iso(event.created_at)}
                        for event in db.query(FactoryEvent).filter_by(run_id=run.id).order_by(FactoryEvent.id)]
    return result


def emit(run_id, agent, stage, message, status="running", detail=None):
    with SessionLocal() as db:
        run = db.get(FactoryRun, run_id)
        run.stage = stage
        run.heartbeat_at = datetime.utcnow()
        db.add(FactoryEvent(run_id=run_id, agent_id=agent, stage=stage,
                            status=status, message=message, detail=detail or {}))
        db.commit()


def check_cancel(run_id):
    with SessionLocal() as db:
        run = db.get(FactoryRun, run_id)
        if not run or run.cancel_requested:
            raise RunCancelled("已停止后续任务，当前步骤产物已保留")


def queue_available():
    try:
        import redis
        return bool(redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1).ping())
    except Exception:
        return False


def start_run(db, target_count=None, trigger="manual", schedule_key=None, release_slot_ids=None):
    config = config_dict(get_config(db))
    target = target_count or config["daily_target"]
    if not 1 <= target <= 10:
        raise ValueError("每轮产量应为 1–10 条")
    config["max_llm_calls"] = max(config["max_llm_calls"], target * 12 + 12)
    config["engagement_required"] = True
    if release_slot_ids is not None:
        config["release_slot_ids"] = list(release_slot_ids)
    if not queue_available():
        raise RuntimeError("任务队列未连接，请检查 Redis")
    if db.query(FactoryRun).filter_by(status="paused_memory").first():
        raise ValueError("存在因内存暂停的批次，请先恢复或取消该批次")
    if db.query(FactoryRun).filter(FactoryRun.active_slot.isnot(None)).first():
        raise ValueError("已有生产任务在运行，请等待完成或先取消")
    pressure = None
    try:
        ensure_memory_available()
    except MemoryPressure as exc:
        pressure = exc
    run = FactoryRun(target_count=target, trigger=trigger, schedule_key=schedule_key, settings_snapshot=config)
    if pressure:
        run.status, run.stage, run.error = "paused_memory", "paused_memory", str(pressure)
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError("该生产计划已创建或已有任务在运行") from None
    if pressure:
        db.add(FactoryEvent(run_id=run.id, agent_id="director", stage="paused_memory", status="paused",
            message=f"内存保护未启动本轮：{pressure}。需手动恢复。", detail=pressure.snapshot))
        db.commit()
        return run
    try:
        from tasks.factory_tasks import produce_run
        task = produce_run.delay(run.id)
        run.task_id = task.id
        db.commit()
    except Exception:
        run.status, run.active_slot, run.finished_at = "failed", None, datetime.utcnow()
        run.error = "任务入队失败，请检查 worker 与 Redis 后重试"
        db.commit()
        raise RuntimeError(run.error) from None
    return run


def _sources_for(source_ids, all_sources):
    valid_ids = {source["id"] for source in all_sources}
    if not isinstance(source_ids, list) or not source_ids or not all(isinstance(s, str) for s in source_ids) or not set(source_ids).issubset(valid_ids):
        raise ValueError("选题包含无效或不存在的引用来源")
    selected = [source for source in all_sources if source["id"] in source_ids]
    if not selected:
        raise ValueError("选题没有可核验的来源")
    return selected


def _save_item(item_id, **updates):
    with SessionLocal() as db:
        item = db.get(FactoryItem, item_id)
        for key, value in updates.items():
            setattr(item, key, value)
        db.commit()


def _make_plan(run_id):
    with SessionLocal() as db:
        run = db.get(FactoryRun, run_id)
        sources, plan, target = run.sources or [], run.plan or {}, run.target_count
        visual_style = run.settings_snapshot.get("visual_style", "cards")
        recent = [item.content.get("title", "") for item in db.query(FactoryItem).filter(FactoryItem.run_id != run_id).order_by(FactoryItem.created_at.desc()).limit(40)]
    if not sources:
        check_cancel(run_id)
        emit(run_id, "researcher", "researching", "正在采集 AI 官方资料，保存来源和发布时间")
        from services.factory_research import collect_sources
        research = collect_sources(limit=min(24, max(10, target * 3)))
        sources = research["sources"]
        if not sources:
            raise RuntimeError("没有获取到可靠资料，已停止生产以免生成无依据内容")
        with SessionLocal() as db:
            db.get(FactoryRun, run_id).sources = sources
            db.commit()
        emit(run_id, "researcher", "researching", f"已采集 {len(sources)} 份官方资料", "completed", {"warnings": research.get("warnings", [])})
    if not plan.get("candidates"):
        if visual_style == "tech_montage":
            from services.factory_visuals import collect_visuals
            from services.factory_research import VISUAL_SOURCE_URLS
            catalog_urls = {url for _, url in VISUAL_SOURCE_URLS}
            visual_sources = sorted(sources, key=lambda s: s["url"] not in catalog_urls)[:8]
            emit(run_id, "researcher", "visual_preflight", "选题前核查动态素材；无匹配画面的题材保留在资料库，暂不进入混剪")
            for source in visual_sources:
                check_cancel(run_id)
                ensure_memory_available()
                visuals = collect_visuals([source], FACTORY_DIR / run_id / "source-intake" / source["id"])
                source["visual_ready"] = visuals.get("status") == "ready"
                source["visual_assets"] = [{k: a.get(k) for k in ("id", "description", "topic_tags")} for a in visuals.get("assets", [])]
            with SessionLocal() as db:
                db.get(FactoryRun, run_id).sources = sources
                db.commit()
            visual_ready = [s for s in sources if s.get("visual_ready")]
            if not visual_ready:
                raise ValueError("选题预检未找到足够动态素材，保留资料但不进入配音与渲染")
            usable = [s for s in sources if s.get("kind") != "reference_index"]
        else:
            usable = sources
        emit(run_id, "researcher", "researching", "正在依据资料筛选选题，区分新闻与常青教程")
        research = call_agent(run_id, "researcher", {"target": target + 2, "sources": usable,
            "recent_titles": recent, "strategy": STRATEGY,
            "visual_constraint": "可以解释近期论文或新闻，必须同时引用事实来源与同主题visual_ready的素材来源；明确区分本事件画面与相关背景演示。无同主题画面则不要强凑，不能把别家的演示说成论文实验。优先不同事件和研究，避免一个产品拆成多条重复内容。"}, max_tokens=max(3500, (target + 2) * 550))
        candidates = research.get("candidates", [])
        if not isinstance(candidates, list) or len(candidates) < target:
            raise ValueError("情报员提供的有效选题不足")
        source_ids = {source["id"] for source in usable}
        candidates = [c for c in candidates if isinstance(c, dict) and c.get("title") and isinstance(c.get("source_ids"), list) and c["source_ids"] and set(c["source_ids"]).issubset(source_ids)]
        if len(candidates) < target:
            raise ValueError("候选选题的引用来源不足，未进入制作")
        plan = {"candidates": candidates}
        with SessionLocal() as db:
            db.get(FactoryRun, run_id).plan = plan
            db.commit()
        emit(run_id, "researcher", "researching", f"提交 {len(candidates)} 个有来源的候选选题", "completed")
    with SessionLocal() as db:
        if db.query(FactoryItem).filter_by(run_id=run_id).count():
            return
    emit(run_id, "director", "planning", f"负责人正在选择 {target} 个选题并安排制作")
    decision = call_agent(run_id, "director", {"target": target, "candidates": plan["candidates"], "recent_titles": recent})
    selected = decision.get("selected", [])
    indices = [entry.get("candidate_index") for entry in selected if isinstance(entry, dict)]
    if len(indices) != target or len(set(indices)) != target or any(type(i) is not int or i < 0 or i >= len(plan["candidates"]) for i in indices):
        raise ValueError("负责人未提交有效且互不重复的生产计划")
    plan = {**plan, "decision": decision}
    with SessionLocal() as db:
        db.get(FactoryRun, run_id).plan = plan
        for position, idx in enumerate(indices):
            db.add(FactoryItem(run_id=run_id, position=position, brief=plan["candidates"][idx]))
        db.commit()
    emit(run_id, "director", "planning", decision.get("reason", "生产计划已下达"), "completed")


def _ensure_visual_support(run_id, item_id):
    with SessionLocal() as db:
        item, run = db.get(FactoryItem, item_id), db.get(FactoryRun, run_id)
        if run.settings_snapshot.get("visual_style") != "tech_montage" or item.brief.get("visual_source_ids"):
            return
        facts = _sources_for(item.brief.get("source_ids", []), run.sources)
        if any(s.get("visual_ready") for s in facts):
            return
        ready = [s for s in run.sources if s.get("visual_ready")]
        brief = dict(item.brief)
        owner = editor_id(item)
    match = call_agent(run_id, owner, {"task_mode": "visual_match", "brief": brief,
        "fact_sources": [{"id": s["id"], "title": s["title"], "text": s["text"][:2000]} for s in facts],
        "visual_sources": [{"id": s["id"], "title": s["title"], "visual_assets": s.get("visual_assets", [])} for s in ready]}, max_tokens=1200)
    ids = match.get("visual_source_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 2 or any(not isinstance(i, str) or i not in {s["id"] for s in ready} for i in ids):
        raise ReviewRejected("该选题没有相符的可用画面，先补素材或更换选题，未进入配音")
    if not isinstance(match.get("reason"), str) or not match["reason"].strip():
        raise ReviewRejected("未说明背景演示与事实的关系，未进入配音")
    _save_item(item_id, brief={**brief, "visual_source_ids": ids, "visual_support_reason": match["reason"], "background_visuals": True})
    emit(run_id, owner, "visual_handoff", "先确认画面支持，再交给小文：" + match["reason"], "completed", {"item_id": item_id, "visual_source_ids": ids})


def _write_and_review(run_id, item_id):
    with SessionLocal() as db:
        item, run = db.get(FactoryItem, item_id), db.get(FactoryRun, run_id)
        if item.content and item.review.get("approved") is True:
            return
        brief, prior, feedback = item.brief, item.content or {}, item.review or {}
        sources = _sources_for([*brief.get("source_ids", []), *brief.get("visual_source_ids", [])], run.sources)
        max_revisions, revisions = run.settings_snapshot.get("max_revisions", 1), item.revisions
        engagement_required = run.settings_snapshot.get("engagement_required", False)
        target_duration = run.settings_snapshot.get("target_duration_seconds")
        calibrated_characters = brief.get("target_script_characters")
        recent_prompts = [i.content.get("follow_prompt") for i in db.query(FactoryItem).filter(
            FactoryItem.status == "completed").order_by(FactoryItem.created_at.desc()).limit(10) if i.content.get("follow_prompt")]
    for attempt in range(revisions, max_revisions + 1):
        check_cancel(run_id)
        _save_item(item_id, status="writing", revisions=attempt)
        emit(run_id, "writer", "writing", f"{'修改' if prior else '撰写'}脚本：{brief['title']}")
        content = call_agent(run_id, "writer", {"brief": brief, "sources": sources, "previous_script": prior,
            "review_feedback": feedback, "strategy": STRATEGY, "recent_follow_prompts": recent_prompts,
            "target_duration_seconds": target_duration,
            "suggested_script_characters": calibrated_characters or (round(target_duration * 4.5) if target_duration else 220)},
            max_tokens=4200 if target_duration else 2400)
        try:
            minimum, maximum = (round(target_duration * 3.3), round(target_duration * 5.7)) if target_duration else (80, 650)
            if type(calibrated_characters) is int and calibrated_characters > 0:
                minimum, maximum = round(calibrated_characters * .95), round(calibrated_characters * 1.05)
            if not isinstance(content.get("script"), str) or not minimum <= len(content["script"]) <= maximum:
                actual = len(content["script"]) if isinstance(content.get("script"), str) else 0
                raise ValueError(f"本次script实测{actual}字符，必须为{minimum}–{maximum}字符；按目标时长讲清背景、例子、原理与边界，不要仅返回提纲")
            if not isinstance(content.get("title"), str) or not content["title"].strip():
                raise ValueError("文案缺少标题")
            _sources_for(content.get("source_ids", []), sources)
            if engagement_required:
                validate_follow_prompt(content)
        except ValueError as exc:
            feedback = {"approved": False, "reason": str(exc)}
            prior = content
            _save_item(item_id, review=feedback)
            emit(run_id, "director", "script_revision", "退回小文修正交付格式：" + str(exc), "rejected")
            if attempt < max_revisions:
                continue
            raise ReviewRejected(str(exc)) from None
        content["sources"] = [{k: s.get(k) for k in ("id", "title", "url", "published_at", "fetched_at", "kind")} for s in sources]
        _save_item(item_id, content=content, status="reviewing")
        emit(run_id, "writer", "writing", f"脚本已提交：{content['title']}", "completed")
        emit(run_id, "reviewer", "reviewing", f"独立核对文案事实与出处：{content['title']}")
        review = call_agent(run_id, "reviewer", {"task_mode": "script_review", "script": content, "sources": sources})
        score = review.get("score", 0)
        approved = review.get("approved") is True and type(score) in (int, float) and score >= 80
        review["approved"] = approved
        _save_item(item_id, review=review)
        emit(run_id, "reviewer", "reviewing", f"{'审核通过' if approved else '要求返工'}：{review.get('reason', '')}", "completed" if approved else "rejected")
        if approved:
            if engagement_required:
                emit(run_id, "reviewer", "engagement_check", "开场与关注理由已通过脚本审核，将交给小麦适配平台：" + content["follow_prompt"],
                     "completed", validate_follow_prompt(content))
            return
        prior, feedback = content, review
        if attempt < max_revisions:
            emit(run_id, "director", "revision", f"安排编剧返工第 {attempt + 1} 次，保留审核意见", "completed")
    raise ReviewRejected("文案未通过审核，已达到本轮返工上限")


def _replace_rejected_topic(run_id, item_id):
    """The director may substitute one researched reserve topic, within the run budget."""
    with SessionLocal() as db:
        run, item = db.get(FactoryRun, run_id), db.get(FactoryItem, item_id)
        if item.brief.get("replacement_count", 0) >= 1:
            return False
        used = {i.brief.get("title") for i in db.query(FactoryItem).filter_by(run_id=run_id)}
        rejected = set(run.plan.get("rejected_titles", []))
        candidates = [c for c in run.plan.get("candidates", []) if c.get("title") not in used | rejected]
        if not candidates:
            return False
        old_title, feedback = item.brief.get("title"), item.review
    emit(run_id, "director", "replacement", "当前选题返工后仍未通过审核，负责人正在评估备用选题")
    decision = call_agent(run_id, "director", {"target": 1, "candidates": candidates,
        "recent_titles": list(used | rejected), "rejected_title": old_title, "review_feedback": feedback})
    selected = decision.get("selected", [])
    if selected == []:
        return False
    if len(selected) != 1:
        raise ValueError("备用选题决策不符合约定")
    index = selected[0].get("candidate_index")
    if type(index) is not int or not 0 <= index < len(candidates):
        raise ValueError("备用选题索引无效")
    with SessionLocal() as db:
        run, item = db.get(FactoryRun, run_id), db.get(FactoryItem, item_id)
        run.plan = {**run.plan, "rejected_titles": [*run.plan.get("rejected_titles", []), old_title]}
        item.brief = {**candidates[index], "replacement_count": 1, "editor_id": editor_id(item)}
        item.content, item.review, item.revisions, item.status = {}, {}, 0, "planned"
        db.commit()
    emit(run_id, "director", "replacement", f"改用备用选题：{candidates[index]['title']}", "completed",
         {"rejected_title": old_title, "review": feedback, "decision": decision})
    return True


def _plan_edit(run_id, content, assets, visual_style, owner="producer"):
    available = [{k: a.get(k) for k in ("id", "description", "source_title", "topic_tags", "clips")} for a in assets]
    feedback = {}
    for attempt in range(2):
        check_cancel(run_id)
        arrangement = call_agent(run_id, owner, {"content": content, "visual_style": visual_style,
            "available_assets": available, "editorial_feedback": feedback}, max_tokens=6500)
        scenes = arrangement.get("scenes", [])
        try:
            if not isinstance(scenes, list) or not 2 <= len(scenes) <= 18 or not all(isinstance(s, dict) and s.get("heading") and isinstance(s.get("body", ""), str) for s in scenes):
                raise ValueError("制作员未提供有效的画面安排")
            if assets:
                validate_editorial_scenes(content["script"], scenes, assets)
                review = call_agent(run_id, "reviewer", {"task_mode": "edit_review",
                    "script": content, "arrangement": arrangement, "available_assets": available,
                    "review_scope": "仅核对已提供素材描述与口播，未直接观看视频，不得声称成片视觉已验收"})
                if review.get("approved") is not True or type(review.get("score")) not in (int, float) or review["score"] < 80:
                    feedback = review
                    raise ValueError("口播与画面搭配未通过审核：" + str(review.get("reason", ""))[:300])
                arrangement["editorial_review"] = {**review, "scope": "source_description_alignment"}
                emit(run_id, "reviewer", "edit_review", "分镜与素材描述核对通过，开始制作", "completed", arrangement["editorial_review"])
            return arrangement
        except ValueError as exc:
            feedback = {**feedback, "error": str(exc), "required_fields": ["heading", "body", "narration_exact", "asset_ids", "match_reason"],
                        "previous_arrangement": arrangement}
            emit(run_id, "director", "edit_revision", f"要求{AGENT_MAP[owner]['name']}重排口播与镜头：" + str(exc), "rejected")
            if attempt:
                raise


def _render(run_id, item_id):
    with SessionLocal() as db:
        item = db.get(FactoryItem, item_id)
        owner = editor_id(item)
        content, media = item.content, item.media or {}
        brief = item.brief
        run_sources = db.get(FactoryRun, run_id).sources
        visual_style = db.get(FactoryRun, run_id).settings_snapshot.get("visual_style", "cards")
        target_duration = db.get(FactoryRun, run_id).settings_snapshot.get("target_duration_seconds")
        if media.get("video_path") and Path(media["video_path"]).is_file():
            return
    check_cancel(run_id)
    ensure_memory_available()
    _save_item(item_id, status="rendering")
    directory = FACTORY_DIR / run_id / item_id
    assets = []
    if visual_style == "tech_montage":
        emit(run_id, "researcher", "visual_research", "正在获取与本条来源对应的真实演示视频")
        from services.factory_visuals import collect_visuals
        visual_sources = list(content.get("sources", []))
        if brief.get("visual_source_ids"):
            visual_sources.extend(_sources_for(brief["visual_source_ids"], run_sources))
            content = {**content, "background_visuals": True, "visual_support_reason": brief.get("visual_support_reason")}
        visuals = collect_visuals(visual_sources, directory / "visual-assets")
        assets = visuals.get("assets", [])
        if visuals.get("status") != "ready" or len(assets) < 3:
            raise ValueError("匹配选题的动态演示素材不足3条，已暂停该条制作；请更换选题或补充素材")
        emit(run_id, "researcher", "visual_research", f"已获取 {len(assets)} 条有出处的动态演示素材", "completed",
             {"manifest_path": str(visuals.get("manifest_path", "")), "warnings": visuals.get("warnings", [])})
    elif visual_style != "cards":
        raise ValueError("不支持的画面风格")
    emit(run_id, owner, "rendering", f"规划演示镜头并合成配音：{content['title']}" if assets else f"规划画面并合成真实配音：{content['title']}")
    arrangement = _plan_edit(run_id, content, assets, visual_style, owner=owner)
    scenes = arrangement.get("scenes", [])
    if content.get("background_visuals"):
        scenes = [{**scene, "evidence_role": "background"} for scene in scenes]
    if not isinstance(scenes, list) or not 2 <= len(scenes) <= 18 or not all(isinstance(s, dict) and s.get("heading") and isinstance(s.get("body", ""), str) for s in scenes):
        raise ValueError("制作员未提供有效的画面安排")
    if assets:
        known = {asset["id"] for asset in assets}
        if any(not isinstance(s.get("asset_ids"), list) or not s["asset_ids"]
               or not all(isinstance(a, str) and a in known for a in s["asset_ids"]) for s in scenes):
            raise ValueError("镜头计划引用了不存在的演示素材")
    content = {**content, "editor_id": owner, "scenes": scenes, "visual_note": arrangement.get("visual_note", ""),
               "editorial_review": arrangement.get("editorial_review", {})}
    _save_item(item_id, content=content)
    if visual_style == "tech_montage":
        from services.factory_voice import synthesize_factory_audio
        from services.factory_montage import render_tech_montage
        voice = synthesize_factory_audio(item_id, content, directory)
        check_cancel(run_id)
        if target_duration and abs(voice["duration"] - target_duration) > 15:
            with SessionLocal() as db:
                item = db.get(FactoryItem, item_id)
                if item.brief.get("duration_revisions", 0) >= 1:
                    raise ValueError(f"配音实际{voice['duration']:.1f}秒，未达到{target_duration}±15秒，已保留并暂停此条")
                suggested = round(len(content["script"]) * target_duration / voice["duration"])
                item.brief = {**item.brief, "duration_revisions": 1, "target_script_characters": suggested}
                item.revisions = 0
                item.review = {"approved": False, "reason": f"实际配音{voice['duration']:.1f}秒，目标{target_duration}秒。按约{suggested}字重写，增加有依据的讲解或删去重复，不能拉慢语音、填充静音或循环画面凑时长。"}
                db.commit()
            emit(run_id, "director", "duration_revision", f"按实测配音时长修订一次，目标{target_duration}秒", "warning")
            _write_and_review(run_id, item_id)
            return _render(run_id, item_id)
        media = render_tech_montage(item_id, content, voice["audio_path"], voice["subtitle_path"], assets, directory)
        media.update(tts_engine=voice["tts_engine"], speech_timings_path=voice["speech_timings_path"], style="tech_montage")
    else:
        from services.factory_media import render_factory_video
        media = render_factory_video(item_id, content, directory)
        media["style"] = "cards"
    for key in ("video_path", "audio_path", "cover_path", "subtitle_path"):
        if not media.get(key) or not Path(media[key]).is_file():
            raise ValueError(f"成片交付缺少 {key}")
    if float(media.get("duration", 0)) < 8:
        raise ValueError("生成的视频时长不足，未通过验收")
    if target_duration and abs(media["duration"] - target_duration) > 15:
        raise ValueError(f"成片未达到{target_duration}±15秒，未进入发布队列")
    media = json.loads(json.dumps(media, default=str))
    _save_item(item_id, media=media, status="adapting")
    if media.get("edl_path"):
        from services.factory_visuals import record_asset_usage
        record_asset_usage(item_id, media)
    emit(run_id, owner, "rendering", f"成片已生成，时长 {media['duration']:.1f} 秒", "completed")
    emit(run_id, "reviewer", "media_review", "成片文件、音轨、时长与编码检查完成", "completed", media.get("quality", {}))


def _package(run_id, item_id):
    with SessionLocal() as db:
        item, run = db.get(FactoryItem, item_id), db.get(FactoryRun, run_id)
        content, media, existing = item.content, item.media, item.packages or []
        platforms = run.settings_snapshot["platforms"]
        if len(existing) == len(platforms) and all(Path(p["path"]).is_file() for p in existing):
            _save_item(item_id, status="completed", error=None)
            return
    check_cancel(run_id)
    emit(run_id, "publisher", "adapting", f"准备 {len(platforms)} 个平台的标题、简介、封面及素材包：{content['title']}")
    adapted = call_agent(run_id, "publisher", {"content": content, "platforms": platforms}, max_tokens=3000)
    variants = adapted.get("platforms", [])
    if not isinstance(variants, list) or len(variants) != len(platforms) or {p.get("platform") for p in variants} != set(platforms):
        raise ValueError("运营员未交付全部平台版本")
    if run.settings_snapshot.get("engagement_required"):
        emit(run_id, "publisher", "engagement_handoff", "小麦已依据含关注提醒的完整口播生成平台文案",
             "completed", validate_follow_prompt(content))
    packages = []
    directory = FACTORY_DIR / run_id / item_id
    for variant in variants:
        platform = variant["platform"]
        if not isinstance(variant.get("title"), str) or not variant["title"].strip():
            raise ValueError("平台版本缺少标题")
        manifest = {**variant, "status": "package_ready", "published": False,
                    "source_item_id": item_id, "sources": content.get("sources", []),
                    "visual_sources": media.get("visual_sources", []),
                    "note": "素材已准备完成，尚未上传或发布。需要连接平台后执行发布。"}
        path = directory / f"{platform}.zip"
        temp = directory / f"{platform}.zip.tmp"
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for key, name in (("video_path", "video.mp4"), ("audio_path", "audio.mp3"), ("cover_path", "cover.png"), ("subtitle_path", "subtitles.ass")):
                archive.write(media[key], name)
            archive.writestr("metadata.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr("script.txt", content["script"])
            archive.writestr("发布说明.txt", manifest["note"] + "\n\n" + variant.get("caption", ""))
            if media.get("visual_sources"):
                archive.writestr("visual-sources.json", json.dumps(media["visual_sources"], ensure_ascii=False, indent=2))
                for source in media["visual_sources"]:
                    if source.get("license_url") == "https://github.com/allenai/ToyBlocks/blob/main/LICENSE":
                        license_file = Path(__file__).resolve().parents[1] / "licenses/ToyBlocks-LICENSE.txt"
                        if license_file.is_file():
                            archive.write(license_file, "licenses/ToyBlocks-LICENSE.txt")
                        break
            if media.get("edl_path"):
                edl = Path(media["edl_path"]).resolve()
                if edl.is_relative_to(FACTORY_DIR.resolve()) and edl.is_file():
                    archive.write(edl, "edit-decision-list.json")
        os.replace(temp, path)
        packages.append({"platform": platform, "title": variant["title"], "caption": variant.get("caption", ""),
                         "tags": variant.get("tags", []), "published": False,
                         "status": "package_ready", "path": str(path)})
    _save_item(item_id, packages=packages, status="completed", error=None)
    emit(run_id, "publisher", "adapting", f"已交付 {len(packages)} 份平台素材包，等待平台接入", "completed")


def _finish_report(run_id):
    with SessionLocal() as db:
        run = db.get(FactoryRun, run_id)
        items = db.query(FactoryItem).filter_by(run_id=run_id).all()
        completed = sum(item.status == "completed" for item in items)
        packages = sum(len(item.packages or []) for item in items if item.status == "completed")
        production = {"completed": completed, "target": run.target_count, "packages": packages,
                      "published": 0, "submitted": sum(p.get("status") == "submitted" for i in items for p in i.packages or []),
                      "failed": sum(item.status == "failed" for item in items)}
        stats = {"production": production, "llm_calls": run.llm_calls,
                 "input_tokens": run.input_tokens, "output_tokens": run.output_tokens,
                 "elapsed_seconds": round((datetime.utcnow() - run.created_at).total_seconds()),
                 "errors": [item.error for item in items if item.error], "platform_data_available": False,
                 "growth": {"status": "not_connected", "net_followers": None, "follow_conversion": None,
                            "completion_rate": None, "note": STRATEGY["measurement_note"]}}
    check_cancel(run_id)
    emit(run_id, "analyst", "reporting", "根据真实产量、审核和失败记录生成复盘")
    try:
        analysis = call_agent(run_id, "analyst", {**stats, "metric_definitions": {
            "completed": "原创成片数量", "packages": "按本轮启用平台生成的输出版本数量，不是输入素材",
            "failed": "当前尚未完成的作品数量，不是历史失败次数",
            "prohibited_inferences": "没有素材消耗量或素材利用率指标，不得用成片数除以平台版本数推断利用率。"
        }})
    except (RunCancelled, MemoryPressure):
        raise
    except Exception as exc:
        analysis = {"summary": f"完成 {completed} 条原创视频，准备 {packages} 份平台素材包。", "recommendations": ["模型复盘暂不可用，请查看生产日志。"]}
        emit(run_id, "analyst", "reporting", f"模型复盘失败，已保留真实统计：{str(exc)[:250]}", "failed")
    report = {**analysis, **stats, "summary": production_summary(production)}
    with SessionLocal() as db:
        run = db.get(FactoryRun, run_id)
        report.update(llm_calls=run.llm_calls, input_tokens=run.input_tokens, output_tokens=run.output_tokens)
        run.report, run.completed_count = report, completed
        run.status = "completed" if completed == run.target_count else ("partial" if completed else "failed")
        run.error = None if run.status == "completed" else "部分或全部视频未完成，请查看单条错误并重试"
        run.finished_at, run.active_slot = datetime.utcnow(), None
        db.commit()
    emit(run_id, "analyst", "reporting", report.get("summary", "生产报告已生成"), "completed")
    emit(run_id, "director", "finished", f"本轮验收：{completed}/{production['target']} 条成片，{packages} 份平台素材包", "completed")


def execute_run(run_id):
    """Checkpoints make re-delivery safe. A Redis lease prevents double workers."""
    import redis
    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=3)
    lock = client.lock(f"short-video-ai-factory:run:{run_id}", timeout=180, thread_local=False)
    if not lock.acquire(blocking=False):
        return {"status": "already_running"}
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(40):
            try:
                lock.extend(180, replace_ttl=True)
                with SessionLocal() as db:
                    run = db.get(FactoryRun, run_id)
                    if run:
                        run.heartbeat_at = datetime.utcnow()
                        db.commit()
            except Exception:
                # Worker will not schedule new work if its lease has been lost.
                stop.set()

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        with SessionLocal() as db:
            run = db.get(FactoryRun, run_id)
            if not run or run.status in TERMINAL:
                return {"status": run.status if run else "missing"}
            run.status, run.error = "running", None
            db.commit()
        check_cancel(run_id)
        ensure_memory_available()
        emit(run_id, "director", "starting", "生产团队已开工，按预算和质量门槛逐步交接")
        _make_plan(run_id)
        with SessionLocal() as db:
            assign_editors(db, run_id)
            selected = db.get(FactoryRun, run_id).settings_snapshot.get("retry_item_ids")
            item_ids = [item.id for item in db.query(FactoryItem).filter_by(run_id=run_id).order_by(FactoryItem.position)
                        if not selected or item.id in selected]
        for item_id in item_ids:
            check_cancel(run_id)
            ensure_memory_available()
            if stop.is_set():
                raise RuntimeError("生产锁已失效，停止调度，等待恢复")
            with SessionLocal() as db:
                already_complete = db.get(FactoryItem, item_id).status == "completed"
            try:
                if not already_complete:
                    try:
                        _ensure_visual_support(run_id, item_id)
                        _write_and_review(run_id, item_id)
                    except ReviewRejected as exc:
                        _save_item(item_id, review={"approved": False, "reason": str(exc)})
                        if not _replace_rejected_topic(run_id, item_id):
                            raise
                        _ensure_visual_support(run_id, item_id)
                        _write_and_review(run_id, item_id)
                    _render(run_id, item_id)
                    _package(run_id, item_id)
                with SessionLocal() as db:
                    snapshot = db.get(FactoryRun, run_id).settings_snapshot
                    auto_publish = snapshot.get("auto_publish_xiaohongshu", False) and not snapshot.get("publish_enabled", False)
                if auto_publish:
                    try:
                        from services.factory_publishing import auto_submit
                        auto_submit(item_id, run_id)
                    except (RunCancelled, MemoryPressure):
                        raise
                    except Exception as exc:
                        emit(run_id, "publisher", "platform_submission", f"成片已保留，小红书提交未完成：{str(exc)[:300]}", "warning")
                with SessionLocal() as db:
                    db.get(FactoryRun, run_id).completed_count = db.query(FactoryItem).filter_by(run_id=run_id, status="completed").count()
                    db.commit()
            except (RunCancelled, MemoryPressure):
                raise
            except Exception as exc:
                _save_item(item_id, status="failed", error=str(exc)[:1000])
                emit(run_id, "director", "item_failed", f"单条生产未完成：{str(exc)[:300]}；继续其他选题", "failed")
        _finish_report(run_id)
        return {"status": "finished", "run_id": run_id}
    except MemoryPressure as exc:
        with SessionLocal() as db:
            run = db.get(FactoryRun, run_id)
            if run:
                run.status, run.stage = "paused_memory", "paused_memory"
                run.error, run.active_slot, run.finished_at = str(exc), "factory", None
                run.completed_count = db.query(FactoryItem).filter_by(run_id=run_id, status="completed").count()
                db.commit()
                emit(run_id, "director", "paused_memory", f"内存保护已暂停本轮：{exc}。已保存进度，需手动恢复。", "paused", exc.snapshot)
        return {"status": "paused_memory", "run_id": run_id}
    except Exception as exc:
        cancelled = isinstance(exc, RunCancelled)
        with SessionLocal() as db:
            run = db.get(FactoryRun, run_id)
            if run:
                run.status = "cancelled" if cancelled else "failed"
                run.error, run.finished_at, run.active_slot = str(exc)[:1000], datetime.utcnow(), None
                db.commit()
                emit(run_id, "director", "cancelled" if cancelled else "failed", str(exc)[:500], "cancelled" if cancelled else "failed")
        return {"status": "cancelled" if cancelled else "failed", "run_id": run_id}
    finally:
        stop.set()
        thread.join(timeout=2)
        try:
            lock.release()
        except redis.exceptions.LockError:
            pass
