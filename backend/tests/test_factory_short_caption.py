from services.factory_short_caption import compact_douyin_caption,caption_text,AI_LABEL


def test_full_caption_including_topics_and_ai_label_fits_fifty_characters():
    caption,tags=compact_douyin_caption({'title':'机器人拿稳工具，就算会用了？',
        'caption':'这是一个特别长的完整说明。'+'后面的说明不能继续堆进发布正文。'*20,
        'tags':['机器人','具身智能','AI','机器人']})
    assert len(caption_text(caption,tags))<=50 and tags==['机器人','具身智能']
    assert AI_LABEL in caption


def test_existing_short_hook_remains_unchanged_and_ai_label_is_not_duplicated():
    variant={'title':'公司新闻','caption':'AI公司上市，你最想看什么？\n内容由AI生成','tags':['Claude','AI科技']}
    caption,tags=compact_douyin_caption(variant)
    assert caption.startswith('AI公司上市，你最想看什么？')
    assert caption.count(AI_LABEL)==1
    assert compact_douyin_caption({**variant,'caption':caption,'tags':tags})==(caption,tags)


def test_long_paragraph_falls_back_to_complete_title_not_a_truncated_claim():
    caption,tags=compact_douyin_caption({'title':'模型更新，进展在哪？','caption':'连续长句没有标点'*30,'tags':['大模型','AI科技']})
    assert caption.splitlines()[0]=='模型更新，进展在哪？'
    assert len(caption_text(caption,tags))<=50


def test_copyable_manual_descriptions_are_short_on_each_short_video_platform():
    from services.factory_handoff import build_handoff, handoff_files
    content={'title':'科技新进展','sources':[{'url':'https://example.org/research','title':'原始来源'}]}
    for platform in ('douyin','kuaishou','xiaohongshu'):
        h=build_handoff(content,{'platform':platform,'title':'机器人学会了吗？',
            'caption':'机器人学会了吗？'+'详细介绍应保留在随附资料。'*30,
            'tags':['机器人','具身智能','科技']})
        assert len(h['description'])<=50
        assert h['description'].count('#')==2
        assert AI_LABEL in h['description']
        assert h['description'] in handoff_files(h)['发布文案.txt']
        assert content['sources'][0]['url'] in handoff_files(h)['资料来源.txt']
