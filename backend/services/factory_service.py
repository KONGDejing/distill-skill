"""Factory application service and durable production workflow."""
import json
import os
import re
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy.exc import IntegrityError
from config import settings
from database import SessionLocal
from models.factory import FactoryConfig, FactoryRun, FactoryItem, FactoryEvent, FactoryPublication, FactoryRelease
from agents.registry import AGENTS, AGENT_MAP, PLATFORMS
from services.factory_llm import call_agent, RunCancelled, ModelServiceUnavailable
from services.factory_resources import MemoryPressure, ensure_memory_available
from services.factory_editorial import STRATEGY, validate_editorial_scenes, validate_follow_prompt
from services.factory_team import assign_editors, editor_id, editor_quotas, validate_editor_plan, candidate_editor
from services.factory_usage import item_usage, batch_usage, usage_context, summarize_usage

TERMINAL = {"completed", "partial", "failed", "cancelled", "paused_memory"}
FACTORY_DIR = settings.STORAGE_DIR / "factory"


class ReviewRejected(ValueError):
    pass


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def config_dict(config):
    from services.factory_platform_policy import automation_state
    keys = ("daily_target", "target_duration_seconds", "sector", "schedule_enabled", "schedule_time", "timezone", "platforms", "max_revisions", "max_llm_calls", "auto_publish_xiaohongshu", "visual_style", "publish_enabled", "publish_times", "manual_delivery_enabled")
    return {**{key: getattr(config, key) for key in (*keys, "publish_jitter_seconds")},
            "editor_quotas": editor_quotas(min(config.daily_target, 6)),
            "distribution_policy": "single-platform-v1", "platform_daily_limit": 2,
            "platform_automation": {p: automation_state(p) for p in config.platforms}}


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
    from services.factory_filenames import safe_filename
    result = run_dict(run)
    result["report"] = run.report or {}
    result["plan"] = run.plan or {}
    result["items"] = []
    current_config = db.get(FactoryConfig, 1)
    publishing_enabled = bool(current_config and current_config.publish_enabled)
    items = db.query(FactoryItem).filter_by(run_id=run.id).order_by(FactoryItem.position).all()
    events = db.query(FactoryEvent).filter_by(run_id=run.id).order_by(FactoryEvent.id).all()
    usage = summarize_usage(events, [item.id for item in items])
    result["token_usage"] = {key: value for key, value in usage.items() if key != "items"}
    for item in items:
        content, media = item.content or {}, item.media or {}
        preview = media.get("preview") or {}
        from services.factory_platform_policy import automation_state, effective_automation_enabled
        packages = [{**package, "download_url": f"/api/factory/items/{item.id}/packages/{package['platform']}",
                     "automation": automation_state(package['platform']),
                     "publishing_enabled": publishing_enabled and effective_automation_enabled(package['platform']),
                     "copy_url": storage_url(package.get("copy_path"))} for package in item.packages or []]
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
            "token_usage": {**usage["items"][item.id], "history_may_be_incomplete": usage["has_unattributed_history"]},
            "editor_id": editor_id(item), "editor_name": AGENT_MAP[editor_id(item)]["name"],
            "distribution": item.brief.get("distribution"),
            "category": item.brief.get("category", "AI 科技"), "status": item.status,
            "script": content.get("script", ""), "hook": content.get("hook", ""),
            "engagement_plan": item.brief.get("engagement_plan"),
            "research_packet": item.brief.get("research_packet"),
            "scenes": content.get("scenes", []), "sources": content.get("sources", []),
            "review": item.review or {}, "media_quality": media.get("quality", {}),
            "video_url": storage_url(next((p["video_path"] for p in item.packages or [] if p.get("video_path")), media.get("video_path"))),
            "webm_url": None if any(p.get("video_path") for p in item.packages or []) else storage_url(media.get("webm_path")),
            "video_download_url": f"/api/factory/items/{item.id}/video" if media.get("video_path") and content.get("title") else None,
            "video_filename": safe_filename(content["title"], ".mp4") if content.get("title") else None,
            "visual_style": media.get("style") or media.get("renderer_kind", "cards"),
            "audio_url": storage_url(media.get("audio_path")),
            "cover_url": storage_url(next((p["cover_path"] for p in item.packages or [] if p.get("cover_path")), media.get("cover_path"))),
            "cover_download_url": f"/api/factory/items/{item.id}/cover", "duration": media.get("duration"),
            "tts_engine": media.get("tts_engine"), "packages": packages, "error": item.error,
            "voice_profile": media.get("voice_profile"), "voice_label": media.get("voice_label"),
            "preview": {"video_url": storage_url(preview.get("video_path")),
                        "title": preview.get("title"), "quality": preview.get("quality", {}),
                        "hook": preview.get("hook"), "script_url": storage_url(preview.get("narration_text_path")),
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
                        for event in events]
    return result


def emit(run_id, agent, stage, message, status="running", detail=None):
    with SessionLocal() as db:
        run = db.get(FactoryRun, run_id)
        run.stage = stage
        run.heartbeat_at = datetime.utcnow()
        db.add(FactoryEvent(run_id=run_id, agent_id=agent, stage=stage,
                            status=status, message=message, detail={**usage_context(run_id), **(detail or {})}))
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


def start_run(db, target_count=None, trigger="manual", schedule_key=None, release_slot_ids=None, editor_quota_override=None):
    config = config_dict(get_config(db))
    target = target_count or config["daily_target"]
    if not 1 <= target <= 6:
        raise ValueError("每轮产量应为 1–6 条")
    config["editor_quotas"] = editor_quotas(target)
    if editor_quota_override is not None:
        if (set(editor_quota_override) != set(config["editor_quotas"])
                or any(type(v) is not int or not 0 <= v <= 2 for v in editor_quota_override.values())
                or sum(editor_quota_override.values()) != target):
            raise ValueError("剩余剪辑配额必须与本轮数量一致，且每人不超过2条")
        config["editor_quotas"] = dict(editor_quota_override)
    config["auto_publish_xiaohongshu"] = False
    config["publish_enabled"] = False
    config["max_llm_calls"] = max(config["max_llm_calls"], target * 12 + 12)
    config["engagement_required"] = True
    config["director_hook_required"] = True
    config["max_total_tokens"] = settings.FACTORY_MAX_TOTAL_TOKENS
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


