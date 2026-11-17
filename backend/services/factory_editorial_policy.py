"""Owner-approved editor mandates and first-party watch sources; no model calls."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re

POLICY = 'douyin-four-random-v1'
DAILY_EDITORS = ('producer', 'producer3')
WEEKLY_PAPER_DAYS = (2, 5)  # Wednesday and Saturday in Asia/Shanghai
WEEKLY_PAPER_TARGET = 2
EDITOR_IDS = ('producer', 'producer2', 'producer3')
MANUAL_PLATFORMS = ('xiaohongshu', 'kuaishou')


@lru_cache(maxsize=1)
def _policy():
    return json.loads((Path(__file__).resolve().parents[1] / 'data/factory_editorial_policy.json').read_text())


def editorial_policy():
    return deepcopy(_policy())


def editor_profile(owner):
    names = {'producer': '小影', 'producer2': '小影2', 'producer3': '小影3'}
    return deepcopy(next(row for row in _policy()['tracks'] if row['editor'] == names[owner]))


def people_context():
    data = _policy()
    return {'overseas_priority': True, 'overseas_count': 40, 'domestic_count': 10,
            'people': [{k: p[k] for k in ('name', 'background', 'watch_topics', 'source_url')} for p in data['people']],
            'overseas_companies': data['overseas_core_companies'] + data['overseas_supplementary_companies'],
            'domestic_company_allowlist': data['domestic_company_allowlist'],
            'scope': '国内公司/人物新闻只限指定七家；名单是检索线索，不是今日事件或现任职务的证据。'}


def matching_people(source):
    text = source.get('title', '') + '\n' + source.get('text', source.get('excerpt', ''))
    found = []
    for person in _policy()['people']:
        aliases = [person['name'], person.get('chinese_name', '')]
        for alias in aliases:
            if not alias:
                continue
            pattern = (r'(?<![A-Za-z])' + re.escape(alias) + r'(?![A-Za-z])') if alias.isascii() else re.escape(alias)
            if re.search(pattern, text, re.I):
                found.append(person['name']); break
    return found


def validate_track_assets(owner, assets):
    if owner != 'producer2' and assets and all(a.get('kind') == 'paper_page' for a in assets):
        raise ValueError('小影与小影3必须交付动态科技场景或新闻故事，不能把整片降级为论文/公告PPT；请补充对应素材')


def validate_track_picture_coverage(owner, quality):
    if owner != 'producer2' and quality.get('dynamic_coverage', 0) < .75:
        raise ValueError('小影/小影3的主体必须是动态科技场景或新闻画面，原文证据不能取代整片；动态覆盖不足75%')
