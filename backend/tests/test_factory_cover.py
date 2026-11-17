import pytest
from services.factory_cover import cover_layout
from services.factory_media import _font


@pytest.mark.parametrize('text', ['奖励为何分两本账\n到底怎样才算清楚', '机器人怎样找示范\n画面像还得步骤对', 'DLSS 5拿奖了\n画面谁说了算？'])
def test_explicit_cover_phrases_stay_whole_and_fit(text):
    size, lines = cover_layout(text)
    assert lines == text.splitlines()
    assert all(_font(size, bold=True).getlength(line) <= 880 for line in lines)
    assert all(len(line) > 1 for line in lines)


def test_douyin_headline_keeps_listing_clause_whole_instead_of_splitting_shang_shi():
    from services.factory_cover import frame_title_layout
    size,lines=frame_title_layout('Claude母公司筹备上市，模型之外看什么','douyin')
    assert lines==['Claude母公司筹备上市，','模型之外看什么']
    assert 48 <= size <= 100
    assert all(_font(size,bold=True).getlength(line)<=800 for line in lines)
