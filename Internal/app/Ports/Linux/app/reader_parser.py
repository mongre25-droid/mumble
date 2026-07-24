"""Reader parser registry — converts diverse file formats to a unified structured
document model (list of ContentBlocks).

Design: one parser per format, registered by file extension. Each parser returns a
ParsedDocument whose `blocks` list replaces the old bare-text field. The store
layer prepends a PlainText snapshot for backward compatibility.

Adding a new format:
  1. Write a parse_*(raw_bytes, filename, metadata) -> ParsedDocument function.
  2. Register it: ParserRegistry.register(name, extensions, callable).
  3. That's it — the import UI and store pick it up automatically.

Owner 2026-06-29 — reader-system redesign milestone.
"""

import dataclasses
import re
from typing import Callable, Dict, List, Optional


# ── Unified document model ──────────────────────────────────────────────────


@dataclasses.dataclass
class ContentBlock:
    """One structural unit of a parsed document.

    type       — "heading" | "paragraph" | "list_item" | "code" | "image" |
                 "table" | "blockquote" | "horizontal_rule" | "break"
    text       — the block's visible text (empty for images / rules / page breaks)
    level      — heading level (1–6); list nesting level; 0 otherwise
    meta       — arbitrary extra data (image src, language, colspan, etc.)
    """
    type: str = "paragraph"
    text: str = ""
    level: int = 0
    meta: Optional[dict] = None

    def as_dict(self):
        d = {"type": self.type, "text": self.text}
        if self.level:
            d["level"] = self.level
        if self.meta:
            d["meta"] = self.meta
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(
            type=d.get("type", "paragraph"),
            text=d.get("text", ""),
            level=int(d.get("level", 0) or 0),
            meta=d.get("meta"),
        )


@dataclasses.dataclass
class ParsedDocument:
    """The result of parsing a file.

    title    — auto-detected title (first heading or filename stem)
    blocks   — ordered structural blocks
    metadata — format-specific extras (author, page_count, source_url, …)
    """
    title: str = "Untitled"
    blocks: List[ContentBlock] = dataclasses.field(default_factory=list)
    metadata: Dict = dataclasses.field(default_factory=dict)

    def plain_text(self) -> str:
        """Return the canonical text that the Reader speaks and indexes.

        Structural markers such as Markdown ``#`` / bullets are deliberately
        omitted. Reader positions are word indexes into this exact string, so
        display-only tokens here shift bookmarks and leave completed structured
        documents looking unfinished in the library.
        """
        lines = []
        for b in self.blocks:
            if b.type in ("horizontal_rule", "break", "image"):
                if b.type == "break":
                    lines.append("")
                continue
            if b.type == "table":
                # Render table as tab-separated text
                meta = b.meta or {}
                headers = meta.get("headers", [])
                rows = meta.get("rows", [])
                if headers:
                    lines.append("\t".join(str(c) for c in headers))
                for row in rows:
                    lines.append("\t".join(str(c) for c in row))
                continue
            t = (b.text or "").strip()
            if not t:
                continue
            lines.append(t)
        return "\n\n".join(lines)


# ── Parser registry ─────────────────────────────────────────────────────────


class ParserRegistry:
    """Extension → parser callable registry.

    Callable receives (raw_bytes: bytes, filename: str, metadata: dict)
    and returns a ParsedDocument. Raise ValueError for unsupported content.
    """
    _parsers: Dict[str, Callable] = {}          # extension → parser
    _names: Dict[str, str] = {}                 # name → primary extension

    @classmethod
    def register(cls, name: str, extensions: List[str],
                 parser: Callable):
        for ext in extensions:
            ext = ext.lower().lstrip(".")
            # The empty extension is the fallback for README/LICENSE-style
            # plain-text files, not the literal suffix ".".
            cls._parsers[("." + ext) if ext else ""] = parser
        cls._names[name] = extensions[0] if extensions else ""

    @classmethod
    def get(cls, extension: str) -> Optional[Callable]:
        return cls._parsers.get(extension.lower())

    @classmethod
    def supported_extensions(cls) -> List[str]:
        # File-picker filters need real suffixes only; extensionless support is
        # still available through parse_file/parse_bytes.
        return sorted(ext for ext in cls._parsers.keys() if ext)

    @classmethod
    def parse_file(cls, path: str, max_bytes: Optional[int] = None) -> "ParsedDocument":
        """Read a file from disk, dispatch to the matching parser, return blocks."""
        import os
        ext = os.path.splitext(path)[1].lower()
        parser = cls.get(ext)
        if parser is None:
            raise ValueError(
                f"Unsupported file format: {ext or 'no extension'}. "
                f"Supported: {', '.join(cls.supported_extensions())}")
        with open(path, "rb") as f:
            if max_bytes is None:
                raw = f.read()
            else:
                limit = max(0, int(max_bytes))
                raw = f.read(limit + 1)
                if len(raw) > limit:
                    raise ValueError(
                        f"That document is larger than {limit // (1024 * 1024)} MB.")
        filename = os.path.basename(path)
        return parser(raw, filename, {"source_path": path})

    @classmethod
    def parse_bytes(cls, data: bytes, filename: str,
                    source_path: str = "") -> "ParsedDocument":
        """Parse in-memory bytes (e.g. from drag-drop or clipboard)."""
        import os
        ext = os.path.splitext(filename)[1].lower()
        parser = cls.get(ext)
        if parser is None:
            raise ValueError(
                f"Unsupported file format: {ext or 'no extension'}")
        return parser(data, filename, {"source_path": source_path})


