"""Weekly paper work is independent of daily six and can never enter Douyin."""
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models.factory import FactoryConfig,FactoryRun,FactoryWorkOrder,FactoryItem
from services import factory_weekly_papers as weekly,factory_service
from services.factory_team import editor_quotas,validate_editor_plan
from services.factory_editorial_policy import POLICY

@pytest.fixture
def db():
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:yield db
    engine.dispose()


def test_weekly_papers_use_separate_quota_not_daily_paper_slots():
    assert editor_quotas()=={'producer':4,'producer2':0,'producer3':2}
    assert editor_quotas(1,'weekly_papers')=={'producer':0,'producer2':1,'producer3':0}
    assert editor_quotas(2,'weekly_papers')=={'producer':0,'producer2':2,'producer3':0}
    with pytest.raises(ValueError):editor_quotas(3,'weekly_papers')
    assert validate_editor_plan([{'category':'论文讲解','source_ids':[]}],[],editor_quotas(1,'weekly_papers'))==['producer2']


def test_paper_due_days_are_wednesday_and_saturday_and_do_not_preempt_daily(db,monkeypatch):
    config=FactoryConfig();db.add(config)
    active=FactoryRun(id='daily',active_slot='factory',settings_snapshot={'production_kind':'daily'});db.add(active);db.commit()
    enqueue=Mock(side_effect=AssertionError('Daily rendering owns lane'))
    monkeypatch.setattr(factory_service,'start_run',enqueue)
    result=weekly.plan_weekly_papers(db,config,datetime(2026,11,18,12,tzinfo=ZoneInfo('Asia/Shanghai')))
    assert result['status']=='waiting_for_production_lane'
    assert {o.key for o in db.query(FactoryWorkOrder)}=={'weekly-paper:2026-11-18','weekly-paper:2026-11-21'}
    enqueue.assert_not_called()


def test_weekly_starts_one_and_never_requeues_failed_or_completed_batch(db,monkeypatch):
    cfg=FactoryConfig();db.add(cfg);db.commit()
    def create(session,**kwargs):
        assert kwargs['production_kind']=='weekly_papers' and kwargs['target_count']==1
        r=FactoryRun(target_count=1,active_slot='factory',schedule_key=kwargs['schedule_key'],settings_snapshot={'production_kind':'weekly_papers'})
        session.add(r);session.commit();return r
    start=Mock(side_effect=create);monkeypatch.setattr(factory_service,'start_run',start)
    at=datetime(2026,11,18,12,tzinfo=ZoneInfo('Asia/Shanghai'))
    assert weekly.plan_weekly_papers(db,cfg,at)['status']=='queued'
    run=db.query(FactoryRun).one();run.status='failed';run.active_slot=None;db.commit()
    weekly.plan_weekly_papers(db,cfg,at)
    assert start.call_count==1
    assert db.get(FactoryWorkOrder,'weekly-paper:2026-11-18').status=='blocked'


def test_existing_completed_paper_counts_for_this_week_without_recreating_it(db,monkeypatch):
    cfg=FactoryConfig();r=FactoryRun(id='prior',active_slot=None);db.add_all([cfg,r]);db.flush()
    paper=FactoryItem(run_id=r.id,position=0,status='completed',brief={'editor_id':'producer2'});db.add(paper);db.flush()
    db.add(FactoryWorkOrder(key='weekly-paper:2026-11-18',kind='weekly_paper',owner='producer2',title='已提前完成',acceptance='真实成片',status='completed',due_at=datetime(2026,11,18,12),evidence={'preexisting_paper_item_id':paper.id}));db.commit()
    start=Mock(side_effect=AssertionError('Existing completed paper must not be redone'))
    monkeypatch.setattr(factory_service,'start_run',start)
    weekly.plan_weekly_papers(db,cfg,datetime(2026,11,18,12,tzinfo=ZoneInfo('Asia/Shanghai')))
    start.assert_not_called()
    assert db.get(FactoryWorkOrder,'weekly-paper:2026-11-18').evidence['done']==1


def test_weekly_budget_is_two_extra_to_daily_six_and_cannot_be_bypassed(db,monkeypatch):
    from services.factory_distribution import local_day
    from tasks.factory_tasks import produce_run
    monkeypatch.setattr(factory_service,'queue_available',lambda:True)
    monkeypatch.setattr(factory_service,'ensure_memory_available',lambda:None)
    enqueue=Mock(return_value=SimpleNamespace(id='weekly-task'))
    monkeypatch.setattr(produce_run,'delay',enqueue)
    day=local_day()
    db.add(FactoryConfig())
    db.add(FactoryRun(id='six-daily',active_slot=None,status='completed',target_count=6,
        settings_snapshot={'distribution_policy':POLICY,'production_kind':'daily','production_day':day}))
    db.commit()
    db.get(FactoryRun,'six-daily').active_slot=None;db.commit()
    first=factory_service.start_run(db,target_count=1,production_kind='weekly_papers')
    assert first.settings_snapshot['editor_quotas']=={'producer':0,'producer2':1,'producer3':0}
    first.active_slot=None;first.status='completed';db.commit()
    second=factory_service.start_run(db,target_count=1,production_kind='weekly_papers')
    second.active_slot=None;second.status='completed';db.commit()
    with pytest.raises(ValueError,match='本周2条'):
        factory_service.start_run(db,target_count=1,production_kind='weekly_papers')
    with pytest.raises(ValueError,match='当天6条'):
        factory_service.start_run(db,target_count=1)
    assert enqueue.call_count==2
