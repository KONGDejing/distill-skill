"""Brief Douyin descriptions: one hook, at most two topics, explicit AI label."""
import re

DOUYIN_CAPTION_LIMIT = 50
AI_LABEL = '内容由AI生成'


def compact_douyin_caption(variant):
    tags=[]
    for value in variant.get('tags') or []:
        tag=re.sub(r'[\s#＃]+','',str(value))
        if tag and len(tag)<=10 and tag not in tags:
            tags.append(tag)
        if len(tags)==2:
            break
    if not tags:
        tags=['AI科技']
    suffix=' '.join('#'+tag for tag in tags)
    available=DOUYIN_CAPTION_LIMIT-len(AI_LABEL)-len(suffix)-2
    candidates=[]
    for value in [variant.get('caption_short'),variant.get('caption')]:
        if not isinstance(value,str):continue
        for paragraph in value.splitlines():
            paragraph=re.sub(r'[#＃][^\s#＃]+','',paragraph).replace(AI_LABEL,'').strip()
            if not paragraph or paragraph.startswith(('来源','AI辅助','http')):continue
            match=re.search(r'[。！？!?]',paragraph)
            candidates.append(paragraph[:match.end()] if match else paragraph)
            break
    candidates += [str(variant.get('title') or '').strip(),'这次AI新进展，你怎么看？']
    body=next((c for c in candidates if c and len(c)<=available),None)
    if body is None:
        tags=tags[:1];suffix='#'+tags[0];available=DOUYIN_CAPTION_LIMIT-len(AI_LABEL)-len(suffix)-2
        body=next(c for c in candidates if c and len(c)<=available)
    caption=body+'\n'+AI_LABEL
    if len((caption+'\n'+suffix).strip())>DOUYIN_CAPTION_LIMIT:
        raise ValueError('抖音发布文案、话题及AI说明合计超过50字')
    return caption,tags


def caption_text(caption,tags):
    return (caption+'\n'+' '.join('#'+tag for tag in tags)).strip()
