"""One original MP4 per employee deliverable, with both manual-platform copies."""
import json
import os
from pathlib import Path
import zipfile

from services.factory_filenames import safe_filename
from services.factory_handoff import handoff_files


def save_original_archive(content, packages, media, directory, route):
    manual = {p['platform']: p for p in packages if p['platform'] in ('xiaohongshu', 'kuaishou')}
    if set(manual) != {'xiaohongshu', 'kuaishou'}:
        raise ValueError('六条原创均须提供小红书和快手完整人工发布文案')
    master = manual['xiaohongshu']; directory = Path(directory)
    target = directory / safe_filename(content['title'] + '—完整交付包', '.zip')
    temporary = target.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.write(master['video_path'], safe_filename(content['title'], '.mp4'))
        archive.write(master['cover_path'], '封面.png')
        archive.writestr('完整口播.txt', content['script'])
        for platform, package in manual.items():
            name = '小红书' if platform == 'xiaohongshu' else '快手'
            handoff = package['manual_handoff']
            archive.writestr(name + '/发布标题.txt', handoff['title'] + '\n')
            archive.writestr(name + '/正文描述.txt', handoff['description'] + '\n')
            for filename, text in handoff_files(handoff).items():
                archive.writestr(name + '/' + filename, text)
        selected = route.get('douyin_selected') is True
        archive.writestr('抖音排期说明.txt', ('交付时本条为抖音预选，最终选择与实际提交状态请在工厂查看；勿重复上传。' if selected
                                              else '交付时本条未预选抖音；最终选择以工厂为准，完整保存供本人手动选用。') + '\n')
        archive.writestr('交付记录.json', json.dumps({'policy': route['policy'], 'editor_id': route['editor_id'],
            'day': route['day'], 'douyin_selected': selected, 'manual_platforms': ['xiaohongshu', 'kuaishou'],
            'sources': content.get('sources', []), 'note': '交付包就绪不代表任何平台已提交或公开发布。'}, ensure_ascii=False, indent=2))
    os.replace(temporary, target)
    return {'archive_path': str(target), 'manual_master_path': master['video_path'],
            'manual_master_cover_path': master['cover_path'], 'archive_policy': route['policy']}
