import pytest
from services.reference_wechat import preview_metadata, wechat_share_url


def test_official_caption_is_never_promoted_to_video_or_continuous_account_access():
    result = preview_metadata({'errCode':0,'data':{'authorInfo':{'nickname':'测试视频号'},
        'feedInfo':{'description':'真实预览文案','createtime':1700000000}}},'测试视频号','https://weixin.qq.com/sph/public')
    assert result['metadata_available']
    assert not result['video_available'] and not result['account_feed_available']
    assert result['reference_only'] and 'transcript' not in result


def test_same_title_from_wrong_author_cannot_be_imported():
    with pytest.raises(ValueError, match='作者'):
        preview_metadata({'errCode':0,'data':{'authorInfo':{'nickname':'冒名账号'},
            'feedInfo':{'description':'文案','createtime':1700000000}}},'测试视频号','https://weixin.qq.com/sph/public')


@pytest.mark.parametrize('url', ['https://weixin.qq.com.evil.example/sph/public',
    'https://channels.weixin.qq.com/finder-preview/pages/sph', 'https://mp.weixin.qq.com/s/public'])
def test_preview_only_accepts_official_channels_share_urls(url):
    with pytest.raises(ValueError): wechat_share_url(url)
