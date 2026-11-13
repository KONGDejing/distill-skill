from copy import deepcopy
from hashlib import sha256

import pypdfium2 as pdfium
import pytest

from services.factory_documents import make_reading_excerpts, merge_original_pdfs
from services.factory_papers import validate_paper_scenes
from test_factory_papers import announcement_evidence


def source():
    return {'id': 'release', 'title': 'Original release',
            'url': 'https://www.anthropic.com/news/example',
            'text': 'The same underlying model. Input price $8. Output price $40.'}


@pytest.mark.parametrize('bad', ['fabrication', 'duplicate', 'table'])
def test_reading_excerpts_reject_unsupported_text_before_rendering(tmp_path, bad):
    record = source()
    excerpts = [{'source_id': record['id'], 'quote': 'same underlying model'}]
    if bad == 'fabrication':
        excerpts[0]['quote'] = 'A fabricated breakthrough'
    elif bad == 'duplicate':
        excerpts.append(deepcopy(excerpts[0]))
    else:
        excerpts[0]['table'] = {'rows': [['Input', '$999']]}
    with pytest.raises(ValueError):
        make_reading_excerpts([record], excerpts, tmp_path / 'invalid.pdf', 'Invalid')
    assert not (tmp_path / 'invalid.pdf').exists()


def test_reading_excerpts_are_attributed_layout_and_preserve_quote(tmp_path):
    record = source()
    result = make_reading_excerpts([record], [{'source_id': record['id'],
        'quote': 'same underlying model', 'heading': '两个名称',
        'table': {'rows': [['Input', '$8'], ['Output', '$40']]}}], tmp_path / 'reading.pdf', 'Reading')
    assert result['kind'] == 'official_document'
    assert 'not_official_pdf_or_screenshot' in result['preparation_method']
    page = result['pages'][0]
    assert page['original_quote'] == 'same underlying model'
    assert 'same underlying model' in page['text']
    assert '不是官网截图' in page['text']
    assert page['source_url'] == record['url']
    assert page['source_text_sha256'] == sha256(record['text'].encode()).hexdigest()


def test_merge_keeps_original_page_order_and_per_page_sources(tmp_path):
    records = []
    for index in range(2):
        path = tmp_path / f'original-{index}.pdf'
        with pdfium.PdfDocument.new() as pdf:
            pdf.new_page(400 + index, 600)
            pdf.save(path)
        records.append({'id': f'original-{index}', 'title': f'Original {index}',
                        'url': f'https://nvidianews.nvidia.com/news/example-{index}', 'path': str(path)})
    result = merge_original_pdfs(records, tmp_path / 'merged.pdf', 'Originals')
    assert result['kind'] == 'official_document'
    assert [p['source_url'] for p in result['pages']] == [r['url'] for r in records]
    assert [p['original_page'] for p in result['pages']] == [1, 1]
    for page, record in zip(result['pages'], records):
        assert page['original_sha256'] == sha256((tmp_path / ('original-' + record['id'][-1] + '.pdf')).read_bytes()).hexdigest()
    with pdfium.PdfDocument(result['path']) as pdf:
        assert [p.get_width() for p in pdf] == [400, 401]


def test_official_documents_keep_announcement_evidence_guards():
    scenes, assets, sources = announcement_evidence()
    for asset in assets[:3]:
        asset['document_kind'] = 'official_document'
        asset['source_url'] = sources[0]['url']
    result = validate_paper_scenes(scenes, assets, sources)
    assert result['document_kind'] == 'official_document'
    assert result['source_urls']
    sources[0]['text'] = 'This source never said that.'
    with pytest.raises(ValueError, match='原文'):
        validate_paper_scenes(scenes, assets, sources)
