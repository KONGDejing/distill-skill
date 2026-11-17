"""Reference vision uses actual image attachments and the configured billing route."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from services import factory_codex, distill_service, factory_llm


def test_codex_vision_attaches_real_files_with_private_copy_and_requested_reasoning(tmp_path, monkeypatch):
    source=tmp_path/'frame.jpg';source.write_bytes(b'actual sampled image bytes')
    monkeypatch.setattr(factory_codex.subprocess,'run',Mock(return_value=SimpleNamespace(returncode=0,
        stdout='Logged in using ChatGPT',stderr='')))
    monkeypatch.setattr(factory_codex.settings,'FACTORY_CODEX_REASONING_EFFORT','medium')
    observed={}
    events='\n'.join(json.dumps(v) for v in [
        {'type':'item.completed','item':{'type':'agent_message','text':'{"observed":true}'}},
        {'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':5}}])
    def start(command,**kwargs):
        attachment=Path(command[command.index('--image')+1])
        observed.update(path=attachment,effort=command[command.index('model_reasoning_effort="high"')],cwd=kwargs['cwd'])
        assert attachment.read_bytes()==source.read_bytes()
        assert attachment.parent==Path(kwargs['cwd']) and attachment!=source
        assert 'OPENAI_API_KEY' not in kwargs['env']
        process=Mock();process.returncode=0;process.communicate.return_value=(events,'')
        return process
    monkeypatch.setattr(factory_codex.subprocess,'Popen',start)
    result=factory_codex.create_response(model='test-model',system='JSON only',messages=[{'role':'user','content':'inspect samples'}],
        max_tokens=100,image_paths=[source],reasoning_effort='high')
    assert result.usage.input_tokens==20
    assert source.exists() and not observed['path'].exists()


def test_distillation_uses_subscription_route_instead_of_legacy_api(monkeypatch,tmp_path):
    dna={key:{} for key in ['value_positioning','viral_techniques','content_preferences','language_style','content_calendar']}
    dna['visual_style']={'scope':'actual sampled frames','cover':'observed subject'}
    reply=SimpleNamespace(content=[SimpleNamespace(type='text',text=json.dumps(dna))],
        usage=SimpleNamespace(input_tokens=100,output_tokens=20))
    monkeypatch.setattr(factory_llm,'model_runtime',lambda:{'provider':'codex','model':'subscription-test'})
    call=Mock(return_value=reply);monkeypatch.setattr(factory_codex,'create_response',call)
    legacy=Mock(side_effect=AssertionError('Legacy API must not be used'))
    monkeypatch.setattr(distill_service.anthropic,'Anthropic',legacy)
    pictures=[tmp_path/'sample.jpg']
    result=distill_service.analyze_blogger_content(['real transcript'],image_paths=pictures,video_metadata=[{'duration_seconds':60}])
    assert result['viral_techniques']['visual_style']['scope']=='actual sampled frames'
    assert call.call_args.kwargs['model']=='subscription-test'
    assert call.call_args.kwargs['image_paths']==pictures
    assert call.call_args.kwargs['reasoning_effort']=='high'
    assert result['_model_usage']['input_tokens']==100
    legacy.assert_not_called()