def _eligible_candidates(candidates, sources, visual_style, *, with_diagnostics=False):
    """Return eligible briefs; optionally also return per-candidate rejection data.

    Source IDs identify evidence pages. Asset IDs identify already verified
    videos and are never silently rewritten into evidence references.
    """
    from services.factory_papers import is_paper_story
    import re
    if not isinstance(candidates, list):
        diagnostics = [{"index": None, "accepted": False, "reasons": [
            {"code": "candidates_not_list", "message": "candidates必须是数组"}]}]
        return ([], diagnostics) if with_diagnostics else []
    ids = {s["id"] for s in sources}
    ready = {s["id"] for s in sources if s.get("visual_ready")}
    asset_sources, fingerprints = {}, {}
    for source in sources:
        fingerprints[source["id"]] = set()
        for asset in source.get("visual_assets") or []:
            if not isinstance(asset, dict):
                continue
            if isinstance(asset.get("id"), str):
                asset_sources.setdefault(asset["id"], []).append(source["id"])
            digest = asset.get("sha256")
            if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest):
                fingerprints[source["id"]].add(digest)
    eligible, diagnostics = [], []
    for index, candidate in enumerate(candidates):
        reasons = []
        row = {"index": index, "title": candidate.get("title") if isinstance(candidate, dict) else None}
        if not isinstance(candidate, dict) or not isinstance(candidate.get("title"), str) or not candidate["title"].strip():
            diagnostics.append({**row, "accepted": False, "reasons": [{"code": "invalid_title", "message": "候选须为对象并有非空标题"}]})
            continue
        refs, visuals = candidate.get("source_ids"), candidate.get("visual_source_ids", [])
        for field, values, required in (("source_ids", refs, True), ("visual_source_ids", visuals, False)):
            if not isinstance(values, list) or (required and not values) or not all(isinstance(i, str) for i in values):
                reasons.append({"code": "invalid_reference_format", "field": field, "message": "必须填写来源source.id数组，事实引用不能为空"})
                continue
            unknown = sorted(set(values) - ids)
            if unknown:
                reasons.append({"code": "asset_ids_used_as_source_ids" if any(i in asset_sources for i in unknown) else "unknown_source_ids",
                    "field": field, "ids": unknown, "source_id_hints": {i: asset_sources[i] for i in unknown if i in asset_sources},
                    "message": "只能引用已提供的source.id；视频asset.id留到剪辑分镜阶段"})
        if not reasons:
            referenced = set(refs) | set(visuals)
            verified = set().union(*(fingerprints[i] for i in referenced))
            paper = is_paper_story(candidate, [s for s in sources if s["id"] in refs])
            unsupported = [i for i in visuals if i not in ready and not fingerprints[i]]
            if unsupported:
                reasons.append({"code": "visual_sources_unavailable", "ids": unsupported, "message": "所列画面来源尚无通过核验的视频"})
            if visual_style == "tech_montage" and not (referenced & ready or len(verified) >= 3 or paper):
                reasons.append({"code": "insufficient_verified_visuals", "verified_unique_assets": len(verified),
                    "required": 3, "message": "本候选引用来源合计不足3条按原片SHA去重的核验视频，且不是可核验论文选题"})
            row["verified_unique_assets"] = len(verified)
        if visuals and not (isinstance(candidate.get("visual_support_reason"), str) and candidate["visual_support_reason"].strip()):
            reasons.append({"code": "missing_visual_support_reason", "message": "辅助画面须说明与本题的具体关系和证据边界"})
        diagnostics.append({**row, "accepted": not reasons, "reasons": reasons})
        if not reasons:
            eligible.append(candidate)
    return (eligible, diagnostics) if with_diagnostics else eligible


