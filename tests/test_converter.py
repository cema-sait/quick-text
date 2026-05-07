from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import fitz
from PIL import Image
import pytest

from paper_md_extractor import converter
from paper_md_extractor.converter import (
    ConversionOptions,
    convert_pdf_to_markdown,
    _clean_markdown,
    _extract_tables,
    _format_metadata,
    _count_markdown_tables,
    _format_text,
    _join_pymupdf4llm_chunks,
    _relocate_layout_images,
    _rewrite_markdown_image_links,
    _safe_artifact_filename,
    _safe_artifact_stem,
    _snapshot_files,
    _unique_path,
    _looks_like_equation,
    _equation_block_rect,
    _line_rect,
    _style_line,
    _table_rect,
    _table_to_markdown,
)


def _png_bytes(color: tuple[int, int, int] = (200, 20, 20)) -> bytes:
    image = Image.new('RGB', (12, 10), color)
    stream = BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


def build_rich_pdf(path: Path) -> None:
    document = fitz.open()
    document.set_metadata({
        'title': 'Robust Extraction',
        'author': 'A. Researcher',
        'subject': 'Testing',
        'keywords': 'pdf, markdown',
    })
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 72), 'Robust Extraction', fontsize=18)
    page.insert_text((72, 120), 'Abstract', fontsize=14)
    page.insert_text((72, 150), 'This paper demonstrates native PDF extraction.', fontsize=11)
    page.insert_text((72, 190), 'E = mc^2', fontsize=12)

    x0, y0 = 72, 250
    widths = [130, 100]
    heights = [28, 28, 28]
    x1 = x0 + sum(widths)
    y1 = y0 + sum(heights)
    for y in [y0, y0 + heights[0], y0 + heights[0] + heights[1], y1]:
        page.draw_line((x0, y), (x1, y), color=(0, 0, 0), width=0.8)
    for x in [x0, x0 + widths[0], x1]:
        page.draw_line((x, y0), (x, y1), color=(0, 0, 0), width=0.8)
    page.insert_text((82, 268), 'Method', fontsize=10)
    page.insert_text((212, 268), 'Score', fontsize=10)
    page.insert_text((82, 296), 'Baseline', fontsize=10)
    page.insert_text((212, 296), '0.81', fontsize=10)
    page.insert_text((82, 324), 'Proposed', fontsize=10)
    page.insert_text((212, 324), '0.92', fontsize=10)

    page.insert_image(fitz.Rect(72, 380, 132, 430), stream=_png_bytes())
    document.save(path)
    document.close()


def test_convert_pdf_extracts_metadata_text_equations_tables_images_and_progress(tmp_path: Path) -> None:
    pdf = tmp_path / 'paper.pdf'
    build_rich_pdf(pdf)
    progress: list[tuple[str, int, int]] = []

    result = convert_pdf_to_markdown(
        pdf,
        tmp_path / 'out',
        ConversionOptions(extract_images=True, extract_tables=True, ocr_scanned_pages=False),
        lambda message, page, total: progress.append((message, page, total)),
    )

    markdown = result.markdown_path.read_text(encoding='utf-8')
    assert result.page_count == 1
    assert result.table_count == 1
    assert result.image_count == 1
    assert result.ocr_page_count == 0
    assert result.equation_image_count == 1
    assert result.elapsed_seconds >= 0
    assert progress == [('Reading page 1 of 1', 1, 1)]
    assert '- **Title**: Robust Extraction' in markdown
    assert '- **Author**: A. Researcher' in markdown
    assert '## Page 1' in markdown
    assert 'This paper demonstrates native PDF extraction.' in markdown
    assert '![Equation 1.1](images/paper-p001-equation-01.png)' in markdown
    assert '$$' not in markdown
    assert '| Method | Score |' in markdown
    assert '| Proposed | 0.92 |' in markdown
    assert '![Extracted image](images/paper-p001-01.png)' in markdown
    assert (result.images_dir / 'paper-p001-01.png').exists()
    assert (result.images_dir / 'paper-p001-equation-01.png').exists()


