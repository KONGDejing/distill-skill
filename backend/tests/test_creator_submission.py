from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.creator_adapters.submission import SubmissionWatch


def observe(host, data, path, *, method="POST", response_host=None):
    adapter = SubmissionWatch()
    adapter.portal_host = host
    adapter.page = Mock()
    adapter.start_submission_watch()
    response = SimpleNamespace(url="https://" + (response_host or host) + path,
                               request=SimpleNamespace(method=method), status=200, json=lambda: data)
    adapter._observe_submission(response)
    return adapter


@pytest.mark.parametrize("host,data,path", [
    ("creator.douyin.com", {"status_code": 0, "data": {"aweme": {"aweme_id": "123"}}}, "/web/api/media/aweme/create/"),
    ("cp.kuaishou.com", {"result": 1, "data": {"photoId": "123"}}, "/rest/cp/works/v2/video/pc/submit"),
])
def test_receipt_requires_success_and_actual_work_identifier(host, data, path):
    assert observe(host, data, path)._submission_receipt["video_id"] == "123"
    assert observe(host, data, path, method="GET")._submission_receipt is None
    assert observe(host, data, path, response_host="evil.example")._submission_receipt is None
    assert observe(host, data, "/draft/create")._submission_receipt is None


@pytest.mark.parametrize("data", [{"result": True, "photoId": "123"}, {"result": 1}, {"result": 0, "photoId": "123"}])
def test_malformed_or_rejected_response_is_never_success(data):
    adapter = observe("cp.kuaishou.com", data, "/rest/cp/works/v2/video/pc/submit")
    assert adapter._submission_receipt is None


def test_business_rejection_is_reported_without_automatic_retry():
    adapter = observe("cp.kuaishou.com", {"result": 8}, "/rest/cp/works/v2/video/pc/submit")
    with pytest.raises(ValueError, match="错误码 8"):
        adapter.wait_for_submission()
    adapter.page.click.assert_not_called()


def test_observed_kuaishou_acceptance_is_valid_without_premature_public_claim():
    from services.creator_connector import _valid_evidence
    receipt = observe("cp.kuaishou.com", {"result": 1, "data": {}}, "/rest/cp/works/v2/video/pc/submit")._submission_receipt
    assert receipt["type"] == "api_acceptance" and _valid_evidence("kuaishou", receipt)
    assert not _valid_evidence("douyin", receipt)
    assert not _valid_evidence("kuaishou", {**receipt, "code": True})
    assert not _valid_evidence("kuaishou", {**receipt, "url": "https://cp.kuaishou.com/draft/create"})
