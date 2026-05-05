from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import html
import re
import time
from typing import Callable, Iterable

try:
    import fitz
except ImportError as exc:  # pragma: no cover - exercised by GUI error path
    raise RuntimeError(
        "PyMuPDF is required. Install dependencies with: python -m pip install -r requirements.txt"
    ) from exc


ProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class ConversionOptions:
    extract_images: bool = True
    extract_tables: bool = True
    ocr_scanned_pages: bool = False
    extract_equations_as_images: bool = True
    image_zoom: float = 2.0
    min_text_chars_for_ocr_skip: int = 40


@dataclass(frozen=True)
class ConversionResult:
    markdown_path: Path
    images_dir: Path
    page_count: int
    elapsed_seconds: float
    image_count: int
    table_count: int
    ocr_page_count: int
    equation_image_count: int = 0


def convert_pdf_to_markdown(
    pdf_path: str | Path,
    output_dir: str | Path,
    options: ConversionOptions | None = None,
    progress: ProgressCallback | None = None,
) -> ConversionResult:
    options = options or ConversionOptions()
    source = Path(pdf_path).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    destination.mkdir(parents=True, exist_ok=True)

    images_dir = destination / "images"
    if options.extract_images or options.extract_equations_as_images:
        images_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    image_count = 0
    table_count = 0
    ocr_page_count = 0
    equation_image_count = 0
    parts: list[str] = [f"# {source.stem}\n"]

    with fitz.open(source) as document:
        metadata = _format_metadata(document.metadata)
        if metadata:
            parts.append(metadata)

        total = document.page_count
        for page_index, page in enumerate(document, start=1):
            if progress:
                progress(f"Reading page {page_index} of {total}", page_index, total)

            page_parts: list[str] = [f"\n\n## Page {page_index}\n"]

            tables = _extract_tables(page) if options.extract_tables else []
            table_count += len(tables)
            table_rects = [rect for table in tables if (rect := _table_rect(table)) is not None]

            def write_equation_image(rect: "fitz.Rect") -> str | None:
                nonlocal equation_image_count
                next_number = equation_image_count + 1
                image_path = _extract_equation_image(
                    page, images_dir, source.stem, page_index, next_number, rect, options.image_zoom
                )
                if image_path is None:
                    return None
                equation_image_count = next_number
                rel = image_path.relative_to(destination).as_posix()
                return f"![Equation {page_index}.{next_number}]({rel})"

            text = _extract_text(
                page,
                table_rects,
                write_equation_image if options.extract_equations_as_images else None,
            )
            if options.ocr_scanned_pages and len(text.strip()) < options.min_text_chars_for_ocr_skip:
                ocr_text = _ocr_page(page, options.image_zoom)
                if ocr_text.strip():
                    text = ocr_text
                    ocr_page_count += 1

            if text.strip():
                page_parts.append(_format_text(text))

            for table_number, table in enumerate(tables, start=1):
                markdown_table = _table_to_markdown(table.extract())
                if markdown_table:
                    page_parts.append(f"\n\n### Table {page_index}.{table_number}\n\n{markdown_table}")

            if options.extract_images:
                extracted = _extract_images(document, page, images_dir, source.stem, page_index)
                image_count += len(extracted)
                for image_path in extracted:
                    rel = image_path.relative_to(destination).as_posix()
                    page_parts.append(f"\n\n![Extracted image]({rel})")

            parts.append("\n".join(part for part in page_parts if part.strip()))

    markdown_path = destination / f"{source.stem}.md"
    markdown_path.write_text(_clean_markdown("\n".join(parts)), encoding="utf-8")
    elapsed = time.perf_counter() - started
    return ConversionResult(
        markdown_path=markdown_path,
        images_dir=images_dir,
        page_count=total,
        elapsed_seconds=elapsed,
        image_count=image_count,
        table_count=table_count,
        ocr_page_count=ocr_page_count,
        equation_image_count=equation_image_count,
    )


def _format_metadata(metadata: dict[str, str]) -> str:
    rows = []
    for key in ("title", "author", "subject", "keywords"):
        value = (metadata.get(key) or "").strip()
        if value:
            rows.append(f"- **{key.title()}**: {value}")
    return "\n".join(["## Metadata", *rows]) if rows else ""


def _extract_tables(page: "fitz.Page") -> list:
    finder = getattr(page, "find_tables", None)
    if finder is None:
        return []
    try:
        return list(finder().tables)
    except Exception:
        return []