@batch_usage("planning")
def _make_plan(run_id):
    with SessionLocal() as db:
        run = db.get(FactoryRun, run_id)
        sources, plan, target = run.sources or [], run.plan or {}, run.target_count
        visual_style = run.settings_snapshot.get("visual_style", "cards")
        quotas = run.settings_snapshot.get("editor_quotas")
        recent = [item.content.get("title", "") for item in db.query(FactoryItem).filter(FactoryItem.run_id != run_id).order_by(FactoryItem.created_at.desc()).limit(40)]
    if not sources:
        check_cancel(run_id)
        emit(run_id, "researcher", "researching", "正在采集 AI 官方资料，保存来源和发布时间")
        from services.factory_research import collect_sources
        research = collect_sources(limit=min(24, max(10, target * 3)))
        sources = research["sources"]
        from services.factory_newsroom import collect_newsroom_sources
        emit(run_id, "researcher3", "newsroom_intake", "小探3正在采集展会原始公告与科技硬件新闻，给小影3建立选题来源")
        newsroom = collect_newsroom_sources()
        sources = list({s["url"]: s for s in [*sources, *newsroom["sources"]]}.values())
        emit(run_id, "researcher3", "newsroom_intake", f"取得{len(newsroom['sources'])}份原始资料；视频素材仍需另行获取与核对", "completed" if newsroom["sources"] else "warning", newsroom)
        if not sources:
            raise RuntimeError("没有获取到可靠资料，已停止生产以免生成无依据内容")
        with SessionLocal() as db:
            db.get(FactoryRun, run_id).sources = sources
            db.commit()
        emit(run_id, "researcher", "researching", f"已采集 {len(sources)} 份官方资料", "completed", {
            "warnings": research.get("warnings", []), "large_model_search": research.get("large_model_search", {})})
    from services.factory_research import is_major_event
    major_events = [{"source_id": s['id'], "title": s.get('title'), "url": s['url'],
                     "published_at": s.get('published_at')} for s in sources if is_major_event(s)]
    if major_events:
        emit(run_id, 'researcher3', 'major_event_watch', '已发现重大事件原始公告；事实核查与画面制作分开跟进',
             'warning', {'events': major_events, 'instruction': '必须逐项交代选用、待核查或待补画面原因，不能因没有视频而静默漏报'})
    if not plan.get("candidates"):
        if visual_style == "tech_montage":
            from services.factory_visuals import collect_visuals
            from services.factory_research import VISUAL_SOURCE_URLS
            from services.factory_papers import is_paper_story
            import time
            catalog_urls = {url.rstrip("/") for _, url in VISUAL_SOURCE_URLS}
            owners = ("producer", "producer2", "producer3")
            buckets = {owner: [] for owner in owners}
            for source in sources:
                # Explicit research handoffs precede catalogue/discovery pages.
                hint = source.get("editor_id")
                if hint not in owners:
                    hint = {"researcher2": "producer2", "researcher3": "producer3"}.get(source.get("researcher_id"))
                owner = hint or ("producer2" if is_paper_story({}, [source]) else
                    "producer3" if source.get("kind") in {"news", "official_reference"} else "producer")
                source["visual_ready"], source["visual_assets"] = False, []
                source["visual_preflight"] = {"status": "not_checked", "editor_id": owner}
                if source.get("kind") == "reference_index":
                    source["visual_preflight"]["reason"] = "reference_index_is_not_event_evidence"
                elif quotas and not quotas.get(owner, 0):
                    source["visual_preflight"]["reason"] = "editor_has_no_quota"
                else:
                    buckets[owner].append((0 if hint else 1, source["url"].rstrip("/") not in catalog_urls, source))
            for owner in owners:
                buckets[owner].sort(key=lambda row: row[:2])
            visual_sources = []
            # A late newsroom handoff gets its turn before any role exhausts the
            # bounded preflight. No broad crawl or enlarged video budget.
            while any(buckets.values()):
                for owner in owners:
                    if buckets[owner]:
                        visual_sources.append(buckets[owner].pop(0)[2])
            max_preflight = min(18, max(6, target * 3))
            visual_sources = visual_sources[:max_preflight]
            deadline = time.monotonic() + 360
            emit(run_id, "researcher", "visual_preflight", "按岗位资料核查动态素材；每页最多取3条核验视频，完整成片仍需后续逐镜头质检", detail={
                "source_ids": [s["id"] for s in visual_sources], "max_sources": max_preflight, "start_budget_seconds": 360})
            for source in visual_sources:
                check_cancel(run_id)
                ensure_memory_available()
                if time.monotonic() >= deadline:
                    source["visual_preflight"]["reason"] = "preflight_time_budget_exhausted"
                    continue
                try:
                    visuals = collect_visuals([source], FACTORY_DIR / run_id / "source-intake" / source["id"], limit=3)
                except (OSError, ValueError) as exc:
                    source["visual_preflight"].update(status="error", error_type=type(exc).__name__, reason=str(exc)[:300])
                    continue
                source["visual_ready"] = visuals.get("status") == "ready"
                source["visual_assets"] = [{k: a.get(k) for k in ("id", "sha256", "description", "topic_tags")}
                                            for a in visuals.get("assets", [])]
                source["visual_preflight"].update(status=visuals.get("status", "unknown"),
                    verified_assets=len(source["visual_assets"]), warnings=visuals.get("warnings", []))
            for source in sources:
                if source["visual_preflight"]["status"] == "not_checked":
                    source["visual_preflight"].setdefault("reason", "preflight_source_budget_exhausted")
            with SessionLocal() as db:
                db.get(FactoryRun, run_id).sources = sources
                db.commit()
            emit(run_id, "researcher", "visual_preflight", "已保存逐来源素材预检结果；未核查与素材不足分别记录", "completed", {
                "sources": [{"source_id": s["id"], "url": s["url"], **s["visual_preflight"]} for s in sources]})
            visual_ready = [s for s in sources if s.get("visual_ready")]
            # Several related official pages may contribute one/two originals
            # each. Their union is checked again against each candidate's refs.
            fingerprints = {a.get("sha256") for s in sources for a in s.get("visual_assets", []) if a.get("sha256")}
            if not visual_ready and len(fingerprints) < 3 and not is_paper_story({}, sources):
                raise ValueError("选题预检未找到足够动态素材，保留资料但不进入配音与渲染")
            usable = [s for s in sources if s.get("kind") != "reference_index"]
        else:
            usable = sources
        import re
        from services.factory_papers import is_paper_story
        topic_patterns = {
            "大模型": r"large language model|大语言模型|大模型|GPT[\s‐‑–-]*\d|Claude\s+(?!Code\b)(?:[A-Za-z]+\s+){0,2}\d|Gemini\s*(?:\d|Pro\b|Flash\b|Ultra\b)|Llama[\s-]*\d|DeepSeek|Qwen",
            "机器人": r"robot(?:ic|ics)?\b|humanoid|机器人|人形|机械臂|具身智能",
            "算力基础设施": r"data[ -]?cent(?:er|re)|infrastructure|GPU cluster|training cluster|TPU|Blackwell|Rubin|数据中心|算力基础设施|训练集群|推理集群",
            "端侧芯片": r"\bNPU\b|\bSoC\b|Snapdragon|Dimensity|端侧芯片|移动芯片|神经网络处理器|骁龙|天玑|Apple M\d|on-device chip|edge chip",
            "AI工具应用": r"AI tool|AI assistant|AI application|Claude Code|ChatGPT|text.to.speech|speech synthesis|AI工具|AI应用|语音合成|视频生成|工作流",
            "论文": r"paper|preprint|论文|预印本",
            "展会争议": r"\bCES\b|trade show|exhibition|lawsuit|controversy|export control|展会|峰会|争议|诉讼|出口管制",
        }
        topic_sources = {topic: [] for topic in topic_patterns}
        named_model = re.compile(r"GPT[\s‐‑–-]*\d[\w.-]*|Claude\s+(?!Code\b)(?:[A-Za-z]+\s+){0,2}\d[\w.-]*|Gemini\s*\d[\w.-]*|Llama[\s-]*\d|DeepSeek[\s-]*(?:V|R)\d|Qwen[\s-]*\d|Kimi\s*K\d|GLM[\s-]*\d", re.I)
        model_change = re.compile(r"introduc|launch|upgrad|releas|capabilit|benchmark|reasoning|open[ -](?:weight|source)|available|发布|推出|升级|能力|评测|推理|开源|开放|可用", re.I)
        named_chip = re.compile(r"Snapdragon\s+\d[\w -]*|Dimensity\s+\d\w*|Apple\s+M\d\w*|Arm\s+Ethos[- ]U\d+|骁龙\s*\d|天玑\s*\d|昇腾\s*\d|Ascend\s*\d", re.I)
        hardware_subject = re.compile(r"chip|processor|silicon|SoC|芯片|处理器|架构|制程", re.I)
        hardware_specs = re.compile(r"\d+(?:\.\d+)?\s*(?:TOPS|TFLOPS|nm|GHz|watts?|W\b)|architecture|transistor|memory bandwidth|制程|晶体管|功耗|能效|核数|内存带宽", re.I)
        for source in usable:
            if source.get("kind") == "reference_index":
                continue
            original = source.get("title", "") + "\n" + source.get("text", "")
            for topic, pattern in topic_patterns.items():
                match = re.search(pattern, original, re.I)
                if topic == "大模型":
                    # A brand mention/world model is not a new general model.
                    # Require a dated original and a named/versioned model with
                    # nearby release, capability or availability information.
                    match = named_model.search(original)
                    model_subject = named_model.search(source.get("title", "")) or re.search(r"large language model|大语言模型|大模型", source.get("title", ""), re.I)
                    if not source.get("published_at") or not match or not model_subject:
                        continue
                    evidence = original[max(0, match.start() - 100):match.end() + 240]
                    if not model_change.search(evidence):
                        continue
                elif topic == "端侧芯片":
                    # An app running on an NPU/Mac is still an app story. The
                    # title must establish a hardware subject and the original
                    # must identify a chip and hardware specification/performance.
                    match = named_chip.search(original)
                    title = source.get("title", "")
                    announced_chip = re.search(r"(?:introducing|announcing|发布|推出)\s+(?:" + named_chip.pattern + ")", title, re.I)
                    if not match or not named_chip.search(title) or not (hardware_subject.search(title) or announced_chip):
                        continue
                    evidence = original[max(0, match.start() - 100):match.end() + 300]
                    if not hardware_specs.search(evidence):
                        continue
                elif topic == "论文":
                    if not is_paper_story({}, [source]):
                        continue
                    evidence = source.get("url", "")
                elif match:
                    evidence = original[max(0, match.start() - 50):match.end() + 100]
                else:
                    continue
                topic_sources[topic].append({"source_id": source["id"], "evidence": evidence})

        def coverage_for(briefs):
            covered = {topic: [] for topic in topic_patterns}
            for index, brief in enumerate(briefs):
                refs = set(brief.get("source_ids", []))
                words = "\n".join(str(brief.get(key, "")) for key in ("title", "category", "angle", "facts"))
                for topic, pattern in topic_patterns.items():
                    cited = [row for row in topic_sources[topic] if row["source_id"] in refs]
                    if cited and (topic == "论文" or re.search(pattern, words, re.I)):
                        covered[topic].append({"candidate_index": index, "source_evidence": cited})
            return {"source_topics": topic_sources, "source_gaps": [key for key, rows in topic_sources.items() if not rows],
                    "candidate_topics": covered, "candidate_gaps": [key for key, rows in covered.items() if not rows],
                    "note": "标签仅表示本题与所引原文存在对应内容，不证明每日更新、独立实测或已有足量视频；单题可含多标签，不按模型自报标签补齐缺口。"}

        source_coverage = coverage_for([])
        emit(run_id, "researcher", "topic_coverage", "已检查七类题材的来源覆盖；缺口如实记录，大模型须主动检索", "completed", source_coverage)
        emit(run_id, "researcher", "researching", "正在依据资料筛选选题，区分新闻与常青教程")
        assignment = {"target": target + 2, "sources": usable,
            "editor_quotas": quotas,
            "editor_specialties": "小影：AI产品与实操；小影2：真实论文；小影3：科技热点、展会与公开争议。候选需满足每岗配额，不能全为论文。",
            "recent_titles": recent, "strategy": STRATEGY,
            "topic_coverage": source_coverage,
            "coverage_instruction": "每日必须主动检查通用大模型的新能力、推理、开源或成本进展，优先真实带日期的发布方原文。同时检查机器人、算力基础设施、端侧芯片、AI工具应用、论文、展会争议。单题可多标签；端侧应用不等于端侧芯片，不把机器人模型冒充通用大模型。不是每天强制凑齐七类：缺原文或对应视频就在coverage_gaps说明，并保留已核验候选，不编造参数或素材。",
            "source_id_contract": "source_ids和visual_source_ids都只能填写下面source_catalog的source_id（正文页面ID）；绝不能填asset_id（视频ID）。多页共同支持同一题时可联合引用，至少3条原片SHA去重的核验视频；辅助画面须写visual_support_reason说明具体关联及边界。不得拿无关视频补数。",
            "major_events": major_events,
            "major_event_instruction": "优先核对这些已采集的重大事件。每项必须在breaking_event_status列出source_id、已证实事实、未确认信息、是否推荐及缺画面原因。缺视频只影响制作就绪，不能把事件当作不存在；虚构反讽需另立创作任务，不冒充新闻实录。",
            "source_catalog": [{"source_id": s["id"], "title": s.get("title"), "kind": s.get("kind"),
                "editor_hint": s.get("visual_preflight", {}).get("editor_id"),
                "visual_status": s.get("visual_preflight", {}).get("status", "not_checked"),
                "visual_ready": bool(s.get("visual_ready")),
                "asset_ids_for_editing_only": [a["id"] for a in s.get("visual_assets", []) if a.get("id")]}
                for s in usable],
            "visual_constraint": "先写完整口播再匹配画面。论文选题必须有对应论文原文，制作会提取真实PDF页面及图表，不能用其他研究的背景演示代替。其他选题需有本题可核验画面线索。素材不足不凑数。"}
        for attempt in range(2):
            research = call_agent(run_id, "researcher", assignment, max_tokens=max(3500, (target + 2) * 550))
            raw_candidates = research.get("candidates") if isinstance(research, dict) else None
            candidates, diagnostics = _eligible_candidates(raw_candidates, usable, visual_style, with_diagnostics=True)
            counts = {owner: sum(candidate_editor(c, usable) == owner for c in candidates) for owner in (quotas or {})}
            enough = len(candidates) >= target and all(counts[owner] >= count for owner, count in (quotas or {}).items())
            candidate_coverage = coverage_for(candidates)
            emit(run_id, "researcher", "candidate_validation", f"第{attempt + 1}次候选校验：有效{len(candidates)}条，逐条拒绝原因已保存", "completed" if enough else "warning", {
                "attempt": attempt + 1, "raw_candidates": raw_candidates, "diagnostics": diagnostics,
                "accepted_candidates": candidates, "editor_counts": counts, "editor_quotas": quotas,
                "target": target, "source_catalog": assignment["source_catalog"], "topic_coverage": candidate_coverage,
                "model_reported_gaps": research.get("coverage_gaps") if isinstance(research, dict) else None,
                "breaking_event_status": research.get('breaking_event_status') if isinstance(research, dict) else None})
            if enough:
                break
            rejection_codes = {reason["code"] for row in diagnostics for reason in row["reasons"]}
            material_only = bool(rejection_codes) and rejection_codes <= {"visual_sources_unavailable", "insufficient_verified_visuals"}
            if attempt or material_only:
                ready = {key: min(counts[key], count) for key, count in (quotas or {}).items()}
                if not candidates or (quotas and not sum(ready.values())):
                    raise ValueError(f"有效选题{len(candidates)}个，各岗位候选{counts}未满足配额{quotas}，保留资料等待补齐，不凑数生产")
                # Keep the six-item target and original per-editor caps. Only
                # admitted candidates proceed; the final report stays partial.
                plan = {**plan, 'production_quotas': ready} if quotas else plan
                if quotas:
                    plan['material_shortfall'] = {key: quotas[key] - ready[key] for key in quotas}
                emit(run_id, 'director', 'material_shortfall', '已核验选题先制作，缺素材岗位继续保留缺口；不降低每日目标',
                     'warning', {'requested_target': target, 'ready_quotas': ready,
                                 'missing_quotas': plan.get('material_shortfall', {}), 'major_events': major_events})
                break
            emit(run_id, "researcher", "candidate_revision", "仅再修正一次候选格式、来源ID或岗位配额；不会重试下载素材", "warning")
            assignment = {**assignment, "previous_candidates": raw_candidates, "candidate_feedback": diagnostics,
                "format_feedback": "上次有效" + str(len(candidates)) + "条，各岗位候选" + str(counts) + "，需满足配额" + str(quotas) + "。按逐条拒因修正，source_ids填正文source_id而非视频asset_id。只用当前已核验且对应本题的来源；素材不足则明确交回，禁止编造或拿无关画面补数。"}
        plan = {**plan, "candidates": candidates, "topic_coverage": candidate_coverage}
        with SessionLocal() as db:
            db.get(FactoryRun, run_id).plan = plan
            db.commit()
        emit(run_id, "researcher", "researching", f"提交 {len(candidates)} 个有来源的候选选题", "completed")
    with SessionLocal() as db:
        if db.query(FactoryItem).filter_by(run_id=run_id).count():
            return
        from services.factory_team import production_quotas
        quotas = production_quotas(db.get(FactoryRun, run_id))
    target = min(target, sum(quotas.values()) if quotas else len(plan['candidates']))
    emit(run_id, "director", "planning", f"负责人正在选择 {target} 个选题并安排制作")
    decision = call_agent(run_id, "director", {"target": target, "candidates": plan["candidates"], "recent_titles": recent,
        "topic_coverage": plan.get("topic_coverage", {}),
        "editor_quotas": quotas, "quota_instruction": "选中条目必须满足各剪辑师配额，论文归小影2、科技热点展会归小影3、其余AI产品实操归小影。"})
    selected = decision.get("selected", [])
    indices = [entry.get("candidate_index") for entry in selected if isinstance(entry, dict)]
    if len(indices) != target or len(set(indices)) != target or any(type(i) is not int or i < 0 or i >= len(plan["candidates"]) for i in indices):
        raise ValueError("负责人未提交有效且互不重复的生产计划")
    plan = {**plan, "decision": decision}
    owners = validate_editor_plan([plan["candidates"][idx] for idx in indices], sources, quotas) if quotas else None
    with SessionLocal() as db:
        db.get(FactoryRun, run_id).plan = plan
        for position, idx in enumerate(indices):
            brief = dict(plan["candidates"][idx])
            if owners:
                brief["editor_id"] = owners[position]
            db.add(FactoryItem(run_id=run_id, position=position, brief=brief))
        db.flush()
        if db.get(FactoryRun, run_id).settings_snapshot.get("distribution_policy") == "single-platform-v1":
            from services.factory_distribution import assign_distribution, local_day
            run = db.get(FactoryRun, run_id)
            slot_ids = run.settings_snapshot.get("release_slot_ids", [])
            timezone_name = run.settings_snapshot.get("timezone", "Asia/Shanghai")
            dates = {}
            for item in db.query(FactoryItem).filter_by(run_id=run_id).all():
                slot = db.get(FactoryRelease, slot_ids[item.position]) if item.position < len(slot_ids) else None
                dates[item.id] = local_day((slot.nominal_at or slot.due_at) if slot else run.created_at, timezone_name)
            assign_distribution(db, run_id, slots=dates)
        db.commit()
    emit(run_id, "director", "planning", decision.get("reason", "生产计划已下达"), "completed")