# ── Built-in parsers ────────────────────────────────────────────────────────


def _parse_plain_text(raw: bytes, filename: str, _meta: dict) -> ParsedDocument:
    text = _decode_text(raw, filename).strip()
    title = _auto_title(text, filename)
    blocks = _text_to_blocks(text)
    return ParsedDocument(title=title, blocks=blocks,
                          metadata={"format": "text"})


def _parse_markdown(raw: bytes, filename: str, _meta: dict) -> ParsedDocument:
    text = _decode_text(raw, filename)
    title = _auto_title(text, filename)
    blocks = _md_to_blocks(text)
    return ParsedDocument(title=title, blocks=blocks,
                          metadata={"format": "markdown"})


def _auto_title(text: str, filename: str = "") -> str:
    """Best-effort auto-title: first non-empty heading line or filename stem."""
    for line in text.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Strip common heading markers for the title
        cleaned = re.sub(r'^#{1,6}\s*', '', stripped)
        cleaned = re.sub(r'^>\s*', '', cleaned)
        cleaned = re.sub(r'^[-*]\s*', '', cleaned)
        cleaned = _strip_markdown_inline(cleaned)
        if cleaned:
            return (cleaned[:70] + "…") if len(cleaned) > 70 else cleaned
    if filename:
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        return stem.strip() or "Untitled"
    return "Untitled"


def _text_to_blocks(text: str) -> List[ContentBlock]:
    """Split plain text on double-newlines into paragraph blocks."""
    blocks = []
    for para in re.split(r'\n\s*\n', text):
        lines = para.strip().splitlines()
        if not lines or not any(l.strip() for l in lines):
            continue
        joined = " ".join(l.strip() for l in lines if l.strip())
        if joined:
            blocks.append(ContentBlock(type="paragraph", text=joined))
    return blocks


def _strip_markdown_inline(text: str) -> str:
    """Turn common inline Markdown into readable display/speech text."""
    import html
    value = text or ""
    value = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', value)
    value = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', value)
    value = re.sub(r'<(?:https?://|mailto:)[^>]+>', '', value)
    value = re.sub(r'`([^`]+)`', r'\1', value)
    value = re.sub(r'(\*\*|__|~~)(.+?)\1', r'\2', value)
    value = re.sub(r'(?<!\w)([*_])([^\n]+?)\1(?!\w)', r'\2', value)
    value = re.sub(r'^\s*\[([ xX])\]\s*',
                   lambda m: 'Done: ' if m.group(1).lower() == 'x' else 'Not done: ',
                   value)
    value = re.sub(r'<[^>]+>', '', value)
    value = re.sub(r'\\([\\`*{}\[\]()#+.!_>-])', r'\1', value)
    return html.unescape(value).strip()