def test_convert_pdf_without_optional_assets_skips_tables_and_images_dir(tmp_path: Path) -> None:
    pdf = tmp_path / 'paper.pdf'
    build_rich_pdf(pdf)

    result = convert_pdf_to_markdown(
        pdf,
        tmp_path / 'out',
        ConversionOptions(
            extract_images=False,
            extract_tables=False,
            ocr_scanned_pages=False,
            extract_equations_as_images=False,
        ),
    )

    markdown = result.markdown_path.read_text(encoding='utf-8')
    assert result.table_count == 0
    assert result.image_count == 0
    assert not result.images_dir.exists()
    assert '![Extracted image]' not in markdown
    assert '### Table' not in markdown



def test_convert_pdf_with_pymupdf4llm_uses_layout_adapter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf = tmp_path / 'Advert AD-4-226-2026 - Senior Research Fellow & AI Lead - CEMA!.pdf'
    build_rich_pdf(pdf)
    calls: list[dict[str, object]] = []

    def fake_to_markdown(doc, **kwargs):
        calls.append({'doc': doc, **kwargs})
        image_dir = Path(kwargs['image_path'])
        image_dir.mkdir(parents=True, exist_ok=True)
        written = image_dir / f"{kwargs['filename']}-0001-00.png"
        written.write_bytes(_png_bytes())
        return f"# Layout Output\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n![Figure]({written})"

    monkeypatch.setattr(converter, '_load_pymupdf4llm', lambda: SimpleNamespace(to_markdown=fake_to_markdown))
    progress: list[tuple[str, int, int]] = []

    result = convert_pdf_to_markdown(
        pdf,
        tmp_path / 'out',
        ConversionOptions(extraction_engine='pymupdf4llm', extract_images=True, ocr_scanned_pages=True),
        lambda message, page, total: progress.append((message, page, total)),
    )

    markdown = result.markdown_path.read_text(encoding='utf-8')
    assert len(calls) == 1
    call = calls[0]
    assert Path(call['doc']).name == 'Advert-AD-4-226-2026-Senior-Research-Fellow-AI-Lead-CEMA.pdf'
    assert Path(call['image_path']).name == 'images'
    assert Path(call['image_path']).parent.name.startswith('quick-text-layout-')
    assert call['write_images'] is True
    assert call['filename'] == 'Advert-AD-4-226-2026-Senior-Research-Fellow-AI-Lead-CEMA'
    assert call['embed_images'] is False
    assert call['page_chunks'] is False
    assert call['use_ocr'] is True
    assert call['force_ocr'] is False
    assert call['show_progress'] is False
    assert result.page_count == 1
    assert result.image_count == 1
    assert result.table_count == 1
    assert result.ocr_page_count == 0
    assert result.equation_image_count == 0
    assert progress == [('Analyzing layout with PyMuPDF4LLM', 1, 1), ('Writing Markdown', 1, 1)]
    assert '![Figure](images/Advert-AD-4-226-2026-Senior-Research-Fellow-AI-Lead-CEMA-0001-00.png)' in markdown



def test_pymupdf4llm_adapter_wraps_layout_engine_filesystem_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf = tmp_path / 'problem & name.pdf'
    build_rich_pdf(pdf)

    def fail_to_markdown(*args, **kwargs):
        raise OSError('cannot open generated image path')

    monkeypatch.setattr(converter, '_load_pymupdf4llm', lambda: SimpleNamespace(to_markdown=fail_to_markdown))
    with pytest.raises(RuntimeError, match='layout extraction failed') as exc_info:
        convert_pdf_to_markdown(pdf, tmp_path / 'out', ConversionOptions(extraction_engine='pymupdf4llm'))
    assert isinstance(exc_info.value.__cause__, OSError)


