"""Ebook library for the /millsandgoons reader.

A small on-disk library: one directory per book holding `book.json` (title,
author, and the block list the reader renders) plus a `media/` folder for
images lifted out of uploaded documents. No database tables, no new
dependencies — .docx is unzipped and parsed with the stdlib.

Content is stored as blocks so a document's pictures keep their place in the
flow:

    {"t": "h",   "x": "Chapter 1"}          heading (chapter break)
    {"t": "p",   "x": "It was a dark…"}     paragraph
    {"t": "img", "x": "image1.png", ...}    image in media/
"""

import json
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from io import BytesIO
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

# Browsers can't draw EMF/WMF, and Word embeds those for drawings/charts.
_IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
              ".svg": "image/svg+xml"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_V = "{urn:schemas-microsoft-com:vml}"


# ---------------------------------------------------------------- helpers

def slugify(title: str) -> str:
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:60] or "book"


def _is_heading(line: str) -> bool:
    return len(line) <= _MAX_HEADING_LEN and bool(_HEADING_RE.match(line))


def media_type(name: str) -> str | None:
    return _IMAGE_EXT.get(Path(name).suffix.lower())


# ------------------------------------------------------------ text source

def parse_text(text: str) -> list[dict]:
    """Plain text (or markdown-ish) → blocks. Blank lines split paragraphs;
    single newlines inside a paragraph are joined with a space."""
    blocks: list[dict] = []
    para: list[str] = []

    def flush():
        if para:
            blocks.append({"t": "p", "x": " ".join(para)})
            para.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if _is_heading(line):
            flush()
            blocks.append({"t": "h", "x": line.lstrip("#").strip()})
            continue
        para.append(line)
    flush()
    return blocks


# ------------------------------------------------------------ docx source

def _docx_rels(zf: zipfile.ZipFile) -> dict[str, str]:
    try:
        xml = zf.read("word/_rels/document.xml.rels")
    except KeyError:
        return {}
    rels = {}
    for rel in ET.fromstring(xml):
        rid, target = rel.get("Id"), rel.get("Target", "")
        if rid and target and "image" in rel.get("Type", ""):
            rels[rid] = target
    return rels


def _para_text(p: ET.Element) -> str:
    out = []
    for node in p.iter():
        if node.tag == _W + "t":
            out.append(node.text or "")
        elif node.tag in (_W + "tab",):
            out.append(" ")
        elif node.tag in (_W + "br", _W + "cr"):
            out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _para_image_ids(p: ET.Element) -> list[str]:
    """Relationship ids of pictures anchored in this paragraph (DrawingML
    first, then the legacy VML shapes older documents still use)."""
    ids = []
    for blip in p.iter(_A + "blip"):
        rid = blip.get(_R + "embed") or blip.get(_R + "link")
        if rid:
            ids.append(rid)
    for img in p.iter(_V + "imagedata"):
        rid = img.get(_R + "id")
        if rid:
            ids.append(rid)
    return ids


def _is_docx_heading(p: ET.Element) -> bool:
    style = p.find(f"{_W}pPr/{_W}pStyle")
    val = (style.get(_W + "val") if style is not None else "") or ""
    v = val.lower()
    return v.startswith("heading") or v in ("title", "subtitle")


def parse_docx(data: bytes) -> tuple[list[dict], dict[str, bytes]]:
    """.docx → (blocks, {media filename: bytes}).

    Headings come from Word's own heading styles, falling back to the same
    text heuristic used for pasted text. Pictures keep their position in the
    flow; formats browsers can't draw (EMF/WMF) are dropped.
    """
    try:
        zf = zipfile.ZipFile(BytesIO(data))
        document = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("Not a readable .docx file") from exc

    rels = _docx_rels(zf)
    body = ET.fromstring(document).find(_W + "body")
    blocks: list[dict] = []
    media: dict[str, bytes] = {}
    seen: dict[str, str] = {}   # zip entry -> stored filename (dedupe reuse)
    n = 0

    for p in (body.iter(_W + "p") if body is not None else []):
        for rid in _para_image_ids(p):
            target = rels.get(rid)
            if not target:
                continue
            entry = "word/" + target.lstrip("/") if not target.startswith("word/") else target
            entry = entry.replace("/../", "/")
            if entry in seen:
                blocks.append({"t": "img", "x": seen[entry]})
                continue
            if not media_type(entry):
                continue
            try:
                raw = zf.read(entry)
            except KeyError:
                continue
            if not raw or len(raw) > MAX_IMAGE_BYTES:
                continue
            n += 1
            name = f"img{n:03d}{Path(entry).suffix.lower()}"
            media[name] = raw
            seen[entry] = name
            blocks.append({"t": "img", "x": name})

        text = _para_text(p)
        if not text:
            continue
        if _is_docx_heading(p) or _is_heading(text):
            blocks.append({"t": "h", "x": text.lstrip("#").strip()})
        else:
            blocks.append({"t": "p", "x": text})

    return blocks, media