def _md_to_blocks(text: str) -> List[ContentBlock]:
    """Convert Markdown to structured ContentBlocks (headings, paragraphs,
    lists, code blocks, blockquotes, horizontal rules).

    Covers the 80/20 of real-world markdown: fenced code blocks, unordered
    lists, ordered lists, ATX headings, blockquotes, hr's, and paragraphs.
    Nested lists and inline formatting are preserved as raw markdown; the
    renderer handles them downstream."""
    blocks = []
    lines = text.splitlines()
    i, n = 0, len(lines)

    def _flush_para(buf):
        if buf:
            readable = _strip_markdown_inline("\n".join(buf).strip())
            if not readable:
                return
            blocks.append(ContentBlock(
                type="paragraph",
                text=readable))

    def _flush_list(buf, ordered=False, base_level=1):
        if not buf:
            return
        for entry in buf:
            item, level = entry[:2]
            order = entry[2] if len(entry) > 2 else None
            meta = {"ordered": ordered}
            if order is not None:
                meta["order"] = order
            blocks.append(ContentBlock(
                type="list_item",
                text=_strip_markdown_inline(item),
                level=level,
                meta=meta))

    def _table_cells(line):
        value = line.strip().strip('|')
        cells = re.split(r'(?<!\\)\|', value)
        return [_strip_markdown_inline(c.replace(r'\|', '|').strip())
                for c in cells]

    while i < n:
        line = lines[i]

        # Blank line
        if not line.strip():
            i += 1
            continue

        # Fenced code block (``` or ~~~)
        if line.strip().startswith("```") or line.strip().startswith("~~~"):
            fence = line.strip()[:3]
            lang = line.strip()[3:].strip()
            i += 1
            code_lines = []
            while i < n and not lines[i].strip().startswith(fence):
                code_lines.append(lines[i])
                i += 1
            blocks.append(ContentBlock(
                type="code",
                text="\n".join(code_lines),
                meta={"language": lang or None}))
            i += 1  # skip closing fence
            continue

        # Horizontal rule
        if re.match(r'^ {0,3}([-*_])[ \t]*\1[ \t]*\1[ \t]*$', line):
            blocks.append(ContentBlock(type="horizontal_rule"))
            i += 1
            continue

        # ATX heading
        hm = re.match(r'^(#{1,6})\s+(.+?)(?:\s+#+)?$', line)
        if hm:
            blocks.append(ContentBlock(
                type="heading",
                text=_strip_markdown_inline(hm.group(2)),
                level=len(hm.group(1))))
            i += 1
            continue

        # GitHub-style pipe table: header row + delimiter row + data rows.
        if "|" in line and i + 1 < n and "|" in lines[i + 1]:
            headers = _table_cells(line)
            delimiter = _table_cells(lines[i + 1])
            if (headers and len(delimiter) == len(headers)
                    and all(re.fullmatch(r':?-{3,}:?', cell or '')
                            for cell in delimiter)):
                truncated = len(headers) > _MAX_COLS
                headers = headers[:_MAX_COLS]
                i += 2
                rows = []
                while i < n and lines[i].strip() and "|" in lines[i]:
                    if len(rows) >= _MAX_ROWS:
                        truncated = True
                        while i < n and lines[i].strip() and "|" in lines[i]:
                            i += 1
                        break
                    row = _table_cells(lines[i])
                    if len(row) > _MAX_COLS:
                        truncated = True
                    rows.append(row[:_MAX_COLS])
                    i += 1
                meta = {"headers": headers, "rows": rows}
                if truncated:
                    meta["truncated"] = True
                blocks.append(ContentBlock(type="table", meta=meta))
                continue

        # Blockquote
        if line.lstrip().startswith(">"):
            buf = []
            while i < n:
                l = lines[i]
                if l.lstrip().startswith(">"):
                    buf.append(re.sub(r'^[> ]{1,4}', '', l))
                    i += 1
                elif not l.strip():
                    i += 1
                    break
                else:
                    break
            blocks.append(ContentBlock(
                type="blockquote",
                text=_strip_markdown_inline(
                    " ".join(b.strip() for b in buf if b.strip()))))
            continue

        # Unordered list items
        ul_match = re.match(r'^(\s*)[-*+]\s+(.+)', line)
        if ul_match:
            buf = []
            while i < n:
                m = re.match(r'^(\s*)[-*+]\s+(.+)', lines[i])
                if m:
                    level = len(m.group(1)) // 2 + 1
                    buf.append((m.group(2), level, None))
                    i += 1
                elif not lines[i].strip():
                    i += 1
                    break
                else:
                    break
            _flush_list(buf, ordered=False)
            continue

        # Ordered list items
        ol_match = re.match(r'^(\s*)(\d+)[.)]\s+(.+)', line)
        if ol_match:
            buf = []
            while i < n:
                m = re.match(r'^(\s*)(\d+)[.)]\s+(.+)', lines[i])
                if m:
                    level = len(m.group(1)) // 2 + 1
                    buf.append((m.group(3), level, int(m.group(2))))
                    i += 1
                elif not lines[i].strip():
                    i += 1
                    break
                else:
                    break
            _flush_list(buf, ordered=True)
            continue

        # Paragraph (accumulate until blank line or a structural line)
        buf = [line]
        i += 1
        while i < n and lines[i].strip():
            nl = lines[i]
            if (nl.lstrip().startswith("```") or nl.lstrip().startswith("~~~")
                    or re.match(r'^(#{1,6})\s', nl)
                    or re.match(r'^ {0,3}([-*_])[ \t]*\1[ \t]*\1', nl)
                    or nl.lstrip().startswith(">")
                    or re.match(r'^(\s*)[-*+]\s+(.+)', nl)
                    or re.match(r'^(\s*)(\d+)[.)]\s+(.+)', nl)):
                break
            buf.append(nl)
            i += 1
        _flush_para(buf)

    return blocks


# ── Register built-in parsers ───────────────────────────────────────────────
ParserRegistry.register("text", [".txt", ".text", ""],
                        _parse_plain_text)
ParserRegistry.register("markdown", [".md", ".markdown", ".mdown"],
                        _parse_markdown)


# ── Optional format parsers (loaded on-demand; dependency absent → clear error) ──

_MAX_ARCHIVE_MEMBERS = 20000
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_PDF_PAGES = 5000
_MAX_EXTRACTED_CHARS = 4 * 1024 * 1024


def _guard_zip_archive(raw: bytes, label: str):
    """Reject corrupt/decompression-bomb office/e-book containers early."""
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_ARCHIVE_MEMBERS:
                raise ValueError(
                    f"{label} contains too many files to import safely.")
            expanded = sum(max(0, info.file_size) for info in infos)
            if expanded > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ValueError(
                    f"{label} expands beyond the 256 MB safety limit.")
    except zipfile.BadZipFile as e:
        raise ValueError(
            f"Corrupt or unreadable {label}: not a valid document archive.") from e