def _ensure_visual_support(run_id, item_id):
    # Source reading precedes writing. Picture selection happens after the script.
    from services.factory_papers import is_paper_story, collect_paper
    with SessionLocal() as db:
        item, run = db.get(FactoryItem, item_id), db.get(FactoryRun, run_id)
        if run.settings_snapshot.get("visual_style") != "tech_montage":
            return
        facts = _sources_for(item.brief.get("source_ids", []), run.sources)
        brief = dict(item.brief)
        if not is_paper_story(brief, facts) or brief.get("paper_document"):
            return
    document = collect_paper(facts, FACTORY_DIR / run_id / "papers")
    _save_item(item_id, brief={**brief, "paper_document": document, "background_visuals": False,
                              "visual_source_ids": []},
               review={"approved": False, "reason": "按真实论文全文重写约150秒口播，之后再匹配原文图表与作者演示"})
    emit(run_id, "researcher", "paper_reading", f"已取得论文原文，共{len(document['pages'])}页；先写口播，再逐句匹配画面", "completed",
         {"item_id": item_id, "paper_url": document["url"], "sha256": document["sha256"]})


@item_usage("script")
def _write_and_review(run_id, item_id):
    with SessionLocal() as db:
        item, run = db.get(FactoryItem, item_id), db.get(FactoryRun, run_id)
        if item.content and item.review.get("approved") is True:
            return
        brief, prior, feedback = item.brief, item.content or {}, item.review or {}
        sources = _sources_for([*brief.get("source_ids", []), *brief.get("visual_source_ids", [])], run.sources)
        if brief.get("paper_document"):
            document = brief["paper_document"]
            narration_pages = _narration_pages(document)
            sources = [*sources, {"id": "paper-fulltext", "title": "官方技术报告原文按页提取" if document.get("kind") == "technical_report" else "论文原文按页提取",
                "url": document["url"], "kind": document.get("kind", "paper"), "pages": narration_pages,
                "page_selection_note": "本条只依据提供的原文页写口播，未提供页不得补造；完整PDF及所有页仍在缓存保留。",
                "text": "\n".join(f"第{p['page']}页：{p['text']}" for p in narration_pages)[:65000]}]
        max_revisions, revisions = run.settings_snapshot.get("max_revisions", 1), item.revisions
        engagement_required = run.settings_snapshot.get("engagement_required", False)
        director_hook_required = run.settings_snapshot.get("director_hook_required", False)
        owner = editor_id(item)
        target_duration = run.settings_snapshot.get("target_duration_seconds")
        calibrated_characters = brief.get("target_script_characters")
        recent_prompts = [i.content.get("follow_prompt") for i in db.query(FactoryItem).filter(
            FactoryItem.status == "completed").order_by(FactoryItem.created_at.desc()).limit(10) if i.content.get("follow_prompt")]
    from services.factory_platform_style import platform_style
    style = platform_style((brief.get("distribution") or {}).get("platform"))
    if style:
        brief = {**brief, "platform_style": style}
        _save_item(item_id, brief=brief)
    if owner == "producer3" and not brief.get("research_packet"):
        from services.factory_newsroom import validate_packet
        emit(run_id, "researcher3", "material_research", "小探3正在核对热点原文、事件日期与所需画面，交接给小影3")
        packet = call_agent(run_id, "researcher3", {"brief": brief, "sources": sources}, work_kind="initial")
        validate_packet(packet, sources)
        brief = {**brief, "research_packet": packet, "researcher_id": "researcher3"}
        _save_item(item_id, brief=brief)
        emit(run_id, "researcher3", "material_handoff", "已交付可追溯资料与画面需求；等待脚本定稿后逐句匹配实际素材", "completed", {"item_id": item_id, "packet": packet})
    if director_hook_required:
        from services.factory_editorial import validate_engagement_plan
        plan = brief.get("engagement_plan")
        if not plan:
            emit(run_id, "director", "hook_planning", "老周正在确定两版开场、正文兑现方式、首镜头证据与关注价值")
            plan = call_agent(run_id, "director", {"task_mode": "hook_plan", "brief": brief, "sources": sources, "strategy": STRATEGY}, work_kind="initial")
            validate_engagement_plan(plan, sources)
            brief = {**brief, "engagement_plan": plan}
            _save_item(item_id, brief=brief)
            emit(run_id, "director", "hook_plan", "已确定开场：" + plan["hooks"][plan["selected"]], "completed", {"item_id": item_id, "plan": plan})
        else:
            validate_engagement_plan(plan, sources)
    minimum, maximum = (round(target_duration * 3.3), round(target_duration * 5.7)) if target_duration else (80, 650)
    if type(calibrated_characters) is int and calibrated_characters > 0:
        # Character count is an estimate; the actual audio remains subject to
        # target +/- 15 seconds. Do not discard a usable draft over five letters.
        minimum, maximum = round(calibrated_characters * .90), round(calibrated_characters * 1.10)
    for attempt in range(revisions, max_revisions + 1):
        check_cancel(run_id)
        _save_item(item_id, status="writing", revisions=attempt, error=None)
        emit(run_id, "writer", "writing", f"{'修改' if prior else '撰写'}脚本：{brief['title']}")
        content = call_agent(run_id, "writer", {"brief": brief, "sources": sources, "previous_script": prior,
            "review_feedback": feedback, "strategy": STRATEGY, "recent_follow_prompts": recent_prompts,
            "target_duration_seconds": target_duration,
            "script_characters_range": [minimum, maximum],
            "length_instruction": "script必须是完整字符串，每个汉字、标点、英文字母均算一个字符；写足背景、具体例子、原理和局限，不能只给提纲。",
            "suggested_script_characters": calibrated_characters or (round(target_duration * 4.5) if target_duration else 220)},
            max_tokens=4200 if target_duration else 2400, work_kind="rework" if prior else "initial")
        try:
            if not isinstance(content.get("script"), str) or not minimum <= len(content["script"]) <= maximum:
                actual = len(content["script"]) if isinstance(content.get("script"), str) else 0
                raise ValueError(f"本次script实测{actual}字符，必须为{minimum}–{maximum}字符；按目标时长讲清背景、例子、原理与边界，不要仅返回提纲")
            if not isinstance(content.get("title"), str) or not content["title"].strip():
                raise ValueError("文案缺少标题")
            _sources_for(content.get("source_ids", []), sources)
            if engagement_required:
                validate_follow_prompt(content)
            if director_hook_required:
                from services.factory_editorial import validate_engagement_delivery
                validate_engagement_delivery(content, brief["engagement_plan"])
        except ValueError as exc:
            feedback = {"approved": False, "reason": str(exc)}
            prior = content
            _save_item(item_id, review=feedback)
            emit(run_id, "director", "script_revision", "退回小文修正交付格式：" + str(exc), "rejected",
                 {"item_id": item_id, "previous_delivery": content})
            if attempt < max_revisions:
                continue
            raise ReviewRejected(str(exc)) from None
        if style:
            content["platform_style"] = style
        content["sources"] = [{k: s.get(k) for k in ("id", "title", "url", "published_at", "fetched_at", "kind")} for s in sources]
        if brief.get("paper_document"):
            content["paper_document"] = brief["paper_document"]
            if brief["paper_document"].get("kind") == "technical_report":
                allowed = set(brief["paper_document"].get("verified_project_urls", []))
                content["document_evidence_sources"] = [{k: s.get(k) for k in ("id", "url", "text")}
                                                        for s in sources if s.get("url") in allowed]
        if brief.get("engagement_plan"):
            content["engagement_plan"] = brief["engagement_plan"]
        if brief.get("research_packet"):
            content["research_packet"] = brief["research_packet"]
        _save_item(item_id, content=content, status="reviewing")
        emit(run_id, "writer", "writing", f"脚本已提交：{content['title']}", "completed")
        emit(run_id, "reviewer", "reviewing", f"独立核对文案事实与出处：{content['title']}")
        review = call_agent(run_id, "reviewer", {"task_mode": "script_review", "script": content, "sources": sources}, work_kind="rework" if prior else "initial")
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


