from __future__ import annotations

from pathlib import Path
import sys
import tempfile

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_md_extractor.converter import ConversionOptions, convert_pdf_to_markdown


def build_sample_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 72), "Sample Academic Paper", fontsize=18)
    page.insert_text((72, 120), "Abstract", fontsize=14)
    page.insert_text(
        (72, 150),
        "This paper demonstrates native text extraction into Markdown.",
        fontsize=11,
    )
    page.insert_text((72, 200), "E = mc^2", fontsize=12)

    x0, y0 = 72, 250
    widths = [130, 100]
    heights = [28, 28, 28]
    x1 = x0 + sum(widths)
    y1 = y0 + sum(heights)
    for y in [y0, y0 + heights[0], y0 + heights[0] + heights[1], y1]:
        page.draw_line((x0, y), (x1, y), color=(0, 0, 0), width=0.8)
    for x in [x0, x0 + widths[0], x1]:
        page.draw_line((x, y0), (x, y1), color=(0, 0, 0), width=0.8)
    page.insert_text((82, 268), "Method", fontsize=10)
    page.insert_text((212, 268), "Score", fontsize=10)
    page.insert_text((82, 296), "Baseline", fontsize=10)
    page.insert_text((212, 296), "0.81", fontsize=10)
    page.insert_text((82, 324), "Proposed", fontsize=10)
    page.insert_text((212, 324), "0.92", fontsize=10)
    document.save(path)
    document.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        pdf = workspace / "sample.pdf"
        out = workspace / "out"
        build_sample_pdf(pdf)
        result = convert_pdf_to_markdown(
            pdf,
            out,
            ConversionOptions(extract_images=True, extract_tables=True, ocr_scanned_pages=False),
        )
        markdown = result.markdown_path.read_text(encoding="utf-8")
        assert "Sample Academic Paper" in markdown
        assert "Abstract" in markdown
        assert result.page_count == 1
        assert result.table_count == 1
        assert "| Method | Score |" in markdown
        print(result.markdown_path)
        print(f"elapsed={result.elapsed_seconds:.3f}s pages={result.page_count}")


if __name__ == "__main__":
    main()


def test_smoke_convert_main() -> None:
    main()