def _parse_pdf(raw: bytes, filename: str, _meta: dict) -> ParsedDocument:
    """Extract text from a PDF via PyMuPDF (fitz). Fastest, most reliable PDF
    text extraction available in pure Python."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ValueError(
            "PDF support requires PyMuPDF. Install: pip install PyMuPDF")
    import io
    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Corrupt or unreadable PDF file: {e}") from e
    blocks = []
    page_count = doc.page_count
    extracted_chars = 0
    try:
        if page_count > _MAX_PDF_PAGES:
            raise ValueError(
                f"That PDF has more than {_MAX_PDF_PAGES:,} pages. Split it before importing.")
        for page_num, page in enumerate(doc, 1):
            text = page.get_text("text") or ""
            extracted_chars += len(text)
            if extracted_chars > _MAX_EXTRACTED_CHARS:
                raise ValueError(
                    "That PDF contains more than 4 MB of text. Split it before importing.")
            if not text.strip():
                continue
            # Add a page-break marker between pages
            if blocks:
                blocks.append(ContentBlock(type="break"))
            for para in re.split(r'\n\s*\n', text.strip()):
                joined = " ".join(para.split()).strip()
                if joined:
                    blocks.append(ContentBlock(type="paragraph", text=joined))
    finally:
        doc.close()
    title = _auto_title(blocks[0].text if blocks else "", filename)
    return ParsedDocument(
        title=title, blocks=blocks,
        metadata={"format": "pdf", "page_count": page_count})


def _parse_docx(raw: bytes, filename: str, _meta: dict) -> ParsedDocument:
    """Extract text + basic structure from a .docx file."""
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        raise ValueError(
            "DOCX support requires python-docx. Install: pip install python-docx")
    import io
    _guard_zip_archive(raw, "DOCX file")
    try:
        doc = Document(io.BytesIO(raw))
    except Exception as e:
        raise ValueError(
            f"Corrupt or unreadable DOCX file: {e}. "
            "The file may be damaged or not a valid Word document.") from e
    blocks = []

    def _add_paragraph(para):
        text = para.text.strip()
        if not text:
            return
        style = (para.style.name or "").lower() if para.style else ""
        if "heading" in style:
            # Try to extract heading level from style name
            level = 1
            for ch in style:
                if ch.isdigit():
                    level = min(6, int(ch))
                    break
            blocks.append(ContentBlock(
                type="heading", text=text, level=level))
        elif any(kw in style for kw in ("list", "bullet", "numbered")):
            ordered = "number" in style
            blocks.append(ContentBlock(
                type="list_item", text=text,
                meta={"ordered": ordered}))
        else:
            blocks.append(ContentBlock(type="paragraph", text=text))

    def _add_table(table):
        headers = []
        rows = []
        truncated = False
        for row_index, row in enumerate(table.rows):
            values = [cell.text.strip() for cell in row.cells]
            if len(values) > _MAX_COLS:
                values = values[:_MAX_COLS]
                truncated = True
            if row_index == 0:
                headers = values
            elif row_index <= _MAX_ROWS:
                rows.append(values)
            else:
                truncated = True
                break
        if not headers and not rows:
            return
        meta = {"headers": headers, "rows": rows}
        if truncated:
            meta["truncated"] = True
        blocks.append(ContentBlock(type="table", meta=meta))

    # python-docx 1.2 exposes document-order iteration over paragraphs and
    # tables. doc.paragraphs alone silently drops every table in a Word file.
    iterator = (doc.iter_inner_content() if hasattr(doc, "iter_inner_content")
                else list(doc.paragraphs) + list(doc.tables))
    for item in iterator:
        if isinstance(item, Paragraph):
            _add_paragraph(item)
        elif isinstance(item, Table):
            _add_table(item)
    title = _auto_title(blocks[0].text if blocks else "", filename)
    return ParsedDocument(
        title=title, blocks=blocks,
        metadata={"format": "docx"})


def _parse_html(raw: bytes, filename: str, _meta: dict) -> ParsedDocument:
    """Extract readable text + structure from HTML, stripping tags and scripts."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ValueError(
            "HTML support requires beautifulsoup4. Install: pip install beautifulsoup4")
    # Decode through the same UTF-8/UTF-16/cp1252 path as text imports. HTML
    # parser auto-detection guesses short cp1252 snippets incorrectly (for
    # example café as an Arabic presentation character).
    soup = BeautifulSoup(_decode_text(raw, filename), "html.parser")
    # Strip script, style, nav, footer, header
    for tag in soup.find_all(["script", "style", "nav", "footer", "header",
                               "aside", "noscript"]):
        tag.decompose()
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else _auto_title(
        soup.get_text(separator="\n", strip=True), filename)
    blocks = _html_soup_to_blocks(soup)
    # If nothing meaningful was extracted, fall back to raw text
    if not blocks:
        text = soup.get_text(separator="\n", strip=True)
        if text:
            blocks = _text_to_blocks(text)
    return ParsedDocument(
        title=title, blocks=blocks,
        metadata={"format": "html"})


def _html_soup_to_blocks(soup) -> List[ContentBlock]:
    """Walk a BeautifulSoup body and extract ContentBlocks for headings,
    paragraphs, lists, blockquotes, and code blocks."""
    blocks = []
    container_tags = {"li", "blockquote", "pre"}

    def _container_text(el):
        """Text owned by a container, excluding nested block containers.

        BeautifulSoup's get_text() includes every descendant.  The old walker
        then visited those descendants again, so ``<blockquote><p>...`` and
        nested lists were spoken twice.  Paragraphs inside a container belong
        to that container; nested containers get their own block.
        """
        parts = []
        for node in el.find_all(string=True):
            text = str(node).strip()
            if not text:
                continue
            parent = node.parent
            nested = False
            while parent is not None and parent is not el:
                if getattr(parent, "name", None) in container_tags:
                    nested = True
                    break
                parent = parent.parent
            if not nested:
                parts.append(text)
        return " ".join(parts)

    for el in soup.find_all(
            ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote",
             "pre", "hr"]):
        tag = el.name.lower()
        parent_container = el.find_parent(list(container_tags))
        if parent_container is not None and tag not in container_tags:
            continue

        text = (_container_text(el) if tag in container_tags
                else el.get_text(separator=" ", strip=True))
        if not text and tag not in ("hr",):
            continue
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            blocks.append(ContentBlock(type="heading", text=text, level=level))
        elif tag == "li":
            list_parent = el.find_parent(["ol", "ul"])
            ordered = bool(list_parent and list_parent.name.lower() == "ol")
            meta = {"ordered": ordered}
            if ordered:
                try:
                    start = int(list_parent.get("start") or 1)
                except (TypeError, ValueError):
                    start = 1
                siblings = list_parent.find_all("li", recursive=False)
                try:
                    meta["order"] = start + siblings.index(el)
                except ValueError:
                    meta["order"] = start
            level = len(el.find_parents(["ol", "ul"])) or 1
            blocks.append(ContentBlock(
                type="list_item", text=text, level=level, meta=meta))
        elif tag == "blockquote":
            blocks.append(ContentBlock(type="blockquote", text=text))
        elif tag == "pre":
            blocks.append(ContentBlock(type="code", text=text))
        elif tag == "hr":
            blocks.append(ContentBlock(type="horizontal_rule"))
        else:
            blocks.append(ContentBlock(type="paragraph", text=text))
    return blocks


