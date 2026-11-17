from copy import deepcopy

import pytest

from services.factory_cut_allocator import allocate_cut_plan
from services.factory_montage import validate_shot_plan


def fixture():
    assets = [{'id': key, 'kind': 'official_video', 'sha256': key,
               'clips': [{'start': 0, 'end': 3}, {'start': 4, 'end': 7}],
               'display_label': '官方演示', 'source_url': f'https://example.org/{key}'}
              for key in ['one', 'two', 'three']]
    lines = ['先看第一段真实动作。', '接着看第二段真实动作。', '最后看第三段真实动作。']
    scenes = [{'heading': '真实动作', 'body': '', 'narration_exact': line,
               'asset_ids': [asset['id']], 'match_reason': '对应已取得的真实动作'}
              for line, asset in zip(lines, assets)]
    cues = [{'text': text, 'start': i * 5, 'end': (i + 1) * 5} for i, text in enumerate(lines)]
    return {'script': ''.join(lines), 'scenes': scenes}, cues, assets


def test_short_disjoint_windows_cover_real_narration_without_reusing_frames():
    content, cues, assets = fixture()
    plan = allocate_cut_plan(content, cues, assets, 15)
    assert validate_shot_plan(plan, 15)['source_reuse_seconds'] == 0
    assert {s['asset_id'] for s in plan} == {'one', 'two', 'three'}
    for i in range(3):
        shots = [s for s in plan if s['scene_index'] == i]
        assert sum(s['duration'] for s in shots) == pytest.approx(5)
        assert all(s['asset_id'] == assets[i]['id'] for s in shots)
        assert all(2 <= s['duration'] <= 3 for s in shots)


def test_unused_unrelated_footage_cannot_fill_an_approved_scene_shortage():
    content, cues, assets = fixture()
    assets[1]['clips'] = [{'start': 0, 'end': 2}]
    extra = deepcopy(assets[0]); extra.update(id='unrelated', sha256='unrelated')
    assets.append(extra)
    with pytest.raises(ValueError, match='第2段.*需5.00秒.*总共2.00秒'):
        allocate_cut_plan(content, cues, assets, 15)
