"""Owner attribution and official save permission are gates, not UI promises."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from playwright.sync_api import sync_playwright

from services import factory_branding as branding
from services.creator_adapters.douyin import Adapter


@pytest.fixture
def verified_brand(tmp_path, monkeypatch):
    path = tmp_path / 'verified-owner.json'
    path.write_text(json.dumps({'platform': 'douyin', 'display_name': '测试科技作者',
        'handle': 'test_science', 'verification_source': 'official_creator_home'}))
    monkeypatch.setattr(branding, 'BRANDING_FILE', path)
    return branding.branding_for('douyin')


def test_owner_identity_requires_verified_private_config(tmp_path, monkeypatch):
    assert branding.branding_for('douyin') == {}
    with pytest.raises(ValueError, match='官方后台核验'):
        branding.require_owner_attribution({})
    path = tmp_path / 'unverified.json'
    path.write_text(json.dumps({'platform': 'douyin', 'display_name': '别人的账号', 'handle': 'other'}))
    monkeypatch.setattr(branding, 'BRANDING_FILE', path)
    assert branding.branding_for('douyin') == {}


def test_attribution_evidence_binds_account_and_exact_final_bytes(verified_brand, tmp_path):
    video = tmp_path / 'final.mp4'; video.write_bytes(b'approved final video')
    quality = {'source_attribution_integrated': True, 'branding_signature': verified_brand['signature'],
               'delivery_sha256': branding.file_sha256(video)}
    package = {'video_path': str(video), 'delivery_quality': quality}
    branding.require_owner_attribution(package)
    video.write_bytes(b'replaced video without signature')
    with pytest.raises(ValueError, match='成片已变化'):
        branding.require_owner_attribution(package)
    package['delivery_quality'] = {**quality, 'branding_signature': 'another account'}
    with pytest.raises(ValueError, match='当前账号来源署名'):
        branding.require_owner_attribution(package)
    assert branding.branding_for('xiaohongshu') == {}


def test_permission_selects_observed_label_and_rechecks_controlled_state():
    # Real browser DOM: native checkbox checked deliberately disagrees with the
    # observed controlled label. An unrelated 不允许 must never be clicked.
    html = '''<label id="unrelated">不允许</label><div class="download-content-observed">
      <label data-checked="true"><input type="checkbox" value="1">允许</label>
      <label data-checked="false"><input type="checkbox" value="0" checked>不允许</label>
    </div><script>
    document.querySelectorAll('.download-content-observed label').forEach(label => {
      label.onclick = () => document.querySelectorAll('.download-content-observed label').forEach(
        other => other.dataset.checked = String(other === label));
    });</script>'''
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
        try:
            page = browser.new_page(); page.set_content(html)
            client = Adapter(SimpleNamespace(pages=[page]))
            payload = {'item_id': 'test-film', 'allow_download': False}
            evidence = client.set_download_permission(payload)
            assert evidence['verification_method'] == 'official_editor_save_permission'
            client.require_download_permission(payload)
            assert page.locator('#unrelated').get_attribute('data-checked') is None
            with pytest.raises(ValueError, match='尚未在官方表单核验'):
                client.require_download_permission({**payload, 'item_id': 'another-film'})
            page.locator('.download-content-observed label').first.click()
            with pytest.raises(ValueError, match='发生变化'):
                client.require_download_permission(payload)
            page.locator('.download-content-observed').evaluate('el => el.remove()')
            with pytest.raises(ValueError, match='无法唯一定位'):
                client.set_download_permission(payload)
        finally:
            browser.close()


def test_publish_cannot_get_submit_control_without_verified_download_permission():
    page = Mock(); page.url = 'https://creator.douyin.com/creator-micro/content/post/video'
    page.get_by_placeholder.return_value.input_value.return_value = '定稿标题'
    client = Adapter(SimpleNamespace(pages=[page]))
    with patch.object(client, 'visible', return_value=Mock()):
        with pytest.raises(ValueError, match='禁止下载尚未'):
            client.publication_control({}, {'title': '定稿标题', 'ai_generated': True, 'allow_download': False})
    page.get_by_role.assert_not_called()


@pytest.mark.parametrize('paper_style', [False, True])
def test_whole_video_has_owner_and_readable_topic_without_changing_voice(verified_brand, tmp_path, monkeypatch, paper_style):
    import subprocess
    import numpy as np
    from PIL import Image
    from services import factory_delivery_video as delivery
    from services.factory_cover import render_frame_cover
    monkeypatch.setattr(delivery, 'ensure_memory_available', lambda: None)
    source, cover, target = [tmp_path / name for name in ['source.mp4', 'cover.png', 'final.mp4']]
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=270x480:r=24:d=6',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6', '-c:v', 'libx264', '-threads', '1',
        '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(source)], check=True)
    design = render_frame_cover(source, '机器人的真实新进展', cover, 'douyin')
    quality = delivery.render_delivery_video(source, cover, target, opening_mode='first_frame_title',
        title_overlay=design['overlay_path'], persistent_title=not paper_style, branding=verified_brand,
        attribution_y=1760 if paper_style else 700)
    assert quality['audio_preserved'] and quality['source_attribution_integrated']
    assert quality['persistent_title'] is not paper_style
    assert quality['title_seconds'] == (2.4 if paper_style else 6)
    branding.require_owner_attribution({'video_path': str(target), 'delivery_quality': quality})
    def frame(path, at):
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(path),
            '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
        return np.frombuffer(raw, np.uint8).reshape(480, 270, 3).astype(float)
    assert np.array_equal(np.asarray(Image.open(cover)), frame(target, 0))
    for at in (0, 3, 5):
        before, after = frame(source, at), frame(target, at)
        title_difference = abs(before[90:162] - after[90:162]).mean()
        assert title_difference < 5 if paper_style and at >= 3 else title_difference > 5
        y = 440 if paper_style else 175
        assert abs(before[y:y+25, 15:160] - after[y:y+25, 15:160]).mean() > 1
        assert abs(before[220:400] - after[220:400]).mean() < 5