def _parse_epub(raw: bytes, filename: str, _meta: dict) -> ParsedDocument:
    """Extract text from an EPUB e-book via ebooklib + bs4."""
    try:
        from bs4 import BeautifulSoup
        from ebooklib import ITEM_DOCUMENT
        from ebooklib import epub
    except ImportError:
        raise ValueError(
            "EPUB support requires ebooklib + beautifulsoup4. "
            "Install: pip install ebooklib beautifulsoup4")
    import io
    _guard_zip_archive(raw, "EPUB file")
    try:
        book = epub.read_epub(io.BytesIO(raw))
    except Exception as e:
        raise ValueError(
            f"Corrupt or unreadable EPUB file: {e}. "
            "The file may be damaged or not a valid EPUB book.") from e

    # EPUB's spine is the publication's reading order. Iterating archive items
    # instead can read chapters out of order and include the navigation page as
    # if it were a chapter.
    document_items = list(book.get_items_of_type(ITEM_DOCUMENT))
    ordered_items = []
    seen_ids = set()
    for entry in (book.spine or []):
        ref = entry[0] if isinstance(entry, (tuple, list)) else entry
        linear = entry[1] if isinstance(entry, (tuple, list)) and len(entry) > 1 else "yes"
        if str(linear).lower() == "no":
            continue
        if hasattr(ref, "get_id"):
            item = ref
        else:
            item = book.get_item_with_id(str(ref))
        if (item is None or isinstance(item, epub.EpubNav)
                or item.get_type() != ITEM_DOCUMENT
                or item.get_id() in seen_ids):
            continue
        ordered_items.append(item)
        seen_ids.add(item.get_id())
    if not ordered_items:
        ordered_items = [item for item in document_items
                         if not isinstance(item, epub.EpubNav)]

    blocks = []
    title = "Untitled"
    for item in ordered_items:
        soup = BeautifulSoup(item.get_content(), "html.parser",
                             from_encoding="utf-8")
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()
        item_blocks = _html_soup_to_blocks(soup)
        if item_blocks:
            if blocks:
                blocks.append(ContentBlock(type="break"))
            blocks.extend(item_blocks)

    md = book.get_metadata("DC", "title")
    if md:
        title = str(md[0][0]) if md[0] else title
    if not blocks:
        # Fallback: extract all text
        for item in ordered_items:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            for tag in soup.find_all(["script", "style", "nav"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            if text:
                blocks.extend(_text_to_blocks(text))
    if not title or title == "Untitled":
        title = _auto_title(blocks[0].text if blocks else "", filename)
    return ParsedDocument(
        title=title, blocks=blocks,
        metadata={"format": "epub"})


def _parse_rtf(raw: bytes, filename: str, _meta: dict) -> ParsedDocument:
    """Extract plain text from an RTF file. Uses a lightweight pure-Python
    RTF stripper — no heavy dependency needed."""
    text = _rtf_to_text(raw)
    title = _auto_title(text, filename)
    blocks = _text_to_blocks(text)
    return ParsedDocument(
        title=title, blocks=blocks,
        metadata={"format": "rtf"})


def _rtf_to_text(raw: bytes) -> str:
    """Strip RTF control words to extract plain text. Handles the common
    subset of RTF used by WordPad, Word, and Apple TextEdit.

    Based on the open-source striprtf algorithm — minimal, no dependencies."""
    # RTF's default byte encoding is ANSI, not UTF-8.  Decode one byte to one
    # code point first so ``\'xx`` escapes can be interpreted faithfully.
    data = raw.decode("latin-1", "replace")
    import re as _re

    # Drop non-body RTF destination groups before stripping control words.
    # Without this, font tables and metadata are emitted as narration (for
    # example "Times New Roman;" before the actual first sentence).
    destinations = {
        "fonttbl", "colortbl", "stylesheet", "info", "pict", "object",
        "header", "headerl", "headerr", "footer", "footerl", "footerr",
        "filetbl", "listtable", "listoverridetable", "revtbl", "generator",
        "xmlnstbl", "datastore", "themedata", "colorschememapping",
    }

    def _drop_destination_groups(value):
        out = []
        i = 0
        n = len(value)
        while i < n:
            if value[i] != "{":
                out.append(value[i])
                i += 1
                continue
            j = i + 1
            while j < n and value[j].isspace():
                j += 1
            ignorable = value.startswith(r"\*", j)
            if ignorable:
                j += 2
                while j < n and value[j].isspace():
                    j += 1
            match = _re.match(r"\\([A-Za-z]+)", value[j:])
            if not match or (not ignorable
                             and match.group(1).lower() not in destinations):
                out.append(value[i])
                i += 1
                continue
            depth = 1
            i = j + len(match.group(0))
            while i < n and depth:
                if value[i] == "\\" and i + 1 < n and value[i + 1] in "{}\\":
                    i += 2
                    continue
                if value[i] == "{":
                    depth += 1
                elif value[i] == "}":
                    depth -= 1
                i += 1
        return "".join(out)

    data = _drop_destination_groups(data)

    def _hex(match):
        value = bytes([int(match.group(1), 16)])
        return value.decode("cp1252", "replace")

    def _decode_unicode_controls(value):
        out = []
        i = 0
        fallback_chars = 1
        while i < len(value):
            uc_match = _re.match(r'\\uc(-?\d+)\s?', value[i:])
            if uc_match:
                fallback_chars = max(0, min(16, int(uc_match.group(1))))
                i += len(uc_match.group(0))
                continue
            uni_match = _re.match(r'\\u(-?\d+)\s?', value[i:])
            if not uni_match:
                out.append(value[i])
                i += 1
                continue
            codepoint = int(uni_match.group(1))
            if codepoint < 0:
                codepoint += 65536
            try:
                out.append(chr(codepoint))
            except ValueError:
                out.append("\ufffd")
            i += len(uni_match.group(0))
            for _ in range(fallback_chars):
                if value.startswith("\\'", i) and i + 3 < len(value):
                    i += 4
                elif i < len(value) and value[i] == "\\" and i + 1 < len(value):
                    i += 2
                elif i < len(value):
                    i += 1
        return "".join(out)

    # Decode Unicode controls first so their ANSI fallback bytes can be skipped
    # instead of being emitted as a duplicate character.
    data = _decode_unicode_controls(data)
    # Decode visible character escapes before the generic control-word pass.
    data = _re.sub(r"\\'([0-9a-fA-F]{2})", _hex, data)

    # Preserve structural controls.  The old implementation stripped every
    # control word first, making these replacements unreachable and collapsing
    # every RTF document into one paragraph.
    data = _re.sub(r'\\(?:par|line)\b\s?', '\n', data, flags=_re.I)
    data = _re.sub(r'\\tab\b\s?', '\t', data, flags=_re.I)
    symbols = {
        "emdash": "—", "endash": "–", "lquote": "‘", "rquote": "’",
        "ldblquote": "“", "rdblquote": "”", "bullet": "• ",
        "enspace": " ", "emspace": " ", "qmspace": " ",
    }
    data = _re.sub(
        r'\\(' + '|'.join(symbols) + r')\b\s?',
        lambda match: symbols[match.group(1).lower()], data, flags=_re.I)
    data = data.replace(r'\~', '\u00a0').replace(r'\_', '\u2011')

    # Strip remaining control words and decode escaped literal delimiters.
    data = _re.sub(r'\\[a-zA-Z]+-?\d*\s?', '', data)
    data = _re.sub(r'\\([\\{}\-])', r'\1', data)
    # Remove braces (group delimiters)
    data = data.replace("{", "").replace("}", "")
    # Normalize whitespace without destroying the tabs we deliberately kept.
    data = _re.sub(r' {2,}', ' ', data)
    data = _re.sub(r' *\n *', '\n', data)
    data = _re.sub(r'\n{3,}', '\n\n', data)
    return data.strip()


# ── New-format parsers (M3 Reader Expansion) ────────────────────────────────

# Caps to prevent memory exhaustion on large files
_MAX_ROWS = 5000
_MAX_COLS = 100


def _decode_text(raw: bytes, filename: str = "") -> str:
    """Decode common text-file encodings without corrupting imported prose.

    Windows editors and exports commonly produce UTF-8-with-BOM, UTF-16, or
    cp1252. Decoding all of those as UTF-8 with replacement characters made a
    valid document unreadable (UTF-16 also left a NUL between every letter).
    """
    import codecs

    # Test UTF-32 before UTF-16 because the little-endian UTF-32 BOM starts
    # with the UTF-16-LE BOM bytes.
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return raw.decode("utf-32")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # A BOM-less UTF-16 file still has a strong alternating-NUL signature for
    # ordinary prose. Prefer the byte order whose expected lane contains more
    # NULs, but only when at least 20% of the bytes are NUL to avoid guessing on
    # arbitrary binary content with a misleading .txt suffix.
    if raw and raw.count(b"\x00") >= max(2, len(raw) // 5):
        even_nuls = raw[0::2].count(0)
        odd_nuls = raw[1::2].count(0)
        encoding = "utf-16-le" if odd_nuls >= even_nuls else "utf-16-be"
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass

    # cp1252 is the practical "ANSI" encoding used by Western Windows apps;
    # latin-1 is the lossless final fallback for undefined cp1252 bytes.
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _parse_csv(raw: bytes, filename: str, _meta: dict) -> ParsedDocument:
    """Parse CSV into a table ContentBlock using stdlib csv module.

    First row is treated as headers, remaining rows as data.
    Caps rows at _MAX_ROWS and columns at _MAX_COLS.
    Empty files produce an empty ParsedDocument with a filename-derived title.
    """
    import csv
    import io as _io

    if not raw or not raw.strip():
        title = _auto_title("", filename)
        return ParsedDocument(
            title=title, blocks=[],
            metadata={"format": "csv"})

    text = _decode_text(raw, filename)
    reader = csv.reader(_io.StringIO(text))
    blocks: List[ContentBlock] = []
    truncated = False

    try:
        headers = next(reader)
    except StopIteration:
        title = _auto_title("", filename)
        return ParsedDocument(
            title=title, blocks=[],
            metadata={"format": "csv"})
    except csv.Error as e:
        raise ValueError(
            f"Malformed CSV file: {e}. The file could not be parsed as CSV.") from e

    # Cap columns
    if len(headers) > _MAX_COLS:
        headers = headers[:_MAX_COLS]
        truncated = True

    # Cap rows and columns while ITERATING.  Materialising ``list(reader)``
    # first defeated the advertised memory cap and let a large CSV exhaust the
    # process before the slice below was ever reached.
    capped_rows = []
    try:
        for index, row in enumerate(reader):
            if index >= _MAX_ROWS:
                truncated = True
                break
            if len(row) > _MAX_COLS:
                row = row[:_MAX_COLS]
                truncated = True
            capped_rows.append(list(row))
    except csv.Error as e:
        raise ValueError(
            f"Malformed CSV file: {e}. The file could not be parsed as CSV.") from e

    meta = {"headers": list(headers), "rows": capped_rows}
    if truncated:
        meta["truncated"] = True

    blocks.append(ContentBlock(type="table", meta=meta))

    title = _auto_title("", filename)
    return ParsedDocument(
        title=title, blocks=blocks,
        metadata={"format": "csv"})


def _parse_xlsx(raw: bytes, filename: str, _meta: dict) -> ParsedDocument:
    """Parse XLSX into table blocks (one per sheet) with sheet headings.

    Uses openpyxl with data_only=True to read cached formula values.
    Caps rows/cols per sheet. Handles empty cells as empty strings.
    """
    import io as _io

    if not raw:
        raise ValueError(
            "Empty XLSX file: the file contains no data. "
            "Please provide a valid XLSX spreadsheet.")
    _guard_zip_archive(raw, "XLSX file")

    try:
        import openpyxl
    except ImportError:
        raise ValueError(
            "XLSX support requires openpyxl. Install: pip install openpyxl")

    try:
        wb = openpyxl.load_workbook(_io.BytesIO(raw), data_only=True, read_only=True)
    except Exception as e:
        raise ValueError(
            f"Corrupt or unreadable XLSX file: {e}. "
            "The file may be damaged or not a valid XLSX spreadsheet.") from e

    blocks: List[ContentBlock] = []
    sheet_names = wb.sheetnames

    try:
        for sname in sheet_names:
            ws = wb[sname]

            # Sheet heading
            blocks.append(ContentBlock(
                type="heading",
                text=f"Sheet: {sname}",
                level=2))

            headers = []
            rows = []
            truncated = False
            row_count = 0

            for row in ws.iter_rows(values_only=True):
                row_vals = [(str(v) if v is not None else "") for v in row]
                if row_count == 0:
                    headers = row_vals
                    # Cap columns on headers
                    if len(headers) > _MAX_COLS:
                        headers = headers[:_MAX_COLS]
                        truncated = True
                else:
                    if row_count > _MAX_ROWS:
                        truncated = True
                        break
                    if len(row_vals) > _MAX_COLS:
                        row_vals = row_vals[:_MAX_COLS]
                        truncated = True
                    rows.append(row_vals)
                row_count += 1

            meta = {"headers": headers, "rows": rows}
            if truncated:
                meta["truncated"] = True

            blocks.append(ContentBlock(type="table", meta=meta))
    finally:
        wb.close()

    title = _auto_title("", filename)
    return ParsedDocument(
        title=title, blocks=blocks,
        metadata={"format": "xlsx"})


def _parse_pptx(raw: bytes, filename: str, _meta: dict) -> ParsedDocument:
    """Parse PPTX: extract slide text and speaker notes.

    Each slide gets a 'Slide N' heading, then paragraphs for text frames
    and notes (if any).
    """
    import io as _io

    if not raw:
        raise ValueError(
            "Empty PPTX file: the file contains no data. "
            "Please provide a valid PPTX presentation.")
    _guard_zip_archive(raw, "PPTX file")

    try:
        from pptx import Presentation
    except ImportError:
        raise ValueError(
            "PPTX support requires python-pptx. Install: pip install python-pptx")

    try:
        prs = Presentation(_io.BytesIO(raw))
    except Exception as e:
        raise ValueError(
            f"Corrupt or unreadable PPTX file: {e}. "
            "The file may be damaged or not a valid PPTX presentation.") from e

    blocks: List[ContentBlock] = []

    for slide_num, slide in enumerate(prs.slides, 1):
        # Slide heading
        blocks.append(ContentBlock(
            type="heading",
            text=f"Slide {slide_num}",
            level=2))

        # Extract text from all shapes
        has_content = False
        for shape in slide.shapes:
            if getattr(shape, "has_table", False):
                headers = []
                rows = []
                truncated = False
                for row_index, row in enumerate(shape.table.rows):
                    values = [cell.text.strip() for cell in row.cells]
                    if len(values) > _MAX_COLS:
                        values = values[:_MAX_COLS]
                        truncated = True
                    if row_index == 0:
                        headers = values
                    elif row_index <= _MAX_ROWS:
                        rows.append(values)
                    else:
                        truncated = True
                        break
                if headers or rows:
                    meta = {"headers": headers, "rows": rows}
                    if truncated:
                        meta["truncated"] = True
                    blocks.append(ContentBlock(type="table", meta=meta))
                    has_content = True
            elif shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        blocks.append(ContentBlock(type="paragraph", text=text))
                        has_content = True

        # Speaker notes
        try:
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    blocks.append(ContentBlock(type="paragraph", text=notes))
                    has_content = True
        except Exception:
            pass  # notes may not be accessible on all slides

    title = _auto_title("", filename)
    return ParsedDocument(
        title=title, blocks=blocks,
        metadata={"format": "pptx"})


def _parse_odt(raw: bytes, filename: str, _meta: dict) -> ParsedDocument:
    """Parse ODT (OpenDocument Text) using odfpy.

    Extracts headings (text:h), paragraphs (text:p), and list items (text:list).
    Handles heading outline levels for proper hierarchy.
    """
    import io as _io

    if not raw:
        raise ValueError(
            "Empty ODT file: the file contains no data. "
            "Please provide a valid ODT document.")
    _guard_zip_archive(raw, "ODT file")

    try:
        from odf.opendocument import load
        from odf import text as odf_text
    except ImportError:
        raise ValueError(
            "ODT support requires odfpy. Install: pip install odfpy")

    try:
        doc = load(_io.BytesIO(raw))
    except Exception as e:
        raise ValueError(
            f"Corrupt or unreadable ODT file: {e}. "
            "The file may be damaged or not a valid ODT document.") from e

    blocks: List[ContentBlock] = []
    title_text = ""

    def _extract_text(element):
        """Extract text content from an odfpy element recursively."""
        parts = []
        for node in element.childNodes:
            if node.nodeType == node.TEXT_NODE:
                parts.append(str(node.data))
            elif hasattr(node, 'childNodes'):
                tag = getattr(node, 'tagName', '')
                if tag == 'text:s':
                    try:
                        count = max(1, int(node.getAttribute('c') or '1'))
                    except (TypeError, ValueError):
                        count = 1
                    parts.append(' ' * count)
                elif tag == 'text:tab':
                    parts.append('\t')
                elif tag == 'text:line-break':
                    parts.append('\n')
                else:
                    parts.append(_extract_text(node))
        return "".join(parts)

    def _walk_elements(elem):
        """Recursively walk odfpy elements to extract ContentBlocks."""
        nonlocal title_text
        for child in elem.childNodes:
            tag = getattr(child, 'tagName', '')
            if not tag:
                continue

            # Heading
            if tag == 'text:h':
                level_str = child.getAttribute('outlinelevel') or '1'
                try:
                    level = int(level_str)
                except ValueError:
                    level = 1
                text_content = _extract_text(child).strip()
                if text_content:
                    if not title_text:
                        title_text = text_content
                    blocks.append(ContentBlock(
                        type="heading", text=text_content, level=min(level, 6)))

            # List
            elif tag == 'text:list':
                _extract_list_items(child)

            # Paragraph
            elif tag == 'text:p':
                # Skip paragraphs that are inside list items (handled by _extract_list_items)
                parent_tag = getattr(child.parentNode, 'tagName', '') if hasattr(child, 'parentNode') else ''
                if parent_tag not in ('text:list-item', 'text:list'):
                    text_content = _extract_text(child).strip()
                    if text_content:
                        blocks.append(ContentBlock(type="paragraph", text=text_content))

            # Recurse into other containers
            elif hasattr(child, 'childNodes'):
                _walk_elements(child)

    def _extract_list_items(list_elem, level=1):
        """Extract items from a text:list element."""
        for child in list_elem.childNodes:
            tag = getattr(child, 'tagName', '')
            if tag == 'text:list-item':
                own_parts = []
                nested_lists = []
                for item_child in child.childNodes:
                    item_tag = getattr(item_child, 'tagName', '')
                    if item_tag == 'text:list':
                        nested_lists.append(item_child)
                    elif item_tag in ('text:p', 'text:h'):
                        value = _extract_text(item_child).strip()
                        if value:
                            own_parts.append(value)
                text_content = ' '.join(own_parts).strip()
                if text_content:
                    blocks.append(ContentBlock(
                        type="list_item", text=text_content, level=level))
                for nested in nested_lists:
                    _extract_list_items(nested, level + 1)
            elif tag == 'text:list':
                # Nested list
                _extract_list_items(child, level + 1)

    # Walk the document body
    if hasattr(doc, 'body') and hasattr(doc.body, 'childNodes'):
        _walk_elements(doc.body)

    if not title_text:
        title_text = _auto_title("", filename)

    return ParsedDocument(
        title=title_text, blocks=blocks,
        metadata={"format": "odt"})


# ── Register optional parsers ───────────────────────────────────────────────
ParserRegistry.register("pdf", [".pdf"], _parse_pdf)
ParserRegistry.register("docx", [".docx"], _parse_docx)
ParserRegistry.register("html", [".html", ".htm"], _parse_html)
ParserRegistry.register("epub", [".epub"], _parse_epub)
ParserRegistry.register("rtf", [".rtf"], _parse_rtf)
ParserRegistry.register("csv", [".csv"], _parse_csv)
ParserRegistry.register("xlsx", [".xlsx"], _parse_xlsx)
ParserRegistry.register("pptx", [".pptx"], _parse_pptx)
ParserRegistry.register("odt", [".odt"], _parse_odt)
