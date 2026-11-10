import pytest
from services.factory_analytics import normalize


def test_douyin_never_uses_sum_of_daily_follower_stock_as_total_or_conversion():
    data = {k: {"status_code": 0, "current_count": v} for k, v in
            {"fans": "700", "play": "100", "new_fans": "3", "cancel_fans": "1"}.items()}
    result = normalize("douyin", {"overview": {"status_code": 0, "data": data},
        "profile": {"status_code": 0, "user_profile": {"follower_count": 101}}},
        "统计周期：2026.11.03-2026.11.09（每天12点更新）")
    assert result["metrics"]["followers"] == 101
    assert result["metrics"]["net_followers"] == 2
    assert result["metrics"]["follow_conversion_rate"] is None
    assert result["period"]["end"] == "2026-11-09"


def test_kuaishou_ratio_converts_to_percentage_and_missing_is_not_zero():
    result = normalize("kuaishou", {"overview": {"result": 1, "data": {"basicData": [
        {"tab": "COMPLETE_RATIO", "sumCount": .125}, {"tab": "PLAY", "sumCount": 0,
            "trendData": [{"date": "20261109", "count": 0}]}]}}})
    assert result["metrics"]["completion_rate"] == 12.5
    assert result["metrics"]["views"] == 0
    assert result["metrics"]["net_followers"] is None


def test_xhs_preserves_official_conversion_instead_of_dividing_aggregate_counts():
    result = normalize("xiaohongshu", {"overview": {"success": True, "data": {"seven": {
        "view_count": 900, "net_rise_fans_count": 2, "home_view_count": 40,
        "home_conversion_rise_fans_rate": 2.8, "video_full_view_rate": 7}}}})
    assert result["metrics"]["follow_conversion_rate"] == 2.8
    assert result["metrics"]["followers"] is None


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu", "kuaishou", "bilibili"])
def test_missing_or_rejected_official_data_is_not_reported_as_zero(platform):
    with pytest.raises(ValueError):
        normalize(platform, {})
