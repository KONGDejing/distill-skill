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


def test_comparison_attaches_real_factory_images_after_reference_and_feeds_edit_actions(monkeypatch,tmp_path):
    dna={key:{} for key in ['value_positioning','viral_techniques','content_preferences','language_style','content_calendar']}
    dna['factory_comparison']={'scope':'提供的真实抽帧','observed_gaps':[{'aspect':'主体面积','reference_observation':'主体可见',
        'factory_observation':'画面留白多','evidence':'两个样本的2秒抽帧','action':'扩大经核对的主体取景'}],
        'priority_actions':[{'owner':'producer','action':'按对象取景','acceptance':'不遮挡演示与字幕'}]}
    reply=SimpleNamespace(content=[SimpleNamespace(type='text',text=json.dumps(dna))],usage=SimpleNamespace(input_tokens=20,output_tokens=10))
    monkeypatch.setattr(factory_llm,'model_runtime',lambda:{'provider':'codex','model':'subscription-test'})
    call=Mock(return_value=reply);monkeypatch.setattr(factory_codex,'create_response',call)
    reference,factory=tmp_path/'reference.jpg',tmp_path/'factory.jpg'
    result=distill_service.analyze_blogger_content(['实际转写'],image_paths=[reference],factory_image_paths=[factory],
        factory_metadata=[{'item_id':'actual','title':'本工厂成片','sample_times_seconds':[.05,2,7]}])
    assert call.call_args.kwargs['image_paths']==[reference,factory]
    assert result['_model_usage']['actual_reference_images']==1 and result['_model_usage']['actual_factory_images']==1
    comparison=result['viral_techniques']['factory_comparison']
    assert comparison['factory_samples'][0]['item_id']=='actual'
    assert '没有完整动作或音频试听' in comparison['actual_evidence_scope']


def test_malformed_comparison_cannot_replace_valid_previous_analysis(monkeypatch):
    dna={key:{} for key in ['value_positioning','viral_techniques','content_preferences','language_style','content_calendar']}
    dna['factory_comparison']={'observed_gaps':[{'aspect':{'bad':'object'}}],'priority_actions':[]}
    reply=SimpleNamespace(content=[SimpleNamespace(type='text',text=json.dumps(dna))],usage=SimpleNamespace(input_tokens=1,output_tokens=1))
    monkeypatch.setattr(factory_llm,'model_runtime',lambda:{'provider':'codex','model':'test'})
    monkeypatch.setattr(factory_codex,'create_response',Mock(return_value=reply))
    assert distill_service.analyze_blogger_content(['实际稿'],factory_metadata=[{'item_id':'current'}]) is None


def test_factory_samples_use_latest_real_nonpaper_exports_and_preserve_tracks(tmp_path,monkeypatch):
    from datetime import datetime,timedelta
    from sqlalchemy import create_engine, null
    from sqlalchemy.orm import Session
    from database import Base
    from models.factory import FactoryRun,FactoryItem
    from services import reference_visuals,video_downloader
    from config import settings
    storage=tmp_path/'storage';storage.mkdir()
    monkeypatch.setattr(settings,'STORAGE_DIR',storage)
    monkeypatch.setattr(video_downloader,'valid_video',lambda p:Path(p).is_file())
    sampled=[]
    def sample(source,target):
        from PIL import Image
        sampled.append(Path(source).name)
        Path(target).parent.mkdir(parents=True,exist_ok=True)
        Image.new('RGB',(1080,355)).save(target)
        return {'image_path':str(target),'sample_times_seconds':[.05,2,7],'duration_seconds':150}
    monkeypatch.setattr(reference_visuals,'sample_reference',sample)
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as db:
        old=FactoryRun(active_slot=null(),status='completed',created_at=datetime.utcnow()-timedelta(days=1),settings_snapshot={})
        current=FactoryRun(active_slot=null(),status='completed',created_at=datetime.utcnow(),settings_snapshot={})
        db.add_all([old,current]);db.flush()
        for run,position,owner,name,style in [
            (old,0,'producer3','old.mp4',''),(current,0,'producer','wide.mp4',''),
            (current,1,'producer','readable.mp4','science_editorial_focus_v2'),
            (current,2,'producer2','paper.mp4',''),(current,3,'producer3','news.mp4','')]:
            path=storage/name;path.write_bytes(b'fixture')
            db.add(FactoryItem(run_id=run.id,position=position,status='completed',content={'title':name,'editor_id':owner,'presentation_style':style},
                packages=[{'platform':'douyin','video_path':str(path)}]))
        db.commit()
        images,metadata=reference_visuals.sample_factory_comparison(db,tmp_path/'samples')
        assert sampled==['readable.mp4','news.mp4']
        assert len(images)==1 and [m['editor_id'] for m in metadata]==['producer','producer3']
        assert [m['image_panel'] for m in metadata]==['上半部分','下半部分']
        assert all(m['sample_role']=='factory_film' for m in metadata)
    engine.dispose()