@item_usage("topic_replacement")
def _replace_rejected_topic(run_id, item_id):
    """The director may substitute one researched reserve topic, within the run budget."""
    with SessionLocal() as db:
        run, item = db.get(FactoryRun, run_id), db.get(FactoryItem, item_id)
        if item.brief.get("replacement_count", 0) >= 1:
            return False
        used = {i.brief.get("title") for i in db.query(FactoryItem).filter_by(run_id=run_id)}
        rejected = set(run.plan.get("rejected_titles", []))
        candidates = [c for c in run.plan.get("candidates", []) if c.get("title") not in used | rejected]
        if run.settings_snapshot.get("editor_quotas"):
            candidates = [c for c in candidates if candidate_editor(c, run.sources) == editor_id(item)]
        if not candidates:
            return False
        old_title, feedback = item.brief.get("title"), item.review
    emit(run_id, "director", "replacement", "当前选题返工后仍未通过审核，负责人正在评估备用选题")
    payload = {"task_mode": "replace_one_rejected_topic", "target": 1, "candidates": candidates,
        "recent_titles": list(used | rejected), "rejected_title": old_title, "review_feedback": feedback,
        "selection_contract": "只为当前这一条选择1个备用选题，selected恰好1项；无合适选题则返回空数组。不要重新安排全天计划。"}
    for attempt in range(2):
        decision = call_agent(run_id, "director", payload, work_kind="rework")
        selected = decision.get("selected")
        if selected == []:
            return False
        index = selected[0].get("candidate_index") if isinstance(selected, list) and len(selected) == 1 and isinstance(selected[0], dict) else None
        if type(index) is int and 0 <= index < len(candidates):
            break
        emit(run_id, "director", "replacement_format", "备用决策数量或索引无效，要求按单条任务重交" if not attempt else "备用决策仍无效，保留原选题与失败证据", "warning",
             {"item_id": item_id, "decision": decision})
        if attempt:
            raise ValueError("备用选题决策不符合单条选择约定，已重试一次")
        payload = {**payload, "format_feedback": "selected只允许0或1项；candidate_index必须是提供列表中从0开始的整数索引。", "previous_decision": decision}
    with SessionLocal() as db:
        run, item = db.get(FactoryRun, run_id), db.get(FactoryItem, item_id)
        run.plan = {**run.plan, "rejected_titles": [*run.plan.get("rejected_titles", []), old_title]}
        distribution = (item.brief or {}).get("distribution")
        item.brief = {**candidates[index], "replacement_count": 1, "editor_id": editor_id(item),
                      **({"distribution": distribution} if distribution else {})}
        item.content, item.review, item.revisions, item.status = {}, {}, 0, "planned"
        db.commit()
    emit(run_id, "director", "replacement", f"改用备用选题：{candidates[index]['title']}", "completed",
         {"rejected_title": old_title, "review": feedback, "decision": decision})
    return True


