"""Durable item ownership for the three video editors."""
from collections import Counter

from agents.registry import AGENT_MAP, EDITOR_IDS, DAILY_EDITOR_QUOTAS, DAILY_TARGET
from models.factory import FactoryEvent, FactoryItem, FactoryRun


def editor_id(item):
    value = (item.brief or {}).get("editor_id")
    # Historical work belongs to its actual original editor.
    return value if value in EDITOR_IDS else "producer"


def editor_quotas(target_count=DAILY_TARGET):
    """Balanced quotas for a new 1–6-item run; a full day is exactly 2 each."""
    if type(target_count) is not int or not 1 <= target_count <= DAILY_TARGET:
        raise ValueError("每轮产量应为1–6条")
    return {owner: target_count // len(EDITOR_IDS) + int(index < target_count % len(EDITOR_IDS))
            for index, owner in enumerate(EDITOR_IDS)}


def _validated_quotas(quotas):
    if (not isinstance(quotas, dict) or set(quotas) != set(EDITOR_IDS)
            or any(type(quotas[owner]) is not int or not 0 <= quotas[owner] <= DAILY_EDITOR_QUOTAS[owner]
                   for owner in EDITOR_IDS)
            or not 1 <= sum(quotas.values()) <= DAILY_TARGET):
        raise ValueError("剪辑师配额必须覆盖三位剪辑师，每人0–2条、合计1–6条")
    return dict(quotas)


def candidate_editor(brief, sources):
    """Classify by cited evidence and topic; a model-provided owner is not proof."""
    from services.factory_papers import is_paper_story
    from services.factory_newsroom import is_newsroom_story
    cited = [source for source in sources if source.get("id") in brief.get("source_ids", [])]
    if is_paper_story(brief, cited):
        return "producer2"
    return "producer3" if is_newsroom_story(brief) else "producer"


def _require_counts(counts, quotas):
    differences = [f"{AGENT_MAP[owner]['name']}需要{quotas[owner]}条、实际{counts[owner]}条"
                   for owner in EDITOR_IDS if counts[owner] != quotas[owner]]
    if differences:
        raise ValueError("剪辑配额不匹配：" + "；".join(differences) + "。请补充对应选题，不挪用其他岗位名额。")


def validate_editor_plan(briefs, sources, quotas):
    """Return editor IDs in input order, without mutations or model calls.

    A new run's selected briefs must fill every quota exactly. Call before
    creating items, then write each returned ID into the corresponding brief.
    """
    quotas = _validated_quotas(quotas)
    if not isinstance(briefs, list) or not all(isinstance(brief, dict) for brief in briefs):
        raise ValueError("剪辑选题计划必须为对象数组")
    owners = [candidate_editor(brief, sources) for brief in briefs]
    for brief, owner in zip(briefs, owners):
        if brief.get("editor_id") and brief["editor_id"] != owner:
            raise ValueError("选题负责人不符合岗位：论文归小影2，热点展会归小影3，其余科技内容归小影")
    _require_counts(Counter(owners), quotas)
    return owners


def assign_editors(db, run_id):
    from services.factory_papers import is_paper_story
    from services.factory_newsroom import is_newsroom_story
    run = db.get(FactoryRun, run_id)
    items = db.query(FactoryItem).filter(FactoryItem.run_id == run_id,
        FactoryItem.status != "deferred").order_by(FactoryItem.position).all()
    quotas = (run.settings_snapshot or {}).get("editor_quotas") if run else None
    if quotas is not None:
        quotas = _validated_quotas(quotas)
        counts, pending = Counter({key: 0 for key in EDITOR_IDS}), []
        for item in items:
            brief = item.brief or {}
            started = item.status != "planned" or bool(item.content or item.media)
            owner = editor_id(item) if brief.get("editor_id") in EDITOR_IDS or started else candidate_editor(brief, run.sources or [])
            if not started and brief.get("editor_id") and owner != candidate_editor(brief, run.sources or []):
                raise ValueError("未开工选题的负责人不符合论文或热点岗位")
            counts[owner] += 1
            if brief.get("editor_id") != owner:
                pending.append((item, owner))
        # Validate before any writes: an imbalanced plan must not spend calls
        # or move already-started work to another employee to fill a quota.
        _require_counts(counts, quotas)
        assigned = []
        for item, owner in pending:
            item.brief = {**(item.brief or {}), "editor_id": owner}
            assigned.append({"item_id": item.id, "position": item.position, "editor_id": owner})
        if assigned:
            db.add(FactoryEvent(run_id=run_id, agent_id="director", stage="editor_assignment", status="completed",
                message="按剪辑师配额安排：" + "、".join(f"{AGENT_MAP[key]['name']}{counts[key]}条" for key in EDITOR_IDS),
                detail={"assignments": assigned, "counts": dict(counts), "editor_quotas": quotas}))
        db.commit()
        return dict(counts)
    # Legacy snapshots preserve the original allocation and actual ownership.
    counts = Counter({key: 0 for key in EDITOR_IDS})
    for item in items:
        if item.brief.get("editor_id") in EDITOR_IDS or item.status != "planned":
            owner = editor_id(item)
            counts[owner] += 1
            if item.brief.get("editor_id") != owner:
                item.brief = {**item.brief, "editor_id": owner}
    assigned = []
    for item in items:
        if item.brief.get("editor_id") in EDITOR_IDS:
            continue
        sources = [s for s in (run.sources or []) if s.get("id") in item.brief.get("source_ids", [])]
        owner = ("producer2" if is_paper_story(item.brief, sources) else "producer3" if is_newsroom_story(item.brief)
                 else min(("producer", "producer3"), key=lambda key: counts[key]))
        item.brief = {**item.brief, "editor_id": owner}
        counts[owner] += 1
        assigned.append({"item_id": item.id, "position": item.position, "editor_id": owner})
    if assigned:
        db.add(FactoryEvent(run_id=run_id, agent_id="director", stage="editor_assignment", status="completed",
            message="老周已分配剪辑任务：" + "、".join(f"{AGENT_MAP[key]['name']}{counts[key]}条" for key in EDITOR_IDS)
                    + "；已开始作品保留原负责人，重配音与渲染遵守内存保护",
            detail={"assignments": assigned, "counts": dict(counts)}))
    db.commit()
    return dict(counts)
