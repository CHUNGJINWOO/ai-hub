from pathlib import Path

from pypdf import PdfReader


CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200


def chunk_text(text: str) -> list[str]:
    text = text.strip()

    if not text:
        return []

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = max(0, end - CHUNK_OVERLAP)

    return chunks


def extract_text_from_pdf(path: Path) -> list[tuple[int | None, str]]:
    reader = PdfReader(str(path))

    pages = []

    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = text.strip()

        if text:
            pages.append((page_number, text))

    return pages


def extract_text_from_plain_file(path: Path) -> list[tuple[int | None, str]]:
    text = path.read_text(encoding="utf-8")
    text = text.strip()

    if not text:
        return []

    return [(None, text)]


def extract_text(path: Path, mime_type: str | None) -> list[tuple[int | None, str]]:
    suffix = path.suffix.lower()

    if suffix == ".pdf" or mime_type == "application/pdf":
        return extract_text_from_pdf(path)

    if suffix in {".txt", ".md", ".markdown"}:
        return extract_text_from_plain_file(path)

    raise ValueError(
        "Unsupported document type. Supported types: PDF, TXT, Markdown."
    )


def chunk_pages(
    pages: list[tuple[int | None, str]],
) -> list[tuple[int, int | None, str]]:
    chunks = []
    chunk_index = 0

    for page_number, text in pages:
        for chunk in chunk_text(text):
            chunks.append(
                (
                    chunk_index,
                    page_number,
                    chunk,
                )
            )
            chunk_index += 1

    return chunks