def _narration_pages(document):
    pages = document['pages']
    if len(pages) <= 12:
        return pages
    selected = list(pages[:10])
    for page in pages[10:]:
        if len(selected) >= 12:
            break
        if re.search(r'(?im)^\s*(?:\d+[.\s]+)?(?:limitations?|conclusions?|discussion|evaluation)\b', page['text']):
            selected.append(page)
    return selected


def _editing_assets(content, assets):
    """Bound long-PDF prompts around the pages cited by the approved script.

    Keep original text and page IDs, all explicit evidence pages and all demos.
    Adjacent pages provide enough continuous context for eight narrative scenes.
    """
    pages = {a['paper_page']: a for a in assets if a.get('kind') == 'paper_page'}
    if len(pages) <= 12:
        return assets
    selected = {1} & pages.keys()
    selected.update(h['page'] for h in content.get('paper_highlights', [])
                    if isinstance(h, dict) and h.get('page') in pages)
    for scene in content.get('scenes', []):
        for key in ('paper_page', 'paper_evidence_page'):
            if scene.get(key) in pages:
                selected.add(scene[key])
        for asset_id in scene.get('asset_ids', []):
            match = re.fullmatch(r'paper-(\d+)', asset_id)
            if match and int(match[1]) in pages:
                selected.add(int(match[1]))
    selected.update(int(n) for n in re.findall(r'第\s*(\d+)\s*页', content.get('script', '')) if int(n) in pages)
    for number in sorted(selected):
        for adjacent in (number - 1, number + 1):
            if len(selected) >= 10:
                break
            if adjacent in pages:
                selected.add(adjacent)
    for number in sorted(pages):
        if len(selected) >= 10:
            break
        selected.add(number)
    return [a for a in assets if a.get('kind') != 'paper_page' or a['paper_page'] in selected]


def _plan_edit(run_id, content, assets, visual_style, owner="producer"):
    editor_content = {k: v for k, v in content.items() if k != 'production_status'}
    selected_assets = _editing_assets(content, assets)
    available = [{k: a.get(k) for k in ("id", "kind", "description", "source_title", "topic_tags", "clips", "paper_page", "document_kind", "reference_source_url", "source_chain")} for a in selected_assets]
    feedback = {}
    prior_edit = bool(content.get("editorial_review"))
    for attempt in range(2):
        check_cancel(run_id)
        arrangement = call_agent(run_id, owner, {"content": editor_content, "visual_style": visual_style,
            "available_assets": available, "editorial_feedback": feedback}, max_tokens=6500,
            work_kind="rework" if feedback or prior_edit else "initial")
        scenes = arrangement.get("scenes", [])
        try:
            if isinstance(scenes, list):
                scenes = [{**s, "body": "" if s.get("body") is None else s.get("body", "")} if isinstance(s, dict) else s for s in scenes]
            if not isinstance(scenes, list) or not 2 <= len(scenes) <= 18 or not all(isinstance(s, dict) and s.get("heading") and isinstance(s.get("body", ""), str) for s in scenes):
                count = len(scenes) if isinstance(scenes, list) else None
                emit(run_id, owner, "edit_format_invalid", "分镜输出格式不合格，保留原始输出供定位", "warning",
                     {"scene_count": count, "arrangement": arrangement})
                raise ValueError(f"分镜须为顶层scenes数组、2–18个叙事段（不是逐镜头列表）；当前段数{count}。每段heading非空、body为字符串（可为空）。请按8–14段交付。")
            if assets:
                validate_editorial_scenes(content["script"], scenes, assets)
                if content.get("paper_document"):
                    from services.factory_papers import validate_paper_scenes, highlight_rectangles
                    validate_paper_scenes(scenes, assets, content.get("document_evidence_sources"))
                    by_id = {a["id"]: a for a in assets}
                    for scene in scenes:
                        asset = by_id[scene["asset_ids"][0]]
                        if asset.get("kind") == "paper_page":
                            for highlight in scene["paper_highlights"]:
                                highlight_rectangles(asset, highlight["text"], scene.get("paper_focus", [0, 0, 1, 1]))
                review = call_agent(run_id, "reviewer", {"task_mode": "edit_review",
                    "script": editor_content, "arrangement": arrangement, "available_assets": available,
                    "review_scope": "仅核对已提供素材描述与口播，未直接观看视频，不得声称成片视觉已验收"},
                    work_kind="rework" if feedback or prior_edit else "initial")
                if review.get("approved") is not True or type(review.get("score")) not in (int, float) or review["score"] < 80:
                    emit(run_id, "reviewer", "edit_review_rejected", "保留分镜与具体审核意见供修正", "rejected",
                         {"review": review, "arrangement": arrangement})
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


