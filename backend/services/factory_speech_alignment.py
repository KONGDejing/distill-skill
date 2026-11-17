"""Align approved display text to a readback of the actual synthesized audio.

ASR is a verification aid, not evidence for the facts in a narration. Missing
characters are interpolated and disclosed, never replaced with recognized text.
"""
from difflib import SequenceMatcher
import json
from pathlib import Path

from services.factory_media import _match_text, _duration, _subtitles
from services.factory_montage import _subtitle_text_spans, _compact


def original_text_cues(script, words, duration, *, scene_anchors=(), min_similarity=0.90):
    expected = _match_text(script)
    heard, heard_times = '', []
    for word in words:
        text = _match_text(word['text'])
        if not text:
            continue
        start = max(0., min(duration, float(word['start'])))
        end = max(start, min(duration, float(word['end'])))
        heard += text
        heard_times.extend([(start+(end-start)*i/len(text), start+(end-start)*(i+1)/len(text))
                            for i in range(len(text))])
    comparison = SequenceMatcher(None, expected, heard, autojunk=False)
    score = comparison.ratio()
    if not expected or score < min_similarity:
        raise ValueError(f'配音回听与原稿匹配不足（{score:.1%}），暂停换入成片，需核对漏读或读错')
    times = [None]*len(expected)
    for block in comparison.get_matching_blocks():
        times[block.a:block.a+block.size] = heard_times[block.b:block.b+block.size]
    interpolated = sum(t is None for t in times)
    index = 0
    while index < len(times):
        if times[index] is not None:
            index += 1
            continue
        end_index = index
        while end_index < len(times) and times[end_index] is None:
            end_index += 1
        start = times[index-1][1] if index else 0.
        end = times[end_index][0] if end_index < len(times) else duration
        end = max(start, end)
        for i in range(index, end_index):
            times[i] = (start+(end-start)*(i-index)/(end_index-index),
                        start+(end-start)*(i-index+1)/(end_index-index))
        index = end_index
    cuts, cursor = {0,len(script)}, 0
    for anchor in scene_anchors:
        position = script.find(anchor, cursor)
        if position < 0:
            raise ValueError('分镜口播锚点不在原稿中，无法同步镜头')
        cuts.add(position);cuts.add(position+len(anchor));cursor=position+len(anchor)
    cues = []
    ordered = sorted(cuts)
    for first,last in zip(ordered,ordered[1:]):
        for begin,end in _subtitle_text_spans(script[first:last]):
            begin += first;end += first
            text = script[begin:end].strip()
            if not _match_text(text):
                continue
            left = min(len(times)-1,len(_match_text(script[:begin])))
            right = max(left,min(len(times)-1,len(_match_text(script[:end]))-1))
            start,finish = times[left][0],times[right][1]
            if finish-start < .01:
                raise ValueError('原稿字幕缺少有效口播时间，需核对语音识别对齐')
            cues.append({'text':text,'start':start,'end':finish,
                         'timing_basis':'asr_word_timestamps_with_original_text_interpolation'})
    if _compact(''.join(c['text'] for c in cues)) != _compact(script):
        raise ValueError('语音对齐未完整保留已审核文字')
    return cues, {'method':'asr_word_timestamps_with_original_text_interpolation',
                  'readback_similarity':round(score,4),'interpolated_characters':interpolated,
                  'normalized_characters':len(expected),'recognized_text':''.join(w['text'] for w in words)}


def align_audio(script, audio_path, directory, *, scene_anchors=(), model=None):
    from services.factory_resources import ensure_memory_available
    ensure_memory_available()
    if model is None:
        from services.whisper_service import _load_model
        model = _load_model()
    import re
    terms = list(dict.fromkeys(re.findall(r'[A-Za-z][A-Za-z0-9.-]{2,}',script)))
    segments,_ = model.transcribe(str(audio_path),language='zh',beam_size=3,vad_filter=True,
                                 word_timestamps=True,initial_prompt='术语：'+ '、'.join(terms[:25]))
    words = [{'text':w.word,'start':w.start,'end':w.end} for s in segments for w in s.words or []]
    duration = _duration(audio_path)
    cues,quality = original_text_cues(script,words,duration,scene_anchors=scene_anchors)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    subtitle=directory/'aligned-subtitles.ass';timings=directory/'aligned-speech-timings.json'
    _subtitles(cues,duration,subtitle)
    timings.write_text(json.dumps(cues,ensure_ascii=False,indent=2))
    (directory/'speech-alignment.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2))
    return {'subtitle_path':str(subtitle.resolve()),'speech_timings_path':str(timings.resolve()),
            'speech_alignment':quality,'duration':duration}
