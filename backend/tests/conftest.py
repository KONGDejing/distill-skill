"""Keep simulated publication locks separate from the live factory's locks."""
import pytest


@pytest.fixture(autouse=True)
def isolated_publication_locks(tmp_path, monkeypatch):
    from services import factory_publishing
    from services import factory_transport_health
    monkeypatch.setattr(factory_publishing, 'LOCK_DIRECTORY', tmp_path / 'publication-locks')
    monkeypatch.setattr(factory_transport_health, 'STATUS_FILE', tmp_path / 'transport.json')