@item_usage("editing")
def _render(run_id, item_id):
    with SessionLocal() as db:
        item = db.get(FactoryItem, item_id)
        owner = editor_id(item)
        content, media = item.content, item.media or {}
        content = {**content, "script_review": dict(item.review or {})}
        brief = item.brief
        run_sources = db.get(FactoryRun, run_id).sources
        visual_style = db.get(FactoryRun, run_id).settings_snapshot.get("visual_style", "cards")
        target_duration = db.get(FactoryRun, run_id).settings_snapshot.get("target_duration_seconds")
        if media.get("video_path") and Path(media["video_path"]).is_file():
            if owner == "producer2" and media.get("voice_profile") != "calm_male":
                raise ValueError("这份未发布论文仍是旧声线，需要按沉稳男声重制后交付")
            from services.factory_montage import validate_publication_media
            from services.factory_papers import require_paper_publication
            validate_publication_media(media)
            require_paper_publication(content, brief, media)
            return
    check_cancel(run_id)
    ensure_memory_available()
    _save_item(item_id, status="rendering", error=None)
    directory = FACTORY_DIR / run_id / item_id
    if owner == "producer2":
        # A new voice gets a separate delivery directory. Keep the previous
        # unsubmitted candidate and its packages available for audit.
        directory = directory / "voice-calm-male"
        content = {**content, "voice_profile": "calm_male", "editor_id": owner}
    assets = []
    if visual_style == "tech_montage" and brief.get("paper_document"):
        from services.factory_papers import paper_assets
        from services.factory_visuals import collect_visuals
        document = brief["paper_document"]
        content = {**content, "paper_document": document, "background_visuals": False}
        page_candidates = [{'kind': 'paper_page', 'paper_page': p['page']} for p in document['pages']]
        selected_pages = {p['paper_page'] for p in _editing_assets(content, page_candidates)}
        render_document = {**document, 'pages': [p for p in document['pages'] if p['page'] in selected_pages]}
        assets = paper_assets(render_document, directory / "paper-pages")
        direct_sources = [s for s in run_sources if s["url"].rstrip("/") in document["verified_project_urls"]]
        if brief.get("verified_visual_manifest"):
            from services.factory_verified_materials import load_verified_assets
            assets += load_verified_assets(brief["verified_visual_manifest"], direct_sources,
                                           minimum_assets=1 if document.get("kind") == "technical_report" else 3)
        elif direct_sources:
            visuals = collect_visuals(direct_sources, directory / "visual-assets")
            assets += [a for a in visuals.get("assets", []) if a.get("source_url", "").rstrip("/") in document["verified_project_urls"]]
        emit(run_id, owner, "paper_picture_matching", "口播已定稿，匹配真实论文页、图表及同论文作者演示", "completed")
    elif visual_style == "tech_montage":
        from services.factory_papers import is_paper_story
        if is_paper_story(brief, content.get("sources", [])):
            raise ValueError("论文讲解尚未取得真实原文，请先读取论文并重写口播，不能用背景演示填充")
        researcher = "researcher3" if owner == "producer3" else "researcher"
        emit(run_id, researcher, "visual_research", "正在获取与本条来源对应的真实演示视频")
        from services.factory_visuals import collect_visuals
        visual_sources = list(content.get("sources", []))
        if brief.get("visual_source_ids"):
            visual_sources.extend(_sources_for(brief["visual_source_ids"], run_sources))
            content = {**content, "background_visuals": True, "visual_support_reason": brief.get("visual_support_reason")}
        if brief.get("verified_visual_manifest"):
            from services.factory_verified_materials import load_verified_assets
            visuals = {"status": "ready", "assets": load_verified_assets(brief["verified_visual_manifest"], visual_sources),
                       "manifest_path": brief["verified_visual_manifest"], "warnings": []}
        else:
            visuals = collect_visuals(visual_sources, directory / "visual-assets")
        assets = visuals.get("assets", [])
        if visuals.get("status") != "ready" or len(assets) < 3:
            raise ValueError("匹配选题的动态演示素材不足3条，已暂停该条制作；请更换选题或补充素材")
        emit(run_id, researcher, "visual_research", f"已获取 {len(assets)} 条有出处的动态演示素材，交给{AGENT_MAP[owner]['name']}逐句匹配", "completed",
             {"item_id": item_id, "manifest_path": str(visuals.get("manifest_path", "")), "warnings": visuals.get("warnings", [])})
    elif visual_style != "cards":
        raise ValueError("不支持的画面风格")
    if assets:
        from services.factory_montage import validate_assets, require_footage_budget
        assets = validate_assets(assets)
        if target_duration:
            require_footage_budget(assets, target_duration)
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
        emit(run_id, owner, "voice_synthesis", "口播与分镜已审核，正在合成配音并对齐字幕：" + content["title"],
             detail={"item_id": item_id})
        voice = synthesize_factory_audio(item_id, content, directory)
        if owner == "producer2" and voice.get("voice_profile") != "calm_male":
            raise ValueError("论文配音未使用要求的沉稳男声，停止交付")
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
        emit(run_id, owner, "rendering", f"配音已完成（{voice['duration']:.1f}秒），正在按口播裁剪画面并检查重复：" + content["title"],
             detail={"item_id": item_id, "tts_engine": voice["tts_engine"]})
        media = render_tech_montage(item_id, content, voice["audio_path"], voice["subtitle_path"], assets, directory)
        media.update(tts_engine=voice["tts_engine"], speech_timings_path=voice["speech_timings_path"], style="tech_montage")
        media.update({key: voice.get(key) for key in ("voice_profile", "voice_name", "voice_label", "voice_rate", "voice_pitch")})
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


