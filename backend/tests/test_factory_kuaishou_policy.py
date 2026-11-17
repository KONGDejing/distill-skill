"""The owner's new manual rule overrides all historical Kuaishou permissions."""
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models.factory import FactoryConfig,FactoryRun,FactoryRelease
from services import factory_platform_policy as policy,creator_connector,factory_schedule,factory_transport_health
from services.factory_kuaishou_policy import authorized


@pytest.mark.parametrize('action',['login','inspect','prepare','publish'])
def test_kuaishou_no_new_account_commands_even_with_old_payload_or_login(monkeypatch,action):
    start=Mock(side_effect=AssertionError('No account startup allowed'))
    monkeypatch.setattr(creator_connector,'_ensure_daemon',start)
    with pytest.raises(policy.AutomationDisabled):creator_connector.submit_command('kuaishou',action,{'item_id':'old-selected'})
    start.assert_not_called()


def test_cached_old_run_permission_never_reenables_kuaishou():
    run=FactoryRun(settings_snapshot={'distribution_policy':'douyin-three-tracks-v2','kuaishou_auto_policy':'kuaishou-two-exclusive-v1'})
    assert not authorized(run) and not policy.automation_enabled('kuaishou')


def test_manual_kuaishou_creates_no_schedule_or_network_restore(monkeypatch):
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        config=FactoryConfig(platforms=['douyin','kuaishou']);db.add(config);db.commit()
        factory_schedule.plan_kuaishou_slots(db,config,datetime(2026,11,17,18,tzinfo=ZoneInfo('Asia/Shanghai')))
        assert db.query(FactoryRelease).count()==0
    start=Mock();probe=Mock();monkeypatch.setattr(creator_connector,'_ensure_daemon',start)
    monkeypatch.setattr(factory_transport_health,'_probe',probe)
    factory_transport_health.restore_saved_session({'ready':True})
    assert factory_transport_health.refresh_kuaishou_transport() is None
    start.assert_not_called();probe.assert_not_called();engine.dispose()
