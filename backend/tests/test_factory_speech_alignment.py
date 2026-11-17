import pytest

from services.factory_speech_alignment import original_text_cues
from services.factory_media import _match_text


def words(text):
    return [{'text':c,'start':i*.2,'end':(i+1)*.2} for i,c in enumerate(text)]


def test_alignment_preserves_original_spelling_and_puts_scene_start_at_actual_speech():
    script='AI先看真实动作。再看任务完成情况。'
    heard=_match_text(script)
    anchors=['AI先看真实动作。','再看任务完成情况。']
    cues,quality=original_text_cues(script,words(heard),len(heard)*.2,scene_anchors=anchors)
    assert ''.join(c['text'] for c in cues)==script
    assert quality['readback_similarity']==1 and quality['interpolated_characters']==0
    second=next(c for c in cues if c['text'].startswith('再看'))
    assert second['start']==pytest.approx(len(_match_text(anchors[0]))*.2)
    assert 'AI' in cues[0]['text']


def test_minor_recognition_error_is_disclosed_and_does_not_rewrite_the_approved_text():
    script='先看真实动作，再看任务完成情况。'
    heard=_match_text(script).replace('务','務')
    cues,quality=original_text_cues(script,words(heard),len(heard)*.2)
    assert ''.join(c['text'] for c in cues)==script
    assert quality['interpolated_characters']==1
    assert all(c['end']>c['start'] for c in cues)


def test_wrong_audio_or_an_unknown_scene_anchor_cannot_be_used_for_alignment():
    with pytest.raises(ValueError,match='匹配不足'):
        original_text_cues('先看真实动作。',words('完全不相干的说话内容'),3)
    with pytest.raises(ValueError,match='锚点不在原稿'):
        original_text_cues('先看真实动作。',words('先看真实动作'),3,scene_anchors=['凭空新增的台词。'])
