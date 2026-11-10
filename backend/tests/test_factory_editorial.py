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