def parse_upload(filename: str, data: bytes) -> tuple[list[dict], dict[str, bytes]]:
    """Dispatch an uploaded file to the right parser."""
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".docx":
        return parse_docx(data)
    if suffix in ("", ".txt", ".text", ".md", ".markdown"):
        return parse_text(data.decode("utf-8", errors="replace")), {}
    if suffix == ".doc":
        raise ValueError("Legacy .doc isn't supported — save it as .docx first")
    raise ValueError(f"Unsupported file type '{suffix}' — use .docx, .txt or .md")


# ----------------------------------------------------------- presentation

def prepare(blocks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Number the blocks for the reader and pull out the chapter list.

    Every block gets `i` (a stable index the reader uses to remember where
    you were); headings also get `ch`, their chapter ordinal.
    """
    out, chapters = [], []
    for i, b in enumerate(blocks):
        item = dict(b, i=i)
        if b.get("t") == "h":
            item["ch"] = len(chapters)
            chapters.append({"title": b.get("x", ""), "i": i})
        out.append(item)
    return out, chapters


# Average adult silent-reading speed for prose; images get a token 12s each
# the way article estimators do, so a picture-heavy book isn't understated.
WORDS_PER_MINUTE = 220


def reading_time(words: int, images: int = 0) -> str:
    """Human reading estimate: "9 min", "1 hr", "2 hr 15 min"."""
    minutes = max(1, round(words / WORDS_PER_MINUTE + images * 0.2))
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} hr" if rest == 0 else f"{hours} hr {rest} min"


def word_count(blocks: list[dict]) -> int:
    return sum(len(b.get("x", "").split()) for b in blocks if b.get("t") in ("p", "h"))


def image_count(blocks: list[dict]) -> int:
    return sum(1 for b in blocks if b.get("t") == "img")


def blocks_to_text(blocks: list[dict]) -> str:
    """Round-trip blocks back to editable plain text. Headings are marked
    `# …` and images `[image: …]` so nothing is silently lost or promoted
    when the edited text is parsed again."""
    parts = []
    for b in blocks:
        if b["t"] == "h":
            parts.append("# " + b["x"])
        elif b["t"] == "p":
            parts.append(b["x"])
        else:
            parts.append(f"[image: {b['x']}]")
    return "\n\n".join(parts)


_IMG_LINE_RE = re.compile(r"^\[image:\s*(.+?)\s*\]$")


def parse_text_with_images(text: str, known: set[str]) -> list[dict]:
    """Like parse_text, but `[image: name]` lines that name an image the book
    already has become image blocks again."""
    blocks = []
    for chunk in re.split(r"\n\s*\n", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _IMG_LINE_RE.match(chunk)
        if m and m.group(1) in known:
            blocks.append({"t": "img", "x": m.group(1)})
        else:
            blocks.extend(parse_text(chunk))
    return blocks



# ------------------------------------------------------------- paid unlock

DEFAULT_TELEGRAM = "onepunchcall"


def split_free(blocks: list[dict], free_chapters: int) -> tuple[list[dict], list[str]]:
    """(blocks a non-paying reader may see, titles of the locked chapters).

    `free_chapters` counts chapters from the top; anything before the first
    heading is front matter and always free. A book with no more chapters
    than the allowance isn't gated at all, so nothing is withheld.
    """
    if free_chapters <= 0:
        return blocks, []
    heads = [i for i, b in enumerate(blocks) if b.get("t") == "h"]
    if len(heads) <= free_chapters:
        return blocks, []
    cut = heads[free_chapters]
    return blocks[:cut], [blocks[i]["x"] for i in heads[free_chapters:]]


def cover_src(book: dict) -> str:
    """Path (below the reader's base) of this book's cover, or "" for none."""
    slug = book.get("slug", "")
    if book.get("cover_file"):
        return f"/b/{slug}/cover?v={book.get('cover_v', 0)}"
    first = next((b["x"] for b in book.get("blocks", []) if b.get("t") == "img"), "")
    return f"/b/{slug}/media/{first}" if first else ""


# ----------------------------------------------------------------- store

class Library:
    """Books on disk under `root`; `root=None` keeps everything in memory."""

    def __init__(self, root: str | None):
        self._root = Path(root) if root else None
        if self._root:
            self._root.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, dict] = {}
        self._mem_media: dict[str, dict[str, bytes]] = {}
        self._mem_settings: dict = {}

    # -- paths

    def _dir(self, slug: str) -> Path:
        return self._root / slug

    def media_path(self, slug: str, name: str) -> Path | None:
        """Resolved path of one image, or None if it escapes the book's
        media directory or doesn't exist."""
        if self._root is None or not _safe_name(slug) or not _safe_name(name):
            return None
        p = (self._dir(slug) / "media" / name).resolve()
        if not str(p).startswith(str((self._dir(slug) / "media").resolve())):
            return None
        return p if p.is_file() else None

    # -- reads

    def get(self, slug: str) -> dict | None:
        if not _safe_name(slug):
            return None
        if self._root is None:
            book = self._mem.get(slug)
            return dict(book) if book else None
        try:
            return json.loads((self._dir(slug) / "book.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def books(self) -> list[dict]:
        """Every book's metadata, newest first (blocks omitted)."""
        out = []
        slugs = (sorted(self._mem) if self._root is None
                 else sorted(p.name for p in self._root.iterdir() if p.is_dir()))
        for slug in slugs:
            book = self.get(slug)
            if not book:
                continue
            blocks = book.get("blocks", [])
            words, images = word_count(blocks), image_count(blocks)
            out.append({
                "slug": book.get("slug", slug), "title": book.get("title", slug),
                "author": book.get("author", ""), "added": book.get("added", ""),
                "cover_src": cover_src(book),
                "words": words, "images": images,
                "read_time": reading_time(words, images),
                "chapters": sum(1 for b in blocks if b.get("t") == "h"),
                "free_chapters": book.get("free_chapters", 0),
                "unlocked": bool(book.get("unlocked")),
                "gated": bool(split_free(blocks, book.get("free_chapters", 0))[1]),
            })
        out.sort(key=lambda b: b["added"], reverse=True)
        return out

    def media_names(self, slug: str) -> set[str]:
        book = self.get(slug) or {}
        return {b["x"] for b in book.get("blocks", []) if b.get("t") == "img"}

    # -- writes

    def save(self, slug: str, title: str, author: str, blocks: list[dict],
             media: dict[str, bytes] | None = None, added: str | None = None,
             free_chapters: int | None = None, terms: str | None = None,
             unlocked: bool | None = None) -> str:
        """Write a book. `media` replaces the book's images when given; pass
        None to leave existing images alone (a metadata-only edit). The
        paywall fields work the same way — None keeps what's stored."""
        if not _safe_name(slug):
            raise ValueError("Bad book id")
        old = self.get(slug) or {}
        book = {
            "slug": slug, "title": title.strip() or "Untitled",
            "author": author.strip(), "blocks": blocks,
            # an uploaded cover outlives content edits; otherwise the first
            # picture in the book stands in for one
            "cover_file": old.get("cover_file", ""),
            "cover_v": old.get("cover_v", 0),
            "added": added or old.get("added")
                     or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "free_chapters": max(0, int(old.get("free_chapters", 0)
                                        if free_chapters is None else free_chapters)),
            "terms": (old.get("terms", "") if terms is None else terms).strip(),
            "unlocked": bool(old.get("unlocked", False) if unlocked is None else unlocked),
        }
        if self._root is None:
            if media is not None:
                self._mem_media[slug] = dict(media)
            self._write(slug, book)
            return slug

        d = self._dir(slug)
        d.mkdir(parents=True, exist_ok=True)
        if media is not None:
            mdir = d / "media"
            shutil.rmtree(mdir, ignore_errors=True)
            if media:
                mdir.mkdir(parents=True, exist_ok=True)
                for name, raw in media.items():
                    if _safe_name(name) and media_type(name):
                        (mdir / name).write_bytes(raw)
        self._write(slug, book)
        return slug

    def _write(self, slug: str, book: dict) -> None:
        if self._root is None:
            self._mem[slug] = book
            return
        d = self._dir(slug)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "book.json.tmp"
        tmp.write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
        tmp.replace(d / "book.json")

    def unique_slug(self, title: str, keep: str | None = None) -> str:
        base = slugify(title)
        slug, n = base, 1
        while slug != keep and self.get(slug) is not None:
            n += 1
            slug = f"{base}-{n}"
        return slug

    def delete(self, slug: str) -> bool:
        if not _safe_name(slug):
            return False
        if self._root is None:
            self._mem_media.pop(slug, None)
            return self._mem.pop(slug, None) is not None
        d = self._dir(slug)
        if not d.is_dir():
            return False
        shutil.rmtree(d, ignore_errors=True)
        return True

    # -- cover image (uploaded separately, so replacing the text keeps it)

    def cover_path(self, slug: str) -> Path | None:
        book = self.get(slug) or {}
        name = book.get("cover_file")
        if not name or self._root is None or not _safe_name(slug) or not _safe_name(name):
            return None
        p = self._dir(slug) / name
        return p if p.is_file() else None

    def set_cover(self, slug: str, filename: str, data: bytes) -> None:
        """Store an uploaded cover. Replaces any previous one."""
        ext = Path(filename or "").suffix.lower()
        if not media_type("x" + ext):
            raise ValueError("Cover must be a PNG, JPEG, GIF, WEBP or SVG image")
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Cover image is empty or too large")
        book = self.get(slug)
        if not book:
            raise ValueError("No such book")
        name = "cover" + ext
        if self._root is not None:
            d = self._dir(slug)
            d.mkdir(parents=True, exist_ok=True)
            for stale in d.glob("cover.*"):        # a different extension
                stale.unlink(missing_ok=True)
            (d / name).write_bytes(data)
        book["cover_file"] = name
        book["cover_v"] = int(book.get("cover_v", 0)) + 1
        self._write(slug, book)

    def clear_cover(self, slug: str) -> None:
        book = self.get(slug)
        if not book or not book.get("cover_file"):
            return
        if self._root is not None:
            for stale in self._dir(slug).glob("cover.*"):
                stale.unlink(missing_ok=True)
        book["cover_file"] = ""
        self._write(slug, book)

    # -- library-wide settings

    def settings(self) -> dict:
        """Telegram handle to sell through, and the terms shown when a book
        doesn't set its own."""
        raw = {}
        if self._root is None:
            raw = dict(self._mem_settings)
        else:
            try:
                raw = json.loads((self._root / "settings.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
        return {"telegram": (raw.get("telegram") or DEFAULT_TELEGRAM).lstrip("@"),
                "terms": raw.get("terms", "")}

    def save_settings(self, telegram: str, terms: str) -> None:
        data = {"telegram": (telegram or DEFAULT_TELEGRAM).strip().lstrip("@")
                            or DEFAULT_TELEGRAM,
                "terms": terms.strip()}
        if self._root is None:
            self._mem_settings = data
            return
        tmp = self._root / "settings.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._root / "settings.json")

    def unlock_terms(self, book: dict) -> str:
        """What the gate should say for this book."""
        return book.get("terms") or self.settings()["terms"]

    # -- one-time migration from the original single-book file

    def migrate_single_book(self, path: str | None) -> None:
        if not path or self._root is None:
            return
        p = Path(path)
        if not p.is_file():
            return
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        title = old.get("title") or "Mills & Goons"
        slug = self.unique_slug(title)
        self.save(slug, title, "", parse_text(old.get("text", "")), media={})
        p.rename(p.with_suffix(".json.migrated"))


def _safe_name(name: str) -> bool:
    return bool(name) and bool(re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name)) \
        and not name.startswith(".")
