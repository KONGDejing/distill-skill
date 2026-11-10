from copy import deepcopy
from unittest.mock import Mock

import pytest

from services.factory_editorial import validate_editorial_scenes, validate_follow_prompt
from services import factory_service


SCRIPT = "先看雪地冠鹤。再看滑雪移动。最后看水面小船。"
ASSETS = [{"id": str(i)} for i in range(3)]


def test_follow_prompt_must_be_once_in_spoken_ending_with_recurring_value():
    prompt = "想看懂AI演示里的门道，可以关注这个系列。"
    content = {"script": "为什么演示好看还不能证明可靠？这里给出判断方法。" + prompt,
               "hook": "为什么演示好看还不能证明可靠？", "follow_prompt": prompt,
               "follow_value": "持续拆解官方演示的能力与限制"}
    assert validate_follow_prompt(content)["follow_prompt"] == prompt
    for update in ({"script": content["script"] + "另一个结尾"}, {"follow_value": "涨粉"},
                   {"hook": "不存在的开场"}, {"script": content["script"] + prompt}):
        with pytest.raises(ValueError):
            validate_follow_prompt({**content, **update})


def scenes():
    return [{"heading": "画面", "body": "观察演示", "narration_exact": line + "。",
             "asset_ids": [str(i)], "match_reason": "展示这句话描述的主体动作"}
            for i, line in enumerate(SCRIPT.rstrip("。").split("。"))]


def test_editorial_contract_requires_complete_ordered_narration():
    assert validate_editorial_scenes(SCRIPT, scenes(), ASSETS)
    for broken in (scenes()[1:], scenes()[:-1], list(reversed(scenes()))):
        with pytest.raises(ValueError):
            validate_editorial_scenes(SCRIPT, broken, ASSETS)
    broken = scenes()
    broken[1]["narration_exact"] = "再看机器人。"
    with pytest.raises(ValueError):
        validate_editorial_scenes(SCRIPT, broken, ASSETS)


@pytest.mark.parametrize("field,value", [("asset_ids", ["invented"]), ("asset_ids", []), ("match_reason", "")])
def test_edit_cannot_use_unexplained_or_unknown_footage(field, value):
    broken = scenes()
    broken[0][field] = value
    with pytest.raises(ValueError):
        validate_editorial_scenes(SCRIPT, broken, ASSETS)


def test_rejected_picture_alignment_is_returned_to_producer_before_rendering(monkeypatch):
    calls = Mock(side_effect=[{"scenes": scenes()}, {"approved": False, "score": 60, "reason": "画面不匹配"},
                             {"scenes": scenes()}, {"approved": True, "score": 90, "reason": "已匹配"}])
    monkeypatch.setattr(factory_service, "call_agent", calls)
    monkeypatch.setattr(factory_service, "check_cancel", lambda _: None)
    monkeypatch.setattr(factory_service, "emit", Mock())
    result = factory_service._plan_edit("run", {"script": SCRIPT}, ASSETS, "tech_montage")
    assert result["editorial_review"]["scope"] == "source_description_alignment"
    assert [call.args[1] for call in calls.call_args_list] == ["producer", "reviewer", "producer", "reviewer"]
    assert calls.call_args_list[2].args[2]["editorial_feedback"]["approved"] is False


@pytest.mark.parametrize("owner", ["producer2", "producer3"])
def test_each_new_editor_executes_its_own_model_step(monkeypatch, owner):
    from unittest.mock import Mock
    from services import factory_service
    monkeypatch.setattr(factory_service, "check_cancel", lambda *_: None)
    monkeypatch.setattr(factory_service, "emit", lambda *_: None)
    calls = Mock(return_value={"scenes": [{"heading": "开场", "body": ""}, {"heading": "结尾", "body": ""}]})
    monkeypatch.setattr(factory_service, "call_agent", calls)
    factory_service._plan_edit("run", {"script": "测试"}, [], "cards", owner=owner)
    assert calls.call_args.args[1] == owner


def test_persistent_edit_rejection_stops_after_bounded_revision(monkeypatch):
    bad = deepcopy(scenes())
    bad[0]["asset_ids"] = []
    calls = Mock(return_value={"scenes": bad})
    monkeypatch.setattr(factory_service, "call_agent", calls)
    monkeypatch.setattr(factory_service, "check_cancel", lambda _: None)
    monkeypatch.setattr(factory_service, "emit", Mock())
    with pytest.raises(ValueError):
        factory_service._plan_edit("run", {"script": SCRIPT}, ASSETS, "tech_montage")
    assert calls.call_count == 2


def test_director_hook_contract_requires_evidence_and_in_script_payoff():
    from services.factory_editorial import validate_engagement_plan, validate_engagement_delivery
    plan={'hooks':['机器人会走路，就能直接推车吗？','推车这件事，为什么机器人还要学？'],'selected':0,
          'viewer_value':'看懂机器人接触力与平衡', 'payoff':'解释推车同时控制平衡的原因', 'opening_visual':'展示对应论文原始标题与作者',
          'follow_value':'持续看懂机器人研究的真实进展', 'experiment':'只比较开场，数据不足不判胜负', 'source_ids':['source']}
    assert validate_engagement_plan(plan,[{'id':'source'}]) == plan
    with pytest.raises(ValueError):validate_engagement_plan(plan,[{'id':'other'}])
    prompt='想持续看懂机器人研究，可以关注这个系列。'
    answer='因为推车会改变接触力，需要同时控制平衡。'
    content={'hook':plan['hooks'][0], 'script':plan['hooks'][0]+answer+prompt, 'follow_prompt':prompt,
             'follow_value':plan['follow_value'], 'payoff_excerpt':answer}
    validate_engagement_delivery(content,plan)
    with pytest.raises(ValueError):validate_engagement_delivery({**content,'payoff_excerpt':'正文没有这句话，不能冒充兑现承诺'},plan)


def test_newsroom_handoff_rejects_invented_evidence_and_index_pages():
    from services.factory_newsroom import validate_packet
    source={'id':'s','url':'https://www.ces.tech/example','kind':'news','text':'This is the actual original statement, including its boundaries.'}
    packet={'facts':[{'source_id':'s','claim':'一个有限的已确认事实','evidence_quote':'This is the actual original statement'}],
            'event_date':'未确认', 'limitations':'缺少可下载视频，不称已取得现场素材',
            'visual_requests':[{'source_id':'s','needed_picture':'对应本条事实的官方演示'}]}
    assert validate_packet(packet,[source]) == packet
    with pytest.raises(ValueError):validate_packet(packet,[{**source,'kind':'reference_index'}])
    bad=deepcopy(packet);bad['facts'][0]['evidence_quote']='A fabricated statement not in the source.'
    with pytest.raises(ValueError):validate_packet(bad,[source])
