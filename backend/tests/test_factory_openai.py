from unittest.mock import Mock
import httpx
import pytest
from services import factory_openai
from services.factory_llm import _account_error


def test_responses_uses_own_key_and_reasoning_compatible_contract(monkeypatch):
    monkeypatch.setattr(factory_openai.settings, 'OPENAI_API_KEY', 'test-secret')
    monkeypatch.setattr(factory_openai.settings, 'OPENAI_BASE_URL', 'https://api.openai.com/v1')
    client = Mock()
    client.post.return_value = httpx.Response(200, json={'status':'completed', 'usage': {'input_tokens':30,'output_tokens':10},
        'output':[{'type':'reasoning'}, {'type':'message','content':[{'type':'output_text','text':'{"approved":false}'}]}]})
    ctx = Mock(); ctx.__enter__ = Mock(return_value=client); ctx.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(factory_openai.httpx, 'Client', Mock(return_value=ctx))
    result = factory_openai.create_response(model='gpt-6-astra',system='Return JSON',messages=[{'role':'user','content':'test'}],max_tokens=200)
    assert result.content[0].text == '{"approved":false}' and result.usage.input_tokens == 30
    request = client.post.call_args
    assert request.args[0] == 'https://api.openai.com/v1/responses'
    assert request.kwargs['headers'] == {'Authorization':'Bearer test-secret'}
    assert request.kwargs['json']['store'] is False
    assert 'temperature' not in request.kwargs['json']
    assert request.kwargs['json']['reasoning'] == {'effort':'low'}


def test_missing_key_stops_before_network_and_never_uses_anthropic_key(monkeypatch):
    monkeypatch.setattr(factory_openai.settings, 'OPENAI_API_KEY', '')
    network=Mock(); monkeypatch.setattr(factory_openai.httpx, 'Client', network)
    with pytest.raises(factory_openai.ProviderError) as e:
        factory_openai.create_response(model='gpt-6-astra',system='JSON',messages=[],max_tokens=200)
    assert e.value.status_code == 401
    network.assert_not_called()


def test_openai_quota_is_terminal_without_echoing_private_provider_message():
    exc=factory_openai.ProviderError(429,{'error':{'code':'insufficient_quota','message':'private account details'}})
    message=_account_error(exc)
    assert '额度不足' in message and 'private' not in message
    assert 'private' not in str(exc)


def test_codex_subscription_does_not_inherit_api_keys_or_gateway_secrets(monkeypatch):
    from services.factory_codex import subscription_environment
    for key in ('OPENAI_API_KEY','CODEX_API_KEY','ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','CODEX_ACCESS_TOKEN'):
        monkeypatch.setenv(key,'private-test-value')
    env=subscription_environment()
    assert not any(k in env for k in ('OPENAI_API_KEY','CODEX_API_KEY','ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','CODEX_ACCESS_TOKEN'))


def test_codex_counts_cached_tokens_and_rejects_missing_usage():
    import json
    from services.factory_codex import parse_events
    lines=[{'type':'item.completed','item':{'type':'agent_message','text':'{"ok":true}'}},
           {'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':5}}]
    r=parse_events('\n'.join(json.dumps(v) for v in lines))
    assert r.usage.input_tokens==100 and r.usage.cached_input_tokens==80
    with pytest.raises(factory_openai.ProviderError):parse_events(json.dumps(lines[0]))
    with pytest.raises(factory_openai.ProviderError) as e:
        parse_events(json.dumps({'type':'turn.failed','error':{'message':'usage_limit_exceeded private account'}}))
    assert e.value.status_code==429 and 'private' not in str(e.value)


def test_paper_compaction_preserves_page_evidence_once_without_mutating_saved_content():
    from services.factory_llm import compact_payload
    document={'url':'https://arxiv.org/abs/example','pages':[{'page':1,'text':'original evidence'}]}
    original={'brief':{'paper_document':document},'sources':[{'id':'paper-fulltext','pages':document['pages'],'text':'original evidence'}]}
    compact=compact_payload(original)
    assert compact['sources'][0]['pages']==document['pages']
    assert 'text' not in compact['sources'][0] and 'pages' not in compact['brief']['paper_document']
    assert original['brief']['paper_document']['pages']==document['pages']
