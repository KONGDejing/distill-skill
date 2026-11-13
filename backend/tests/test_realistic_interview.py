from copy import deepcopy
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('realistic_interview', Path(__file__).resolve().parents[2] / 'scripts/render_realistic_interview.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def story():
    return {'duration': 3, 'fiction_notice': 'AI声像模拟·虚构面试', 'dialogue': [
        {'speaker': 'hr', 'text': '您好', 'headline': '虚构问题', 'start': 0, 'end': .8, 'cut_end': 1},
        {'speaker': 'huang', 'text': '您好', 'headline': '虚构回答', 'start': 1, 'end': 2.8, 'cut_end': 3}]}


def test_reviewed_timeline_keeps_all_audio_intervals():
    value = story(); original = deepcopy(value)
    renderer.validate_timeline(value)
    assert value == original


@pytest.mark.parametrize('error', ['gap', 'overlap', 'missing-disclosure', 'zero-speech', 'duration', 'long-turn'])
def test_invalid_timeline_is_rejected_before_model_loading(error):
    value = story()
    if error == 'gap': value['dialogue'][1]['start'] = 1.1
    if error == 'overlap': value['dialogue'][1]['start'] = .9
    if error == 'missing-disclosure': value['fiction_notice'] = ''
    if error == 'zero-speech': value['dialogue'][0]['end'] = 0
    if error == 'duration': value['duration'] = 3.2
    if error == 'long-turn':
        value['dialogue'][1].update(end=13, cut_end=14); value['duration'] = 14
    with pytest.raises(ValueError): renderer.validate_timeline(value)