def test_safe_artifact_stem_removes_problematic_characters_and_limits_length() -> None:
    assert _safe_artifact_stem('Advert AD-4-226-2026 - Senior Research Fellow & AI Lead - CEMA!') == (
        'Advert-AD-4-226-2026-Senior-Research-Fellow-AI-Lead-CEMA'
    )
    assert _safe_artifact_stem('λλλ &&&') == 'document'
    assert _safe_artifact_stem('a' * 120) == 'a' * 80

def test_pymupdf4llm_adapter_handles_missing_dependency(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf = tmp_path / 'layout.pdf'
    build_rich_pdf(pdf)

    def missing():
        raise RuntimeError('PyMuPDF4LLM layout extraction is optional.')

    monkeypatch.setattr(converter, '_load_pymupdf4llm', missing)
    with pytest.raises(RuntimeError, match='optional'):
        convert_pdf_to_markdown(pdf, tmp_path / 'out', ConversionOptions(extraction_engine='pymupdf4llm'))


def test_pymupdf4llm_adapter_rejects_unknown_engine(tmp_path: Path) -> None:
    pdf = tmp_path / 'layout.pdf'
    build_rich_pdf(pdf)
    options = ConversionOptions(extraction_engine='unknown')  # type: ignore[arg-type]
    with pytest.raises(ValueError, match='Unsupported extraction engine'):
        convert_pdf_to_markdown(pdf, tmp_path / 'out', options)



def test_layout_image_relocation_sanitizes_links_and_handles_collisions(tmp_path: Path) -> None:
    destination = tmp_path / 'out'
    images_dir = destination / 'images'
    source_dir = tmp_path / 'layout images'
    source_dir.mkdir()
    generated = source_dir / 'Bad Name & Formula?.png'
    generated.write_bytes(_png_bytes())
    images_dir.mkdir(parents=True)
    (images_dir / 'Bad-Name-Formula.png').write_bytes(b'existing')

    markdown = _relocate_layout_images(
        f'![Abs]({generated}) ![Name]({generated.name}) ![Remote](https://example.com/a.png) ![Missing](missing.png)',
        source_dir,
        images_dir,
        destination,
    )

    assert '![Abs](images/Bad-Name-Formula-2.png)' in markdown
    assert '![Name](images/Bad-Name-Formula-2.png)' in markdown
    assert '![Remote](https://example.com/a.png)' in markdown
    assert '![Missing](missing.png)' in markdown
    assert (images_dir / 'Bad-Name-Formula-2.png').read_bytes() == generated.read_bytes()


def test_layout_image_relocation_passthroughs_empty_sources(tmp_path: Path) -> None:
    assert _relocate_layout_images('![A](a.png)', tmp_path / 'missing', tmp_path / 'images', tmp_path) == '![A](a.png)'
    empty = tmp_path / 'empty'
    empty.mkdir()
    assert _relocate_layout_images('plain', empty, tmp_path / 'images', tmp_path) == 'plain'


def test_artifact_filename_snapshot_and_unique_path_helpers(tmp_path: Path) -> None:
    assert _safe_artifact_filename('Bad Name & Formula?.PNG') == 'Bad-Name-Formula.png'
    assert _safe_artifact_filename('!!!') == 'document.bin'
    target = tmp_path / 'artifact.png'
    assert _unique_path(target) == target
    target.write_bytes(b'one')
    assert _unique_path(target) == tmp_path / 'artifact-2.png'
    assert _snapshot_files(tmp_path / 'missing') == set()
    assert target.resolve() in _snapshot_files(tmp_path)


def test_pymupdf4llm_markdown_helpers_rewrite_chunks_and_tables(tmp_path: Path) -> None:
    image = tmp_path / 'out' / 'images' / 'figure.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(_png_bytes())
    markdown = _rewrite_markdown_image_links(f'![Fig]({image}) ![Remote](https://example.com/a.png)', tmp_path / 'out')
    assert f'![Fig](images/figure.png)' in markdown
    assert '![Remote](https://example.com/a.png)' in markdown
    assert _count_markdown_tables('| A | B |\n| --- | :---: |\n| 1 | 2 |') == 1
    assert _join_pymupdf4llm_chunks([{'text': 'Alpha'}, {'text': ''}, {'text': 'Beta'}]) == '## Page 1\n\nAlpha\n\n## Page 3\n\nBeta'

def test_convert_pdf_uses_ocr_when_native_text_is_sparse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf = tmp_path / 'scan.pdf'
    document = fitz.open()
    document.new_page(width=200, height=200)
    document.save(pdf)
    document.close()
    monkeypatch.setattr(converter, '_ocr_page', lambda page, zoom: 'OCR title\nOCR body')

    result = convert_pdf_to_markdown(
        pdf,
        tmp_path / 'out',
        ConversionOptions(
            ocr_scanned_pages=True,
            extract_images=False,
            extract_tables=False,
            extract_equations_as_images=False,
        ),
    )

    markdown = result.markdown_path.read_text(encoding='utf-8')
    assert result.ocr_page_count == 1
    assert 'OCR title OCR body' in markdown


def test_convert_pdf_rejects_missing_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        convert_pdf_to_markdown(tmp_path / 'missing.pdf', tmp_path / 'out')


def test_format_metadata_ignores_empty_values() -> None:
    assert _format_metadata({'title': '  Title  ', 'author': '', 'subject': 'Subject'}) == (
        '## Metadata\n- **Title**: Title\n- **Subject**: Subject'
    )
    assert _format_metadata({}) == ''


def test_table_to_markdown_normalizes_escapes_and_filters_blank_rows() -> None:
    markdown = _table_to_markdown([
        ['Name', 'Value'],
        ['Alpha', '1'],
        [None, ''],
        ['Beta\nCell', '<tag>', 'extra'],
    ])
    assert markdown == '\n'.join([
        '| Name | Value |  |',
        '| --- | --- | --- |',
        '| Alpha | 1 |  |',
        '| Beta Cell | &lt;tag&gt; | extra |',
    ])
    assert _table_to_markdown([[None, ''], []]) == ''


class RectTable:
    rect = fitz.Rect(1, 2, 3, 4)


class BBoxTable:
    bbox = (2, 3, 8, 9)


class BadRectCellsTable:
    rect = 'not-a-rect'
    cells = [(0, 0, 1, 1), None, (1, 1, 3, 4), 'bad-cell']


class NoGeometryTable:
    cells = []


def test_table_rect_supports_pymupdf_table_shape_variants() -> None:
    assert _table_rect(RectTable()) == fitz.Rect(1, 2, 3, 4)
    assert _table_rect(BBoxTable()) == fitz.Rect(2, 3, 8, 9)
    assert _table_rect(BadRectCellsTable()) == fitz.Rect(0, 0, 3, 4)
    assert _table_rect(NoGeometryTable()) is None
    assert _table_rect(object()) is None


class NoTableFinderPage:
    pass


class ErrorTableFinderPage:
    def find_tables(self):
        raise RuntimeError('table detection failed')


def test_extract_tables_is_defensive_for_missing_or_failing_detector() -> None:
    assert _extract_tables(NoTableFinderPage()) == []
    assert _extract_tables(ErrorTableFinderPage()) == []


def test_text_formatting_and_equation_heuristics() -> None:
    assert _looks_like_equation('E = mc^2')
    assert not _looks_like_equation('This is a normal sentence with punctuation.')
    assert _style_line('Large Heading', [{'size': 19, 'font': 'Helvetica'}]) == '## Large Heading'
    assert _style_line('Small Bold Heading', [{'size': 12, 'font': 'Helvetica-Bold'}]) == '### Small Bold Heading'
    assert _style_line('This is body text.', [{'size': 10, 'font': 'Helvetica'}]) == 'This is body text.'
    assert _format_text('Intro\ncontinues\n\n## Heading\nBody\n$$\nx = y + 1\n$$') == (
        'Intro continues\n\n## Heading\n\nBody\n\n$$\n\nx = y + 1\n\n$$'
    )
    assert _format_text('Before\n![Equation](images/equation.png)\nAfter') == (
        'Before\n\n![Equation](images/equation.png)\n\nAfter'
    )
    assert _clean_markdown('A\n\n\n\n\nB\n') == 'A\n\n\nB\n'



def test_extract_text_skips_non_text_empty_and_excluded_blocks() -> None:
    class Page:
        def get_text(self, kind, flags=0):
            return {
                'blocks': [
                    {'type': 1, 'bbox': (0, 0, 10, 10)},
                    {'type': 0, 'bbox': (0, 0, 10, 10), 'lines': [
                        {'spans': [{'text': 'Excluded text', 'size': 10, 'font': 'Helvetica'}]},
                    ]},
                    {'type': 0, 'bbox': (20, 20, 40, 40), 'lines': [
                        {'spans': [{'text': '   ', 'size': 10, 'font': 'Helvetica'}]},
                        {'spans': [{'text': 'Included', 'size': 10, 'font': 'Helvetica'}]},
                    ]},
                ]
            }

    text = converter._extract_text(Page(), [fitz.Rect(0, 0, 10, 10)])
    assert text == 'Included'


def test_image_extraction_handles_duplicate_bad_and_empty_images(tmp_path: Path) -> None:
    class Page:
        def get_images(self, full=True):
            return [(1,), (1,), (2,), (3,), (4,)]

    class Document:
        def extract_image(self, xref):
            if xref == 2:
                raise RuntimeError('bad image')
            if xref == 3:
                return {'ext': 'png'}
            return {'ext': 'png', 'image': b'png-bytes'}

    paths = converter._extract_images(Document(), Page(), tmp_path, 'paper', 2)
    assert [path.name for path in paths] == ['paper-p002-01.png', 'paper-p002-05.png']
    assert (tmp_path / 'paper-p002-01.png').read_bytes() == b'png-bytes'


def test_ocr_page_returns_text_and_handles_tesseract_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class Pixmap:
        width = 2
        height = 1
        n = 3
        samples = bytes([255, 0, 0, 0, 255, 0])

    class Page:
        def get_pixmap(self, matrix, alpha=False):
            return Pixmap()

    class Tesseract:
        def __init__(self):
            self.fail = False
        def image_to_string(self, image):
            if self.fail:
                raise RuntimeError('ocr failed')
            return 'OCR text'

    fake_tesseract = Tesseract()
    monkeypatch.setitem(__import__('sys').modules, 'pytesseract', fake_tesseract)
    assert converter._ocr_page(Page(), 1.5) == 'OCR text'
    fake_tesseract.fail = True
    assert converter._ocr_page(Page(), 1.5) == ''


def test_ocr_page_returns_empty_when_optional_dependency_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == 'pytesseract':
            raise ImportError('missing')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', blocked_import)
    assert converter._ocr_page(object(), 2.0) == ''


def test_format_text_flushes_paragraph_before_heading_and_short_equation_rejection() -> None:
    assert not _looks_like_equation('=')
    assert _format_text('Opening line\n# Heading\nNext') == 'Opening line\n\n# Heading\n\nNext'



def test_equation_text_fallback_when_image_capture_disabled(tmp_path: Path) -> None:
    pdf = tmp_path / 'paper.pdf'
    build_rich_pdf(pdf)

    result = convert_pdf_to_markdown(
        pdf,
        tmp_path / 'out',
        ConversionOptions(extract_images=False, extract_tables=False, extract_equations_as_images=False),
    )

    markdown = result.markdown_path.read_text(encoding='utf-8')
    assert result.equation_image_count == 0
    assert '$$' in markdown
    assert 'E = mc^2' in markdown
    assert not result.images_dir.exists()


def test_line_rect_uses_line_bbox_and_span_fallback() -> None:
    assert _line_rect({'bbox': (1, 2, 3, 4), 'spans': []}) == fitz.Rect(1, 2, 3, 4)
    assert _line_rect({'bbox': 'bad', 'spans': [{'bbox': (0, 0, 2, 2)}, {'bbox': (2, 2, 4, 5)}]}) == fitz.Rect(0, 0, 4, 5)
    assert _line_rect({'spans': [{'bbox': 'bad'}, {}]}) is None


def test_extract_equation_image_handles_bad_clip(tmp_path: Path) -> None:
    class Page:
        rect = fitz.Rect(0, 0, 100, 100)
        def get_pixmap(self, matrix, clip, alpha=False):
            raise RuntimeError('render failed')

    assert converter._extract_equation_image(Page(), tmp_path, 'paper', 1, 1, fitz.Rect(10, 10, 20, 20), 2.0) is None



def test_broken_multiline_equation_text_is_grouped_into_broad_crop() -> None:
    class Page:
        rect = fitz.Rect(0, 0, 600, 800)
        def get_text(self, kind, flags=0):
            return {
                'blocks': [
                    {
                        'type': 0,
                        'bbox': (92, 110, 548, 238),
                        'lines': [
                            {'bbox': (96, 112, 530, 126), 'spans': [{'text': '(2) �cij = K cij sl'}]},
                            {'bbox': (226, 128, 236, 142), 'spans': [{'text': 'j'}]},
                            {'bbox': (96, 150, 530, 164), 'spans': [{'text': '(3) Mi = log2'}]},
                            {'bbox': (160, 166, 308, 180), 'spans': [{'text': 'cij Nj cik Nk'}]},
                            {'bbox': (96, 188, 530, 202), 'spans': [{'text': '(4) Ai = 1'}]},
                            {'bbox': (146, 204, 214, 218), 'spans': [{'text': '2 log2'}]},
                            {'bbox': (238, 220, 546, 234), 'spans': [{'text': ', for ci. �= 0'}]},
                        ],
                    }
                ]
            }

    captured: list[fitz.Rect] = []
    text = converter._extract_text(Page(), [], lambda rect: captured.append(rect) or '![Equation](images/equation.png)')

    assert text == '![Equation](images/equation.png)'
    assert len(captured) == 1
    assert captured[0].x0 == pytest.approx(48)
    assert captured[0].x1 == pytest.approx(552)
    assert captured[0].y0 < 112
    assert captured[0].y1 > 234


def test_equation_block_rect_ignores_prose_blocks_and_broadens_right_edge_numbers() -> None:
    class Page:
        rect = fitz.Rect(0, 0, 600, 800)

    prose_block = {
        'type': 0,
        'bbox': (72, 72, 500, 130),
        'lines': [
            {'bbox': (72, 72, 500, 88), 'spans': [{'text': 'This sentence has x = 1 but should remain text.'}]},
            {'bbox': (72, 92, 500, 108), 'spans': [{'text': 'It is explanatory prose, not a display equation.'}]},
        ],
    }
    equation_block = {
        'type': 0,
        'bbox': (120, 180, 540, 224),
        'lines': [
            {'bbox': (120, 180, 540, 196), 'spans': [{'text': 'Ai = 1'}]},
            {'bbox': (142, 202, 530, 218), 'spans': [{'text': 'cij Nj · cik Nk (4)'}]},
        ],
    }

    assert _equation_block_rect(Page(), prose_block) is None
    rect = _equation_block_rect(Page(), equation_block)
    assert rect == fitz.Rect(48, 172, 552, 226)


def test_equation_heuristic_handles_extracted_symbol_noise_and_indices() -> None:
    assert _looks_like_equation('(2) �cij = K cij sl')
    assert _looks_like_equation('(4) Ai = 1')
    assert _looks_like_equation(', for ci. �= 0')
    assert converter._looks_like_equation_continuation('cij Nj cik Nk')
    assert not _looks_like_equation('This sentence has x = 1 but should remain text.')
