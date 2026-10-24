"""Policy documents: type sniffing, text extraction, normalization and quote location (§4.8).

A document is a list of *segments* (one per non-blank line, with its page for
PDFs). Normalization is exactly what §4.8 allows: Unicode NFC, unified line
breaks, and every run of whitespace folded into one space. The normalized full
text is the normalized segments joined by single spaces, which equals
normalizing the raw text as a whole, so a quote found there can always be
mapped back to the segments it spans.

Locators: Markdown `line:N` (or `line:N-M` when a quote spans lines), PDF
`page:N` (or `page:N-M`). The seed data uses the same `line:N` form.
"""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_WHITESPACE = re.compile(r"\s+")
MARKDOWN_SUFFIXES = (".md", ".markdown")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    return _WHITESPACE.sub(" ", text).strip()


class DocumentError(Exception):
    """The file is not a usable policy document. Carries the HTTP status and error code."""

    def __init__(self, status_code: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class Segment:
    page: int | None  # 1-based page for PDFs, None for Markdown
    line: int  # 1-based line in the source (Markdown) or within the page (PDF)
    text: str  # normalized, non-empty


@dataclass(frozen=True)
class PolicyDocument:
    kind: str  # "markdown" | "pdf"
    segments: tuple[Segment, ...]
    page_count: int | None = None

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments)

    @property
    def normalized_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def locate(self, quote: str) -> str | None:
        """Locator of the first occurrence of the normalized quote, or None (→ invalid_quote)."""
        needle = normalize(quote)
        if not needle:
            return None
        start = self.text.find(needle)
        if start < 0:
            return None
        end = start + len(needle)
        offset, first, last = 0, None, None
        for segment in self.segments:
            seg_end = offset + len(segment.text)
            if first is None and start < seg_end:
                first = segment
            if end <= seg_end:
                last = segment
                break
            offset = seg_end + 1  # the joining space
        assert first is not None and last is not None
        if self.kind == "pdf":
            return f"page:{first.page}" if first.page == last.page else f"page:{first.page}-{last.page}"
        return f"line:{first.line}" if first.line == last.line else f"line:{first.line}-{last.line}"


def _segments(text: str, page: int | None = None) -> list[Segment]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [Segment(page, number, normalized) for number, raw in enumerate(lines, start=1)
            if (normalized := normalize(raw))]


def sniff(filename: str | None, data: bytes) -> str:
    """'pdf' by magic bytes; 'markdown' for a .md/.markdown name with UTF-8 text; else 415."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    name = (filename or "").lower()
    if name.endswith(MARKDOWN_SUFFIXES) and b"\x00" not in data:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return "markdown"
    raise DocumentError(415, "UNSUPPORTED_MEDIA_TYPE", "只支持 Markdown（.md，UTF-8）或文本型 PDF",
                        {"allowed": ["text/markdown", "application/pdf"]})


def parse(kind: str, data: bytes) -> PolicyDocument:
    if kind == "markdown":
        text = data.decode("utf-8").removeprefix("\ufeff")
        document = PolicyDocument("markdown", tuple(_segments(text)))
        if not document.segments:
            raise DocumentError(422, "EMPTY_DOCUMENT", "制度文件没有文本内容", {})
        return document
    return _parse_pdf(data)


def _parse_pdf(data: bytes) -> PolicyDocument:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError(415, "UNSUPPORTED_MEDIA_TYPE", "不支持加密的 PDF", {"reason": "encrypted"})
        pages = [page.extract_text() or "" for page in reader.pages]
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError):
        raise DocumentError(415, "UNSUPPORTED_MEDIA_TYPE", "无法解析的 PDF 文件",
                            {"reason": "unreadable_pdf"}) from None
    segments = [segment for number, text in enumerate(pages, start=1) for segment in _segments(text, number)]
    if not segments:
        # No text layer at all: a scanned document. OCR is out of scope (REQ-005).
        raise DocumentError(415, "SCANNED_PDF", "该 PDF 没有文本层（扫描件），请上传文本型 PDF 或 Markdown",
                            {"pages": len(pages)})
    return PolicyDocument("pdf", tuple(segments), page_count=len(pages))


def load(path: Path, kind: str) -> PolicyDocument:
    return parse(kind, path.read_bytes())


def kind_of_path(path: str) -> str:
    return "pdf" if path.lower().endswith(".pdf") else "markdown"
