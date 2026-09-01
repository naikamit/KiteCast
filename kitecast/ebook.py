"""Single-ebook store + chapter parser for the /millsandgoons reader.

One book, stored as a JSON file (title + raw text) next to the SQLite
ledger. The admin pastes or uploads plain text; chapters are detected from
heading-looking lines (markdown `#` headings, or lines like "Chapter 3",
"Prologue", "Part Two"). Everything else is MVP-simple on purpose — the
reader front end is where the effort goes.
"""

import json
import re
import threading
from pathlib import Path

# A line is a chapter heading if it's short and looks like one.
_HEADING_RE = re.compile(
    r"^(?:#{1,3}\s+\S.*"                       # markdown heading
    r"|(?:chapter|part|book)\s+\S+.*"          # Chapter 3 / Part Two: ...
    r"|prologue\b.*|epilogue\b.*|interlude\b.*|preface\b.*|afterword\b.*"
    r")$",
    re.IGNORECASE,
)
_MAX_HEADING_LEN = 80


class EbookStore:
    """Load/save the one book. `path=None` keeps it in memory (tests)."""

    def __init__(self, path: str | None):
        self._path = Path(path) if path else None
        self._mem: dict | None = None
        self._lock = threading.Lock()

    def load(self) -> dict | None:
        with self._lock:
            if self._path is None:
                return dict(self._mem) if self._mem else None
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None

    def save(self, title: str, text: str) -> None:
        book = {"title": title.strip() or "Untitled", "text": text}
        with self._lock:
            if self._path is None:
                self._mem = book
                return
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)


def _is_heading(line: str) -> bool:
    return len(line) <= _MAX_HEADING_LEN and bool(_HEADING_RE.match(line))


def _flush_paragraph(buf: list[str], out: list[str]) -> None:
    if buf:
        out.append(" ".join(buf))
        buf.clear()


def parse_chapters(text: str) -> list[dict]:
    """Split raw text into [{"title": str, "paragraphs": [str, ...]}, ...].

    Blank lines separate paragraphs; single newlines inside a paragraph are
    joined with a space. Text before the first heading becomes its own
    unnamed opening chapter; a book with no headings is one chapter.
    """
    chapters: list[dict] = []
    current: dict | None = None
    para: list[str] = []

    def ensure_chapter(title: str) -> dict:
        ch = {"title": title, "paragraphs": []}
        chapters.append(ch)
        return ch

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if current:
                _flush_paragraph(para, current["paragraphs"])
            continue
        if _is_heading(line):
            if current:
                _flush_paragraph(para, current["paragraphs"])
            current = ensure_chapter(line.lstrip("#").strip())
            continue
        if current is None:
            current = ensure_chapter("")
        para.append(line)
    if current:
        _flush_paragraph(para, current["paragraphs"])

    return [ch for ch in chapters if ch["paragraphs"] or ch["title"]]


def word_count(text: str) -> int:
    return len(text.split())
