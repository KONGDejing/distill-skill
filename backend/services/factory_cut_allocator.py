"""Allocate reviewed source windows to actual narration intervals.

This bounded optimizer changes neither approved asset choices nor any quality
threshold. The result still passes the renderer's ordinary provenance guards.
"""
import math

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from services.factory_editorial import validate_editorial_scenes
from services.factory_montage import FPS, _anchor_time, validate_editor_cut_plan


def allocate_cut_plan(content, cues, assets, duration):
    scenes = content['scenes']
    validate_editorial_scenes(content['script'], scenes, assets)
    anchors = [_anchor_time(scene['narration_exact'], cues) for scene in scenes[1:]]
    if any(anchor is None for anchor in anchors):
        raise ValueError('精确剪辑表的口播锚点必须出现在真实字幕中')
    starts = [0] + [round(anchor * FPS) for anchor in anchors]
    ends = starts[1:] + [math.ceil(duration * FPS)]
    if any(end <= start for start, end in zip(starts, ends)):
        raise ValueError('口播段落必须按真实字幕时序排列')
    windows = []
    for asset in assets:
        for clip in asset['clips']:
            first = math.ceil(clip['start'] * FPS - 1e-6)
            last = math.floor(clip['end'] * FPS + 1e-6)
            if last - first >= 2 * FPS:
                windows.append((asset, clip, first, last))
    edges = [(i, w) for i, scene in enumerate(scenes)
             for w, (asset, _, _, _) in enumerate(windows)
             if asset['id'] in scene['asset_ids']]
    count = len(edges)
    if not count or count > 4096:
        raise ValueError('可分配的素材窗口为空或超过有界规划规模')

    # Integer source frames and binary used-window indicators. Each scene gets
    # its exact spoken duration; each source window can be consumed only once.
    offset = len(scenes)
    rows = offset + len(windows) + 2 * count
    matrix = lil_matrix((rows, 2 * count), dtype=float)
    lower = np.full(rows, -np.inf)
    upper = np.full(rows, np.inf)
    capacities = []
    for i in range(len(scenes)):
        lower[i] = upper[i] = ends[i] - starts[i]
    for w, (_, _, first, last) in enumerate(windows):
        upper[offset + w] = last - first
    for j, (i, w) in enumerate(edges):
        capacity = windows[w][3] - windows[w][2]
        capacities.append(capacity)
        matrix[i, j] = matrix[offset + w, j] = 1
        row = offset + len(windows) + 2 * j
        matrix[row, j], matrix[row, count + j], upper[row] = 1, -capacity, 0
        matrix[row + 1, j], matrix[row + 1, count + j] = 1, -2 * FPS
        lower[row + 1] = 0
    result = milp(
        np.concatenate([np.zeros(count), np.ones(count)]),
        integrality=np.ones(2 * count),
        bounds=Bounds(np.zeros(2 * count), np.array(capacities + [1] * count)),
        constraints=LinearConstraint(matrix.tocsr(), lower, upper),
        options={'time_limit': 20, 'mip_rel_gap': .01},
    )
    if result.x is None:
        budgets = []
        for i, scene in enumerate(scenes):
            available = sum(last - first for asset, _, first, last in windows if asset['id'] in scene['asset_ids'])
            budgets.append(f"第{i+1}段[{scene['heading']}]口播需{(ends[i]-starts[i])/FPS:.2f}秒，所选源窗总共{available/FPS:.2f}秒")
        raise ValueError('精确镜头预算无可行解：' + str(result.message) + '。' + '；'.join(budgets)
                         + '。这些容量是未扣除其他段共享占用的上界；同一源窗全片只能消费一次，每次至少2秒。请按真实口播重拆段，选择有语义依据的更多已提供素材并保留后段预算，不得循环或使用无关画面。')
    amounts = np.rint(result.x[:count]).astype(int)
    used = {w: window[2] for w, window in enumerate(windows)}
    plan = []
    for i, scene in enumerate(scenes):
        cursor = starts[i]
        ordered = sorted(
            ((j, w) for j, (index, w) in enumerate(edges) if index == i and amounts[j] > 0),
            key=lambda pair: scene['asset_ids'].index(windows[pair[1]][0]['id']),
        )
        for j, w in ordered:
            asset, clip, _, _ = windows[w]
            amount = int(amounts[j])
            cuts = math.ceil(amount / (4.5 * FPS))
            base, extra = divmod(amount, cuts)
            for k in range(cuts):
                frames = base + int(k < extra)
                first, last = used[w], used[w] + frames
                region = clip.get('focus_region') or (asset.get('focus_regions') or [None])[0]
                plan.append({
                    'shot_index': len(plan), 'scene_index': i, 'asset_id': asset['id'],
                    'source_sha256': asset['sha256'], 'kind': asset['kind'],
                    'source_start': first / FPS, 'source_end': last / FPS,
                    'duration': frames / FPS, 'timeline_start': cursor / FPS,
                    'timeline_end': (cursor + frames) / FPS,
                    'source_url': asset['source_url'], 'display_label': asset['display_label'],
                    'match_reason': scene['match_reason'], 'narration_anchor': scene['narration_exact'],
                    'heading': scene['heading'], 'body': scene.get('body', ''),
                    'focus_region': region, 'layout': 'full_frame_and_detail' if region else 'full_frame',
                    'timing_basis': 'exact_subtitle_anchors_and_constrained_source_windows',
                    'complete_source_frame_preserved': True,
                })
                cursor, used[w] = cursor + frames, last
        if cursor != ends[i]:
            raise ValueError('精确镜头分配未完整覆盖对应口播')
    return validate_editor_cut_plan(content, cues, assets, plan, duration)