def _table_rect(table: object) -> "fitz.Rect | None":
    for attr in ("rect", "bbox"):
        value = getattr(table, attr, None)
        if value:
            try:
                return fitz.Rect(value)
            except Exception:
                pass

    cells = getattr(table, "cells", None)
    if not cells:
        return None

    rects = []
    for cell in cells:
        if not cell:
            continue
        try:
            rects.append(fitz.Rect(cell))
        except Exception:
            pass
    if not rects:
        return None

    table_bounds = fitz.Rect(rects[0])
    for rect in rects[1:]:
        table_bounds.include_rect(rect)
    return table_bounds


def _extract_text(
    page: "fitz.Page",
    excluded_rects: Iterable["fitz.Rect"],
    equation_image_writer: Callable[["fitz.Rect"], str | None] | None = None,
) -> str:
    excluded = list(excluded_rects)
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_LIGATURES).get("blocks", [])
    lines: list[str] = []

    for block in blocks:
        if block.get("type") != 0:
            continue
        rect = fitz.Rect(block["bbox"])
        if any(rect.intersects(table_rect) for table_rect in excluded):
            continue

        if equation_image_writer:
            equation_rect = _equation_block_rect(page, block)
            if equation_rect:
                equation_markdown = equation_image_writer(equation_rect)
                if equation_markdown:
                    lines.append(equation_markdown)
                    lines.append("")
                    continue

        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            lines.append(_style_line(text, line.get("spans", [])))
        if lines and lines[-1] != "":
            lines.append("")

    return "\n".join(lines).strip()


def _equation_block_rect(page: "fitz.Page", block: dict) -> "fitz.Rect | None":
    line_items = []
    for line in block.get("lines", []):
        text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
        if not text:
            continue
        rect = _line_rect(line)
        if rect:
            line_items.append((text, rect))

    if not line_items:
        return None

    candidates = [_looks_like_equation(text) for text, _ in line_items]
    if not any(candidates):
        return None

    if len(line_items) > 1 and not _equation_block_is_compact([text for text, _ in line_items], candidates):
        return None

    first = next(index for index, candidate in enumerate(candidates) if candidate)
    last = len(candidates) - 1 - next(index for index, candidate in enumerate(reversed(candidates)) if candidate)
    while first > 0 and _looks_like_equation_continuation(line_items[first - 1][0]):
        first -= 1
    while last + 1 < len(line_items) and _looks_like_equation_continuation(line_items[last + 1][0]):
        last += 1

    equation_rect = fitz.Rect(line_items[first][1])
    for _, rect in line_items[first + 1 : last + 1]:
        equation_rect.include_rect(rect)
    return _broaden_equation_rect(page, equation_rect)


def _equation_block_is_compact(texts: list[str], candidates: list[bool]) -> bool:
    candidate_count = sum(candidates)
    if candidate_count >= 2:
        return True
    if len(texts) <= 3:
        return True

    prose_words = sum(len(re.findall(r"[A-Za-z]{4,}", text)) for text in texts)
    mathish_lines = sum(_looks_like_equation_continuation(text) for text in texts)
    return prose_words <= max(4, len(texts)) and mathish_lines >= len(texts) // 2


