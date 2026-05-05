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
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            if equation_image_writer and _looks_like_equation(text):
                equation_rect = _line_rect(line)
                equation_markdown = equation_image_writer(equation_rect) if equation_rect else None
                if equation_markdown:
                    lines.append(equation_markdown)
                    continue
            lines.append(_style_line(text, line.get("spans", [])))
        if lines and lines[-1] != "":
            lines.append("")

    return "\n".join(lines).strip()


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
    if len(text) > 180 or len(text) < 3:
        return False
    math_tokens = len(re.findall(r"[=∑∫√∞≈≤≥±×÷^_{}]|\\[a-zA-Z]+", text))
    letters = len(re.findall(r"[A-Za-z]", text))
    words = len(re.findall(r"[A-Za-z]{3,}", text))
    return math_tokens >= 2 and words <= max(3, letters // 12)


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
