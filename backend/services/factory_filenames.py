"""Portable delivery names derived from approved titles, without identifiers."""
import re
import unicodedata


MAX_FILENAME_BYTES = 240
_INVALID_ASCII = frozenset('<>:"/\\|?*')
_WINDOWS_DEVICE = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$", re.IGNORECASE)


def safe_filename(title, suffix):
    """Preserve title punctuation/spaces while returning a single safe filename.

    The caller supplies a fixed extension such as ``.mp4``, ``.zip`` or ``.txt``.
    Limit UTF-8 bytes, not just characters, so long Chinese titles remain usable
    on filesystems with a 255-byte component limit.
    """
    if not isinstance(title, str) or not title.strip():
        raise ValueError("文件标题不能为空")
    if not isinstance(suffix, str) or not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix):
        raise ValueError("文件后缀必须是固定扩展名，例如 .mp4、.zip 或 .txt")
    cleaned = "".join(char for char in title if char not in _INVALID_ASCII
                      and unicodedata.category(char) not in {"Cc", "Cf", "Cs"})
    cleaned = re.sub(r"^[.\s]+|[.\s]+$", "", cleaned)
    if not cleaned:
        raise ValueError("标题去除非法文件名字符后为空")
    if _WINDOWS_DEVICE.fullmatch(cleaned.split(".", 1)[0].rstrip()):
        cleaned = "_" + cleaned
    remaining, prefix = MAX_FILENAME_BYTES - len(suffix.encode("utf-8")), []
    for char in cleaned:
        length = len(char.encode("utf-8"))
        if length > remaining:
            break
        prefix.append(char)
        remaining -= length
    stem = re.sub(r"[.\s]+$", "", "".join(prefix))
    if not stem:
        raise ValueError("有效文件标题为空")
    return stem + suffix
