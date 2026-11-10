"""Durable item ownership for the three video editors."""
from collections import Counter

from agents.registry import AGENT_MAP, EDITOR_IDS
from models.factory import FactoryEvent, FactoryItem, FactoryRun


def editor_id(item):
    value = (item.brief or {}).get("editor_id")
    # Historical work belongs to its actual original editor.
    return value if value in EDITOR_IDS else "producer"


def assign_editors(db, run_id):
    from services.factory_papers import is_paper_story
    run = db.get(FactoryRun, run_id)
    items = db.query(FactoryItem).filter_by(run_id=run_id).order_by(FactoryItem.position).all()
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
        owner = "producer2" if is_paper_story(item.brief, sources) else min(("producer", "producer3"), key=lambda key: counts[key])
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
