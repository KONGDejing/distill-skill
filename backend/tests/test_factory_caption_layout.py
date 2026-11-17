import subprocess
from pathlib import Path

import numpy as np
import pytest

from services.factory_caption_layout import caption_safety, require_caption_safety
from services.factory_montage import _montage_subtitles, _compact, read_subtitle_cues


def test_decoded_ass_text_clears_bottom_description_and_right_controls(tmp_path):
    text = '机器人能拿稳工具，还需要看它是否真的完成了任务。'
    cues = [{'start': 0, 'end': 2, 'text': _compact(text)}]
    scene = {'scene_index':0,'timeline_start':0,'timeline_end':2,'narration_anchor':text,
             'kind':'official_video','asset_id':'real','display_label':'官方演示','heading':'真实动作'}
    path=tmp_path/'captions.ass'
    result=_montage_subtitles(cues,[scene],{'real':{'id':'real','credit':'原始来源'}},
        {'script':text,'scenes':[{'narration_exact':text}],'presentation_style':'science_editorial_safe_v3'},path)
    # Isolate the narration layer, then test libass's actual pixel placement,
    # rather than assuming MarginV means the same thing as a text baseline.
    path.write_text('\n'.join(line for line in path.read_text().splitlines()
                             if not line.startswith('Dialogue:') or ',Narration,' in line))
    data=subprocess.check_output(['ffmpeg','-v','error','-f','lavfi','-i','color=black:s=1080x1920:d=2',
        '-vf',f'ass={path}','-ss','0.5','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
    frame=np.frombuffer(data,dtype=np.uint8).reshape((1920,1080,3))
    y,x=np.where(frame.min(axis=2)>180)
    assert len(y)>1000
    assert y.max()<1400 and x.max()<900 and x.min()>=70
    # Simulated app controls/description must leave every caption pixel intact.
    masked=frame.copy();masked[1450:]=0;masked[650:1450,920:]=0
    assert np.array_equal(frame,masked)
    assert _compact(''.join(c['text'] for c in read_subtitle_cues(path)))==_compact(text)
    assert result['subtitle_quality']['caption_safe_area_version']==1


def test_gate_rejects_old_burned_subtitles_even_if_final_file_claims_new_version():
    with pytest.raises(ValueError,match='字幕安全区'):
        require_caption_safety({'quality':{}},{'delivery_quality':{'caption_safe_area_version':1}})
    media={'quality':{'caption_safe_area_version':1,'caption_safe_area':caption_safety()}}
    require_caption_safety(media,{'delivery_quality':{'caption_safe_area_version':1}})
    media['quality']['caption_safe_area']['bottom']=1695
    with pytest.raises(ValueError,match='字幕安全区'):
        require_caption_safety(media,{'delivery_quality':{'caption_safe_area_version':1}})


def test_selected_safe_douyin_export_is_preferred_over_first_manual_package():
    from types import SimpleNamespace
    from services.factory_caption_layout import preferred_douyin_variant
    manual={'platform':'xiaohongshu','video_path':'old-manual.mp4'}
    fixed={'platform':'douyin','video_path':'fixed-douyin.mp4','delivery_quality':{'caption_safe_area_version':1}}
    item=SimpleNamespace(brief={'distribution':{'douyin_selected':True}},packages=[manual,fixed])
    assert preferred_douyin_variant(item) is fixed
    item.brief={'distribution':{'douyin_selected':False}}
    assert preferred_douyin_variant(item) is None