def _looks_like_equation_continuation(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 80:
        return False
    if _looks_like_equation(stripped):
        return True

    tokens = re.findall(r"\S+", stripped)
    if not tokens or len(tokens) > 8:
        return False

    long_words = len(re.findall(r"[A-Za-z]{4,}", stripped))
    math_chars = len(re.findall(r"[=∑∫√∞≈≤≥±×÷·^_{}()\[\]�]|[\uf000-\uf8ff]|[α-ωΑ-Ω]", stripped))
    compact_symbols = all(len(token) <= 6 for token in tokens)
    compact_variable_tokens = len(re.findall(r"\b[A-Za-z]{1,3}\d*\b", stripped))
    return compact_symbols and long_words <= 1 and (
        math_chars > 0 or len(tokens) <= 3 or compact_variable_tokens >= 3
    )


def _broaden_equation_rect(page: "fitz.Page", rect: "fitz.Rect") -> "fitz.Rect":
    broadened = fitz.Rect(rect)
    page_width = page.rect.width
    horizontal_margin = min(54, page_width * 0.08)
    broadened.x0 = page.rect.x0 + horizontal_margin
    broadened.x1 = page.rect.x1 - horizontal_margin
    broadened.y0 = max(page.rect.y0, broadened.y0 - 8)
    broadened.y1 = min(page.rect.y1, broadened.y1 + 8)
    return broadened


def _line_rect(line: dict) -> "fitz.Rect | None":
    bbox = line.get("bbox")
    if bbox:
        try:
            return fitz.Rect(bbox)
        except Exception:
            pass

    spans = line.get("spans", [])
    rects = []
    for span in spans:
        bbox = span.get("bbox")
        if not bbox:
            continue
        try:
            rects.append(fitz.Rect(bbox))
        except Exception:
            pass
    if not rects:
        return None

    line_bounds = fitz.Rect(rects[0])
    for rect in rects[1:]:
        line_bounds.include_rect(rect)
    return line_bounds


def _extract_equation_image(
    page: "fitz.Page",
    images_dir: Path,
    paper_stem: str,
    page_index: int,
    equation_number: int,
    rect: "fitz.Rect",
    zoom: float,
) -> Path | None:
    clip = fitz.Rect(rect)
    clip.x0 = max(page.rect.x0, clip.x0 - 8)
    clip.y0 = max(page.rect.y0, clip.y0 - 6)
    clip.x1 = min(page.rect.x1, clip.x1 + 8)
    clip.y1 = min(page.rect.y1, clip.y1 + 6)
    if clip.is_empty or clip.is_infinite:
        return None

    images_dir.mkdir(parents=True, exist_ok=True)
    image_path = images_dir / f"{paper_stem}-p{page_index:03d}-equation-{equation_number:02d}.png"
    try:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
        pixmap.save(image_path)
    except Exception:
        return None
    return image_path


def _style_line(text: str, spans: list[dict]) -> str:
    if _looks_like_equation(text):
        return f"$$\n{text}\n$$"

    max_size = max((span.get("size", 0) for span in spans), default=0)
    boldish = any("bold" in span.get("font", "").lower() for span in spans)
    word_count = len(text.split())
    if word_count <= 14 and (max_size >= 14 or boldish) and not text.endswith("."):
        level = "###" if max_size < 18 else "##"
        return f"{level} {text}"
    return text


def _looks_like_equation(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) > 180 or len(stripped) < 3:
        return False

    equation_index = bool(re.search(r"(^\(?\d+[a-zA-Z]?\)?\s+|\s+\(?\d+[a-zA-Z]?\)?$)", stripped))
    math_tokens = len(
        re.findall(
            r"[=∑∫√∞≈≤≥±×÷·^_{}]|\\[a-zA-Z]+|[α-ωΑ-Ω]|[\uf000-\uf8ff]|�||||",
            stripped,
        )
    )
    letters = len(re.findall(r"[A-Za-z]", stripped))
    words = len(re.findall(r"[A-Za-z]{3,}", stripped))
    compact_variable_tokens = len(re.findall(r"\b[A-Za-z]{1,3}\d*\b", stripped))

    if equation_index and math_tokens >= 1 and words <= 6:
        return True
    if math_tokens >= 2 and words <= max(4, letters // 10):
        return True
    if math_tokens >= 1 and compact_variable_tokens >= 2 and words <= 4:
        return True
    return False


def _format_text(text: str) -> str:
    paragraphs = []
    current: list[str] = []
    in_math = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "$$":
            if current:
                paragraphs.append(" ".join(current))
                current = []
            paragraphs.append(line)
            in_math = not in_math
            continue
        if in_math:
            paragraphs.append(line)
            continue
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if line.startswith("#") or line.startswith("!["):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            paragraphs.append(line)
        else:
            current.append(line)

    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def _table_to_markdown(rows: list[list[str | None]]) -> str:
    clean_rows = [
        [html.escape((cell or "").replace("\n", " ").strip()) for cell in row]
        for row in rows
        if row and any((cell or "").strip() for cell in row)
    ]
    if not clean_rows:
        return ""

    width = max(len(row) for row in clean_rows)
    normalized = [row + [""] * (width - len(row)) for row in clean_rows]
    header = normalized[0]
    separator = ["---"] * width
    body = normalized[1:] or [[""] * width]

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _extract_images(
    document: "fitz.Document",
    page: "fitz.Page",
    images_dir: Path,
    paper_stem: str,
    page_index: int,
) -> list[Path]:
    paths: list[Path] = []
    seen_xrefs: set[int] = set()
    for image_number, image in enumerate(page.get_images(full=True), start=1):
        xref = image[0]
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        try:
            extracted = document.extract_image(xref)
        except Exception:
            continue
        ext = extracted.get("ext", "png")
        image_bytes = extracted.get("image")
        if not image_bytes:
            continue
        image_path = images_dir / f"{paper_stem}-p{page_index:03d}-{image_number:02d}.{ext}"
        image_path.write_bytes(image_bytes)
        paths.append(image_path)
    return paths


def _ocr_page(page: "fitz.Page", zoom: float) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""

    matrix = fitz.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    mode = "RGB" if pixmap.n < 4 else "RGBA"
    image = Image.frombytes(mode, [pixmap.width, pixmap.height], pixmap.samples)
    try:
        return pytesseract.image_to_string(image)
    except Exception:
        return ""


def _clean_markdown(markdown: str) -> str:
    markdown = re.sub(r"\n{4,}", "\n\n\n", markdown)
    return markdown.strip() + "\n"
