"""Generate the PDF fixtures of contract §7.1.1 (deterministic, no third-party PDF writer).

- `tests/fixtures/policies/v1.2-text.pdf`: the text of `v1.2.md` as a text-layer PDF.
  Markdown heading marks are dropped (as a rendered document would show them);
  every paragraph is one line on a landscape A4 page, so the text layer keeps
  each paragraph intact (a line break inside a paragraph would split quotes,
  see docs/architecture.md IMP-64). Font: the standard CJK font STSong-Light
  with the predefined `UniGB-UCS2-H` CMap, not embedded (viewers substitute;
  the text layer is plain UCS-2 and needs no font file).
- `tests/fixtures/policies/scanned.pdf`: one page holding only a grey-scale
  image of "scanned" text lines, with no font and no text layer (AC-005 → 415).

Run: uv run --offline python scripts/make_policy_pdfs.py
The output is byte-for-byte reproducible (no timestamps, fixed object order).
"""
from __future__ import annotations

import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ROOT / "tests" / "fixtures" / "policies"
PAGE_W, PAGE_H = 842, 595  # landscape A4, points
MARGIN = 50
FONT_SIZE = 10
LEADING = 17
LINES_PER_PAGE = (PAGE_H - 2 * MARGIN) // LEADING


def _pdf(objects: list[bytes]) -> bytes:
    """Objects are numbered 1..n in order; object 1 must be the catalog."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _stream(data: bytes, extra: str = "") -> bytes:
    packed = zlib.compress(data, 9)
    return (f"<< /Length {len(packed)} /Filter /FlateDecode {extra}>>\nstream\n".encode() + packed
            + b"\nendstream")


def _ucs2_hex(text: str) -> str:
    return "<" + text.encode("utf-16-be").hex().upper() + ">"


def paragraphs(markdown: str) -> list[str]:
    lines = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        lines.append(line)
    return lines


def text_pdf(lines: list[str]) -> bytes:
    pages = []
    current: list[str] = []
    for line in lines:
        if len(current) == LINES_PER_PAGE:
            pages.append(current)
            current = []
        if line or current:  # no blank line at the top of a page
            current.append(line)
    if current:
        pages.append(current)

    # 1 catalog, 2 pages, 3 font (Type0), 4 CIDFont, 5 descriptor, then (page, content) pairs
    page_numbers = [6 + 2 * i for i in range(len(pages))]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{n} 0 R' for n in page_numbers)}] /Count {len(pages)} >>"
        .encode(),
        b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H"
        b" /DescendantFonts [4 0 R] >>",
        b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light"
        b" /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >>"
        b" /FontDescriptor 5 0 R /DW 1000 /W [1 95 500] >>",
        b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 /FontBBox [-25 -254 1000 880]"
        b" /ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 880 /StemV 93 >>",
    ]
    for index, page_lines in enumerate(pages):
        ops = [f"BT /F1 {FONT_SIZE} Tf {LEADING} TL {MARGIN} {PAGE_H - MARGIN} Td"]
        for line in page_lines:
            ops.append(f"{_ucs2_hex(line)} Tj T*" if line else "T*")
        ops.append("ET")
        content_number = page_numbers[index] + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}]"
            f" /Resources << /Font << /F1 3 0 R >> >> /Contents {content_number} 0 R >>".encode())
        objects.append(_stream("\n".join(ops).encode("ascii")))
    return _pdf(objects)


def scanned_pdf() -> bytes:
    """A page image of grey 'text lines' on off-white paper, with light deterministic noise."""
    width, height = 827, 585
    rows = bytearray()
    seed = 12345
    for y in range(height):
        rows.append(0)  # PNG filter type "None" for this row (see /DecodeParms below)
        for x in range(width):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            value = 240 if (seed >> 16) % 8 else 228
            band = (y - 60) % 31
            if 60 <= y < height - 60 and band < 11 and 66 <= x < width - 66 - (y * 37) % 170:
                if (x // 10 + y // 31) % 9 != 0:  # gaps between "words"
                    value = 60
            rows.append(value)
    image = _stream(bytes(rows), f"/Type /XObject /Subtype /Image /Width {width} /Height {height}"
                                 " /ColorSpace /DeviceGray /BitsPerComponent 8"
                                 f" /DecodeParms << /Predictor 15 /Colors 1 /Columns {width} >> ")
    content = f"q {PAGE_W} 0 0 {PAGE_H} 0 0 cm /Im1 Do Q".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}]"
        " /Resources << /XObject << /Im1 4 0 R >> >> /Contents 5 0 R >>".encode(),
        image,
        _stream(content),
    ]
    return _pdf(objects)


def main() -> None:
    source = (POLICIES / "v1.2.md").read_text(encoding="utf-8")
    (POLICIES / "v1.2-text.pdf").write_bytes(text_pdf(paragraphs(source)))
    (POLICIES / "scanned.pdf").write_bytes(scanned_pdf())
    print("wrote", POLICIES / "v1.2-text.pdf", "and", POLICIES / "scanned.pdf")


if __name__ == "__main__":
    main()
