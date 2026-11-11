from pathlib import PurePosixPath, PureWindowsPath

import pytest

from services.factory_filenames import safe_filename


def test_chinese_title_punctuation_and_internal_spaces_are_preserved():
    title = "AI画面逼真，就算懂物理了吗？  DLSS 5：谁说了算"
    for suffix in (".mp4", ".zip", ".txt"):
        assert safe_filename(title, suffix) == title + suffix


def test_path_and_control_characters_cannot_escape_the_delivery_directory():
    name = safe_filename(' ../..\\中文:标题?*<测试>|"/\x00\n\t\x7f\u202e . ', ".mp4")
    assert name == "中文标题测试.mp4"
    assert PurePosixPath(name).name == PureWindowsPath(name).name == name
    assert not name.startswith((".", " "))
    assert safe_filename("NUL", ".txt") == "_NUL.txt"
    assert safe_filename(" . 保留  内部空格 . ", ".mp4") == "保留  内部空格.mp4"


def test_long_unicode_title_is_truncated_without_broken_characters_or_random_ids():
    title = "中文标题🎥" * 80
    name = safe_filename(title, ".mp4")
    assert len(name.encode("utf-8")) <= 240 and name.endswith(".mp4")
    assert title.startswith(name[:-4])
    assert name == safe_filename(title, ".mp4")


@pytest.mark.parametrize("title", [None, "", " \t ", "../\\:*?<>|\x00"])
def test_empty_or_entirely_unsafe_title_is_rejected(title):
    with pytest.raises(ValueError):
        safe_filename(title, ".mp4")


def test_suffix_cannot_supply_a_path():
    with pytest.raises(ValueError, match="后缀"):
        safe_filename("中文标题", ".mp4/../../escape")
