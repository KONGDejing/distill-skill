"""Owner-authorized two-item Kuaishou selection, disjoint from Douyin."""
from sqlalchemy import update
from models.factory import FactoryItem, FactoryPublication, FactoryRun

POLICY = 'kuaishou-two-exclusive-v1'
DAILY_LIMIT = 2
PUBLISH_TIMES = ('19:00', '21:00')


def authorized(run):
    return (run.settings_snapshot or {}).get('kuaishou_auto_policy') == POLICY


def content_score(item):
    """An editorial preference for visible demonstrations, not an algorithm claim."""
    brief, media = item.brief or {}, item.media or {}
    track = brief.get('editor_track')
    fit = {'technology_scenes': 2, 'people_news': 1, 'paper_explainer': 0}.get(track, 0)
    return (item.status == 'completed' and (item.review or {}).get('approved') is True,
            fit, (media.get('quality') or {}).get('dynamic_coverage', 0),
            (item.review or {}).get('score', 0), -item.position)


def assign_kuaishou(db, run, day):
    from services.factory_distribution import reserved_item_ids, reserves_quota
    db.execute(update(FactoryRun).where(FactoryRun.id == run.id).values(heartbeat_at=FactoryRun.heartbeat_at))
    if not authorized(run):
        return {}
    items = db.query(FactoryItem).filter_by(run_id=run.id).all()
    group = [item for item in items if (item.brief or {}).get('distribution', {}).get('day') == day
             and item.status != 'deferred']
    reserved = reserved_item_ids(db, 'kuaishou', day, include_prepares=True,
                                 timezone_name=run.settings_snapshot.get('timezone', 'Asia/Shanghai'))
    other_picks = {item.id for item in db.query(FactoryItem).filter(FactoryItem.run_id != run.id)
                   if (item.brief or {}).get('distribution', {}).get('day') == day
                   and (item.brief or {}).get('distribution', {}).get('kuaishou_selected') is True}
    candidates = []
    for item in group:
        route = item.brief['distribution']
        opposite = db.query(FactoryPublication).filter_by(item_id=item.id, platform='douyin').all()
        if not route.get('douyin_selected') and not any(reserves_quota(job, True) for job in opposite):
            candidates.append(item)
    chosen = {item.id for item in candidates if item.id in reserved}
    if (reserved & {item.id for item in group}) - chosen:
        raise ValueError('快手已有任务与抖音预选冲突，保留历史记录等待核对')
    occupied = reserved | other_picks
    for item in sorted(candidates, key=content_score, reverse=True):
        if len(occupied | chosen) >= DAILY_LIMIT:
            break
        chosen.add(item.id)
    output = {}
    for item in group:
        route = dict(item.brief['distribution'])
        selected = item.id in chosen
        route.update(kuaishou_auto_policy=POLICY, kuaishou_selected=selected)
        if not route.get('douyin_selected'):
            route['platform'] = 'kuaishou' if selected else None
            route['reason'] = ('快手每日2条，从未上抖音的作品中优先选已验收、动态画面和新闻叙事适合的内容；不是对平台推荐算法的判断。'
                               if selected else '未入选自动发布；完整成片和各平台文案继续保存供本人选择。')
        item.brief = {**item.brief, 'distribution': route}
        output[item.id] = route
    db.flush()
    return output