@item_usage("platform_adaptation")
def _package(run_id, item_id):
    from services.factory_filenames import safe_filename
    from services.factory_platform_style import platform_style
    from services.factory_cover import COVER_DESIGN_VERSION, FRAME_TITLE_VERSION
    from services.factory_handoff import HANDOFF_VERSION, build_handoff, handoff_files
    with SessionLocal() as db:
        item, run = db.get(FactoryItem, item_id), db.get(FactoryRun, run_id)
        content, media, existing = item.content, item.media, item.packages or []
        video_filename = safe_filename(content["title"], ".mp4")
        platforms = run.settings_snapshot["platforms"]
        route = item.brief.get("distribution") or {}
        if route.get("day"):
            signature = "".join(c.casefold() for c in content.get("script", "") if c.isalnum())
            for other in db.query(FactoryItem).filter(FactoryItem.id != item.id, FactoryItem.status != "deferred"):
                if (signature and (other.brief.get("distribution") or {}).get("day") == route["day"]
                        and signature == "".join(c.casefold() for c in other.content.get("script", "") if c.isalnum())):
                    raise ValueError("同日作品出现相同口播，需重写独立内容，不能换标题或跨平台重复交付")
        if run.settings_snapshot.get("distribution_policy") == "single-platform-v1" or route:
            if route.get("platform") not in platforms:
                raise ValueError("尚未确定唯一分发平台，保留成片等待排期")
            platforms = [route["platform"]]
            existing = [p for p in existing if p["platform"] in platforms]
        reuse_variants = len(existing) == len(platforms) and all(Path(p["path"]).is_file() for p in existing)
        if reuse_variants and all((p.get("cover_design") or {}).get("version") == COVER_DESIGN_VERSION
                                  and (platform_style(p["platform"]).get("opening_mode") != "first_frame_title"
                                       or ((p.get("cover_design") or {}).get("frame_title_version") == FRAME_TITLE_VERSION
                                           and (p.get("cover_design") or {}).get("text_rendered") == content["title"]
                                           and (p.get("delivery_quality") or {}).get("opening_mode") == "first_frame_title"))
                                  and p.get("source_media_fingerprint") == media.get("fingerprint")
                                  and p.get("delivery_layout") == "platform-zip-v1"
                                  and p.get("handoff_version") == HANDOFF_VERSION
                                  and p.get("cover_path") and Path(p["cover_path"]).is_file()
                                  and p.get("video_path") and Path(p["video_path"]).is_file()
                                  and p.get("video_filename") == video_filename for p in existing):
            _save_item(item_id, status="completed", error=None)
            return
        publications = db.query(FactoryPublication).filter_by(item_id=item_id, action="publish").all()
        warnings = {}
        for job in publications:
            if job.status == "submitted":
                warnings[job.platform] = "这条作品已有平台接收记录，请在官方作品管理核对，不要重复上传。"
            elif job.status in {"queued", "running"} or (job.result or {}).get("submission_uncertain"):
                warnings[job.platform] = "这条作品已有提交尝试但结果未确认；请本人先在官方作品管理确认是否已发布或审核中，再决定是否上传，不能重复提交。"
    check_cancel(run_id)
    emit(run_id, "publisher", "adapting", f"准备完整人工发布包：标题、正文、话题、AI说明、钩子、置顶评论及回复模板：{content['title']}")
    if reuse_variants:
        # Upgrade finishing from the approved montage without another model
        # call. Preserve its old archive and publication evidence.
        variants = [dict(p) for p in existing]
    else:
        publication_content = {k: content[k] for k in ("title", "script", "hook", "follow_prompt", "follow_value", "sources", "engagement_plan", "platform_style") if k in content}
        adapted = call_agent(run_id, "publisher", {"content": publication_content, "platforms": platforms,
            "platform_styles": {p: platform_style(p) for p in platforms},
            "delivery_mode": "manual", "instruction": "只能重组本条已审核口播的事实；完整交付manual_handoff字段。不要增加未实测结论，不假冒观众留言。"}, max_tokens=3000,
                             work_kind="rework" if existing else "initial")
        variants = adapted.get("platforms", [])
    if not isinstance(variants, list) or len(variants) != len(platforms) or {p.get("platform") for p in variants} != set(platforms):
        raise ValueError("运营员未交付全部平台版本")
    if run.settings_snapshot.get("engagement_required"):
        emit(run_id, "publisher", "engagement_handoff", "小麦已依据含关注提醒的完整口播生成平台文案",
             "completed", validate_follow_prompt(content))
    packages = []
    directory = FACTORY_DIR / run_id / item_id
    directory.mkdir(parents=True, exist_ok=True)
    previous = {p["platform"]: p for p in existing}
    for variant in variants:
        platform = variant["platform"]
        if not isinstance(variant.get("title"), str) or not variant["title"].strip():
            raise ValueError("平台版本缺少标题")
        handoff = build_handoff(content, variant, route, warnings.get(platform))
        style = platform_style(platform)
        frame_title = style.get("opening_mode") == "first_frame_title"
        design_suffix = f"design{COVER_DESIGN_VERSION}" + (f"-firstframe{FRAME_TITLE_VERSION}" if frame_title else "")
        cover_path = directory / f"{platform}-cover-v{HANDOFF_VERSION}-{design_suffix}.png"
        if frame_title:
            from services.factory_cover import render_frame_cover
            handoff["cover_text"] = content["title"]
            cover_design = render_frame_cover(media["video_path"], content["title"], cover_path, platform)
        else:
            from services.factory_cover import render_cover
            cover_design = render_cover({**content, "cover_text": handoff["cover_text"], "platform_style": style}, cover_path)
        from services.factory_delivery_video import render_delivery_video
        delivery_video = directory / f"{platform}-complete-v{HANDOFF_VERSION}-{design_suffix}.mp4"
        finish_options = {"opening_mode": "first_frame_title", "title_overlay": cover_design["overlay_path"]} if frame_title else {}
        delivery_quality = render_delivery_video(media["video_path"], cover_path, delivery_video, **finish_options)
        manifest = {**{k: v for k, v in variant.items() if k not in {"path", "download_url", "copy_path", "copy_url", "publication_job_id", "cover_path", "video_path"}}, "status": "package_ready", "published": False,
                    "handoff_version": HANDOFF_VERSION, "manual_handoff": handoff, "video_filename": video_filename, "delivery_layout": "platform-zip-v1",
                    "delivery_quality": delivery_quality, "platform_style": style,
                    "cover_design": {k: v for k, v in cover_design.items() if k not in {"design_path", "overlay_path"}},
                    "source_item_id": item_id, "sources": content.get("sources", []),
                    "visual_sources": media.get("visual_sources", []),
                    "note": "本包仅代表素材交付，不代表平台发布状态。请在官方客户端人工核对、上传并发布到分配的平台。"}
        # Versioned output keeps previously delivered archives intact on upgrade.
        path = directory / f"{platform}-manual-v{HANDOFF_VERSION}-{design_suffix}.zip"
        temp = path.with_suffix(".zip.tmp")
        texts = handoff_files(handoff)
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.write(cover_path, "cover.png")
            for key, name in (("video_path", video_filename), ("audio_path", "audio.mp3"), ("subtitle_path", "subtitles.ass")):
                archive.write(delivery_video if key == "video_path" else media[key], name)
            archive.writestr("metadata.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr("发布标题.txt", handoff["title"] + "\n")
            archive.writestr("正文描述.txt", handoff["description"] + "\n")
            archive.writestr("发布平台.txt", (style.get("name") or platform) + "\n本包只上传到这个平台。\n")
            archive.writestr("script.txt", content["script"])
            archive.writestr("发布说明.txt", manifest["note"] + "\n\n" + variant.get("caption", ""))
            for name, text in texts.items():
                archive.writestr(name, text)
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
        copy_path = directory / f"{platform}-发布文案.txt"
        copy_path.write_text(texts["发布文案.txt"], encoding="utf-8")
        packages.append({"platform": platform, "title": variant["title"], "caption": variant.get("caption", ""),
                         "tags": variant.get("tags", []), "published": False,
                         "status": previous.get(platform, {}).get("status", "package_ready"), "path": str(path),
                         "handoff_version": HANDOFF_VERSION, "manual_handoff": handoff, "video_filename": video_filename, "delivery_layout": "platform-zip-v1",
                         "copy_path": str(copy_path), "cover_path": str(cover_path), "cover_design": cover_design,
                         "video_path": str(delivery_video), "delivery_quality": delivery_quality, "platform_style": style,
                         "source_media_fingerprint": media.get("fingerprint")})
    _save_item(item_id, packages=packages, status="completed", error=None)
    emit(run_id, "publisher", "adapting", f"已交付 {len(packages)} 份完整人工发布包，等待用户在官方客户端发布", "completed")


@batch_usage("reporting")
def _finish_report(run_id):
    with SessionLocal() as db:
        run = db.get(FactoryRun, run_id)
        items = db.query(FactoryItem).filter(FactoryItem.run_id == run_id, FactoryItem.status != "deferred").all()
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
    except (RunCancelled, MemoryPressure, ModelServiceUnavailable):
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
                        if item.status != "deferred" and (not selected or item.id in selected)]
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
                    db.get(FactoryRun, run_id).completed_count = db.query(FactoryItem).filter_by(run_id=run_id, status="completed").count()
                    db.commit()
            except (RunCancelled, MemoryPressure, ModelServiceUnavailable):
                raise
            except Exception as exc:
                _save_item(item_id, status="failed", error=str(exc)[:1000])
                emit(run_id, "director", "item_failed", f"单条生产未完成：{str(exc)[:300]}；继续其他选题", "failed")
        _finish_report(run_id)
        return {"status": "finished", "run_id": run_id}
    except ModelServiceUnavailable as exc:
        with SessionLocal() as db:
            run = db.get(FactoryRun, run_id)
            run.completed_count = db.query(FactoryItem).filter_by(run_id=run_id, status="completed").count()
            run.status = "partial" if run.completed_count else "failed"
            run.error, run.active_slot, run.finished_at = str(exc), None, datetime.utcnow()
            db.commit()
        emit(run_id, "director", "model_service_blocked", str(exc), "blocked")
        return {"status": "model_service_blocked", "run_id": run_id}
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
