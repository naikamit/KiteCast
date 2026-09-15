"""Ebook library: text/docx parsing, the on-disk store, reader + admin routes."""

import base64
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from kitecast.app import build_app
from kitecast.config import settings as app_settings
from kitecast.ebook import (Library, blocks_to_text, cover_src, parse_docx, parse_text,
                            parse_text_with_images, parse_upload, prepare, reading_time,
                            slugify, split_free, word_count)

# 1x1 pixels, enough for a real image byte-stream through the whole path.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
JPG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")

_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
       'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
       'xmlns:v="urn:schemas-microsoft-com:vml"')


def _drawing(rid):
    return (f'<w:r><w:drawing><a:graphic><a:graphicData>'
            f'<a:blip r:embed="{rid}"/></a:graphicData></a:graphic></w:drawing></w:r>')


DOC_BODY = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document {_NS}><w:body>
  <w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>Mills and Goons</w:t></w:r></w:p>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>The Storm</w:t></w:r></w:p>
  <w:p><w:r><w:t>It was a dark</w:t></w:r><w:r><w:t xml:space="preserve"> and stormy</w:t></w:r>
      <w:r><w:tab/><w:t>night.</w:t></w:r></w:p>
  <w:p/>
  <w:p>{_drawing("rId5")}</w:p>
  <w:p><w:r><w:t>Chapter 2</w:t></w:r></w:p>
  <w:p><w:r><w:t>The goons regrouped.</w:t></w:r></w:p>
  <w:p><w:r><w:pict><v:shape><v:imagedata r:id="rId6"/></v:shape></w:pict></w:r></w:p>
  <w:p>{_drawing("rId7")}</w:p>
  <w:p>{_drawing("rId5")}</w:p>
</w:body></w:document>"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/photo.png"/>
  <Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/old.jpg"/>
  <Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/chart.emf"/>
  <Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def make_docx(body: str = DOC_BODY, rels: str = DOC_RELS) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", body)
        zf.writestr("word/_rels/document.xml.rels", rels)
        zf.writestr("word/media/photo.png", PNG)
        zf.writestr("word/media/old.jpg", JPG)
        zf.writestr("word/media/chart.emf", b"\x01\x00\x00\x00emf junk")
    return buf.getvalue()


@pytest.fixture
def client(ledger, kite, telegram, settings, monkeypatch):
    monkeypatch.setattr(app_settings, "db_path", settings.db_path)
    monkeypatch.setattr(app_settings, "base_url", settings.base_url)
    return TestClient(build_app(ledger=ledger, kite=kite, telegram=telegram))


SAMPLE = """\
Prologue

Before the beginning there was tea.

Chapter 1

It was a dark and stormy night.
The rain fell in sheets.

New paragraph here.
"""


# ---------------------------------------------------------------- parsing

def test_parse_text_headings_and_paragraphs():
    blocks = parse_text(SAMPLE)
    assert [b["x"] for b in blocks if b["t"] == "h"] == ["Prologue", "Chapter 1"]
    # single newlines join into one paragraph; blank lines split
    assert blocks[3]["x"] == "It was a dark and stormy night. The rain fell in sheets."
    assert blocks[4]["x"] == "New paragraph here."


def test_parse_text_long_chaptery_line_is_body_text():
    line = "Chapter meetings were held every Tuesday " + "x" * 60
    assert parse_text(line) == [{"t": "p", "x": line}]


def test_parse_docx_text_headings_and_images():
    blocks, media = parse_docx(make_docx())

    assert [b["x"] for b in blocks if b["t"] == "h"] == [
        "Mills and Goons",   # Title style
        "The Storm",         # Heading1 style
        "Chapter 2",         # unstyled, caught by the text heuristic
    ]
    assert blocks[2] == {"t": "p", "x": "It was a dark and stormy night."}

    # images keep their place in the flow, EMF is dropped, repeats reuse one file
    imgs = [b["x"] for b in blocks if b["t"] == "img"]
    assert imgs == ["img001.png", "img002.jpg", "img001.png"]
    assert set(media) == {"img001.png", "img002.jpg"}
    assert media["img001.png"] == PNG
    assert blocks[3]["t"] == "img" and blocks[4]["x"] == "Chapter 2"


def test_parse_docx_rejects_junk():
    with pytest.raises(ValueError):
        parse_docx(b"this is not a zip file")


def test_parse_upload_dispatch_and_errors():
    blocks, media = parse_upload("book.txt", b"Chapter 1\n\nHello.")
    assert [b["t"] for b in blocks] == ["h", "p"] and media == {}

    blocks, media = parse_upload("book.docx", make_docx())
    assert media and any(b["t"] == "img" for b in blocks)

    with pytest.raises(ValueError, match="save it as .docx"):
        parse_upload("old.doc", b"\xd0\xcf\x11\xe0")
    with pytest.raises(ValueError, match="Unsupported"):
        parse_upload("book.pdf", b"%PDF-")


def test_prepare_indexes_blocks_and_lists_chapters():
    blocks, chapters = prepare(parse_text(SAMPLE))
    assert [b["i"] for b in blocks] == list(range(len(blocks)))
    assert [c["title"] for c in chapters] == ["Prologue", "Chapter 1"]
    assert [b["ch"] for b in blocks if b["t"] == "h"] == [0, 1]


def test_text_round_trip_keeps_images():
    blocks = [{"t": "h", "x": "One"}, {"t": "p", "x": "Prose."},
              {"t": "img", "x": "img001.png"}]
    text = blocks_to_text(blocks)
    assert "[image: img001.png]" in text and "# One" in text
    # headings survive even when their wording isn't chapter-shaped
    assert parse_text_with_images(text, {"img001.png"}) == blocks
    # a placeholder naming an image the book doesn't have stays plain text
    assert parse_text_with_images(text, set())[2]["t"] == "p"


def test_reading_time_reads_like_a_person_would_say_it():
    assert reading_time(0) == "1 min"          # never "0 min"
    assert reading_time(880) == "4 min"
    assert reading_time(13_200) == "1 hr"      # exact hours drop the minutes
    assert reading_time(60_000) == "4 hr 33 min"
    # pictures add a little browsing time
    assert reading_time(880, images=10) == "6 min"


def test_slugify():
    assert slugify("Mills & Goons!") == "mills-goons"
    assert slugify("...") == "book"


# ------------------------------------------------------------------ store

def test_library_save_list_get_delete(tmp_path):
    lib = Library(str(tmp_path / "ebooks"))
    assert lib.books() == []

    blocks = parse_text(SAMPLE)
    lib.save("mills", "Mills & Goons", "A. Nonymous", blocks, {"img001.png": PNG})
    book = lib.get("mills")
    assert book["title"] == "Mills & Goons" and book["author"] == "A. Nonymous"
    assert book["blocks"] == blocks

    listed = lib.books()
    assert len(listed) == 1 and listed[0]["words"] == word_count(blocks)
    assert listed[0]["chapters"] == 2

    assert lib.media_path("mills", "img001.png").read_bytes() == PNG
    assert lib.delete("mills") is True
    assert lib.get("mills") is None and lib.books() == []


def test_library_unique_slug_and_ordering(tmp_path):
    lib = Library(str(tmp_path / "ebooks"))
    first = lib.unique_slug("Mills & Goons")
    lib.save(first, "Mills & Goons", "", parse_text("One."), {}, added="2026-01-01T00:00:00")
    second = lib.unique_slug("Mills & Goons")
    assert (first, second) == ("mills-goons", "mills-goons-2")
    lib.save(second, "Mills & Goons", "", parse_text("Two."), {}, added="2026-02-01T00:00:00")
    assert [b["slug"] for b in lib.books()] == [second, first]   # newest first
    # editing keeps a book's own slug
    assert lib.unique_slug("Mills & Goons", keep=first) == first


def test_library_media_path_rejects_traversal(tmp_path):
    lib = Library(str(tmp_path / "ebooks"))
    lib.save("mills", "M", "", parse_text("Hi."), {"img001.png": PNG})
    (tmp_path / "ebooks" / "secret.txt").write_text("nope")
    assert lib.media_path("mills", "../../secret.txt") is None
    assert lib.media_path("mills", "missing.png") is None
    assert lib.get("../etc") is None


def test_library_metadata_edit_keeps_images(tmp_path):
    lib = Library(str(tmp_path / "ebooks"))
    blocks = [{"t": "p", "x": "Hi."}, {"t": "img", "x": "img001.png"}]
    lib.save("mills", "M", "", blocks, {"img001.png": PNG})
    lib.save("mills", "Renamed", "Author", blocks, media=None)   # None = leave media alone
    assert lib.get("mills")["title"] == "Renamed"
    assert lib.media_path("mills", "img001.png") is not None


def test_migrates_the_original_single_book_file(tmp_path):
    old = tmp_path / "ebook_millsandgoons.json"
    old.write_text('{"title": "Mills & Goons", "text": "Chapter 1\\n\\nOnce."}')
    lib = Library(str(tmp_path / "ebooks"))
    lib.migrate_single_book(str(old))

    books = lib.books()
    assert [b["title"] for b in books] == ["Mills & Goons"]
    assert lib.get(books[0]["slug"])["blocks"][0] == {"t": "h", "x": "Chapter 1"}
    assert not old.exists()          # renamed aside so it can't re-import
    lib.migrate_single_book(str(old))
    assert len(lib.books()) == 1


# ----------------------------------------------------------------- routes

def test_library_page_empty_then_populated(client):
    r = client.get("/millsandgoons")
    assert r.status_code == 200 and "No books yet" in r.text

    r = client.post("/millsandgoonsadmin", data={"title": "Mills & Goons",
                                                 "author": "A. Nonymous", "text": SAMPLE})
    assert r.status_code == 200          # redirect followed
    r = client.get("/millsandgoons")
    assert "A. Nonymous" in r.text
    assert '/millsandgoons/b/mills-goons"' in r.text
    # the cover carries the title; underneath is chapters and reading time
    assert "2 chapters ·" in r.text and "1 min" in r.text


def test_reader_pages_never_link_to_the_admin_panel(client):
    """The admin URL is the only thing keeping strangers out — no page a
    reader can reach may advertise it."""
    client.post("/millsandgoonsadmin", data={"title": "Mills & Goons", "text": SAMPLE})
    for url in ("/millsandgoons", "/millsandgoons/b/mills-goons"):
        assert "millsandgoonsadmin" not in client.get(url).text, url


def test_reader_renders_text_and_chapters(client):
    client.post("/millsandgoonsadmin", data={"title": "Mills & Goons", "text": SAMPLE})
    r = client.get("/millsandgoons/b/mills-goons")
    assert r.status_code == 200
    assert "dark and stormy" in r.text
    assert 'data-ch="0"' in r.text and "Prologue" in r.text
    assert client.get("/millsandgoons/b/nope").status_code == 404


def test_docx_upload_serves_its_images_in_the_reader(client):
    r = client.post("/millsandgoonsadmin", data={"author": "Word"},
                    files={"bookfile": ("Mills and Goons.docx", make_docx(),
                                        "application/vnd.openxmlformats-officedocument"
                                        ".wordprocessingml.document")})
    assert r.status_code == 200

    slug = "mills-and-goons"          # no title given: taken from the filename
    reader = client.get(f"/millsandgoons/b/{slug}")
    assert f'/millsandgoons/b/{slug}/media/img001.png' in reader.text
    assert "The goons regrouped." in reader.text

    img = client.get(f"/millsandgoons/b/{slug}/media/img001.png")
    assert img.status_code == 200 and img.content == PNG
    assert img.headers["content-type"] == "image/png"
    assert client.get(f"/millsandgoons/b/{slug}/media/chart.emf").status_code == 404
    assert client.get(f"/millsandgoons/b/{slug}/media/..%2Fbook.json").status_code == 404


def test_title_falls_back_to_first_heading(client):
    client.post("/millsandgoonsadmin", data={"text": "Prologue\n\nSomething."})
    assert "Prologue" in client.get("/millsandgoons").text


def test_admin_edit_saves_and_deletes(client):
    client.post("/millsandgoonsadmin", data={"title": "Mills & Goons", "text": SAMPLE})
    r = client.get("/millsandgoonsadmin/b/mills-goons")
    assert r.status_code == 200 and "Prologue" in r.text

    r = client.post("/millsandgoonsadmin/b/mills-goons",
                    data={"title": "Renamed", "author": "New Author",
                          "text": "Chapter 9\n\nRewritten."})
    assert r.status_code == 200 and "Saved." in r.text
    reader = client.get("/millsandgoons/b/mills-goons").text
    assert "Rewritten." in reader and "Renamed" in reader and "New Author" in reader

    # blank text with no upload is a metadata-only edit, not a wipe
    client.post("/millsandgoonsadmin/b/mills-goons", data={"title": "Renamed II", "text": ""})
    assert "Rewritten." in client.get("/millsandgoons/b/mills-goons").text

    r = client.post("/millsandgoonsadmin/b/mills-goons/delete")
    assert r.status_code == 200
    assert client.get("/millsandgoons/b/mills-goons").status_code == 404
    assert "No books yet" in client.get("/millsandgoons").text


def test_admin_rejects_empty_and_unsupported(client):
    assert client.post("/millsandgoonsadmin", data={"title": "T", "text": "  "}).status_code == 400
    r = client.post("/millsandgoonsadmin", data={"title": "T"},
                    files={"bookfile": ("book.pdf", b"%PDF-", "application/pdf")})
    assert r.status_code == 400

# ---------------------------------------------------------------- paywall

GATED = ("Chapter 1\n\nFree words here.\n\nChapter 2\n\nPaid words here.\n\n"
         "Chapter 3\n\nMore paid words.")


def test_split_free_cuts_at_the_chapter_boundary():
    blocks = parse_text(GATED)
    free, locked = split_free(blocks, 1)
    assert [b["x"] for b in free] == ["Chapter 1", "Free words here."]
    assert locked == ["Chapter 2", "Chapter 3"]

    # front matter before the first heading stays with the free part
    front = parse_text("A note before we start.\n\n" + GATED)
    free, locked = split_free(front, 1)
    assert free[0]["x"] == "A note before we start."
    assert locked == ["Chapter 2", "Chapter 3"]


def test_split_free_no_gate_cases():
    blocks = parse_text(GATED)
    assert split_free(blocks, 0) == (blocks, [])      # 0 = whole book free
    assert split_free(blocks, 3) == (blocks, [])      # allowance covers the book
    assert split_free(blocks, 9) == (blocks, [])
    assert split_free(parse_text("No headings at all."), 1)[1] == []


def test_library_keeps_paywall_fields_across_metadata_edits(tmp_path):
    lib = Library(str(tmp_path / "ebooks"))
    blocks = parse_text(GATED)
    lib.save("g", "G", "", blocks, {}, free_chapters=1, terms="Rs 199")
    assert lib.get("g")["free_chapters"] == 1 and lib.get("g")["unlocked"] is False

    lib.save("g", "G2", "", blocks, None)              # rename only
    book = lib.get("g")
    assert (book["free_chapters"], book["terms"], book["unlocked"]) == (1, "Rs 199", False)

    lib.save("g", "G2", "", blocks, None, unlocked=True)
    assert lib.get("g")["unlocked"] is True


def test_library_settings_default_to_the_telegram_handle(tmp_path):
    lib = Library(str(tmp_path / "ebooks"))
    assert lib.settings() == {"telegram": "onepunchcall", "terms": ""}
    lib.save_settings("@someoneelse", "  Pay me  ")
    assert lib.settings() == {"telegram": "someoneelse", "terms": "Pay me"}
    lib.save_settings("", "x")
    assert lib.settings()["telegram"] == "onepunchcall"

    # a book's own terms win over the library default
    lib.save_settings("onepunchcall", "library terms")
    assert lib.unlock_terms({"terms": ""}) == "library terms"
    assert lib.unlock_terms({"terms": "book terms"}) == "book terms"


def _add_gated_book(client, free=1, terms="Rs 199 for the rest."):
    client.post("/millsandgoonsadmin", data={"title": "Gated", "text": GATED,
                                             "free_chapters": free, "terms": terms})
    return client.get("/millsandgoonsadmin").text


def test_paid_chapters_never_reach_a_locked_browser(client):
    _add_gated_book(client)
    page = client.get("/millsandgoons/b/gated").text
    assert "Free words here." in page
    assert "Paid words here." not in page and "More paid words." not in page
    # the gate names what's behind it, and points at Telegram
    assert "Chapter 2" in page and "https://t.me/onepunchcall" in page
    assert "Rs 199 for the rest." in page
    assert "2 more chapters behind this" in page


def test_shelf_marks_gated_books_locked(client):
    _add_gated_book(client)
    client.post("/millsandgoonsadmin", data={"title": "Open", "text": GATED,
                                             "free_chapters": 0})
    shelf = client.get("/millsandgoons").text
    assert shelf.count("free</span>") == 1           # only the gated one is chipped

    # and the chip clears once the book is opened
    client.post("/millsandgoonsadmin/b/gated",
                data={"title": "Gated", "free_chapters": 1, "unlocked": "1"})
    assert "free</span>" not in client.get("/millsandgoons").text


def test_admin_can_point_the_gate_at_another_telegram_handle(client):
    _add_gated_book(client)
    client.post("/millsandgoonsadmin/settings",
                data={"telegram": "@otherhandle", "terms": "Library terms"})
    assert "https://t.me/otherhandle" in client.get("/millsandgoons/b/gated").text


def test_admin_checkbox_opens_and_recloses_the_book(client):
    _add_gated_book(client)
    assert "Paid words here." not in client.get("/millsandgoons/b/gated").text

    client.post("/millsandgoonsadmin/b/gated",
                data={"title": "Gated", "free_chapters": 1, "unlocked": "1"})
    page = client.get("/millsandgoons/b/gated").text
    assert "Paid words here." in page and "More paid words." in page
    assert "Unlock on Telegram" not in page          # the gate is gone
    assert "unlocked" in client.get("/millsandgoonsadmin").text

    # unticking puts the gate back
    client.post("/millsandgoonsadmin/b/gated", data={"title": "Gated", "free_chapters": 1})
    assert "Paid words here." not in client.get("/millsandgoons/b/gated").text


def test_book_terms_override_library_terms(client):
    client.post("/millsandgoonsadmin/settings",
                data={"telegram": "onepunchcall", "terms": "Library terms"})
    client.post("/millsandgoonsadmin", data={"title": "Gated", "text": GATED,
                                             "free_chapters": 1, "terms": ""})
    assert "Library terms" in client.get("/millsandgoons/b/gated").text
    client.post("/millsandgoonsadmin/b/gated",
                data={"title": "Gated", "free_chapters": 1, "terms": "Just this book"})
    page = client.get("/millsandgoons/b/gated").text
    assert "Just this book" in page and "Library terms" not in page

# --------------------------------------------------------- the books domain

@pytest.fixture
def books_client(ledger, kite, telegram, settings, monkeypatch):
    """A client whose requests arrive on the public books domain."""
    monkeypatch.setattr(app_settings, "db_path", settings.db_path)
    monkeypatch.setattr(app_settings, "books_host", "millsandgoon.com")
    c = TestClient(build_app(ledger=ledger, kite=kite, telegram=telegram))
    c.headers.update({"host": "millsandgoon.com"})
    return c


def test_books_host_serves_the_library_at_the_root(books_client):
    books_client.post("/admin", data={"title": "Mills & Goons", "text": SAMPLE})
    root = books_client.get("/")
    assert root.status_code == 200
    assert "Mills &amp; Goons" in root.text
    # links are short ones, with no /millsandgoons prefix anywhere
    assert 'href="/b/mills-goons"' in root.text
    assert "millsandgoons" not in root.text

    reader = books_client.get("/b/mills-goons")
    assert reader.status_code == 200 and "dark and stormy" in reader.text
    assert "millsandgoonsadmin" not in reader.text


def test_books_host_has_no_kite_routes(books_client):
    for path in ("/board", "/friends", "/screenshot", "/api/instruments",
                 "/kite/postback", "/kite/redirect", "/auth/login", "/trade"):
        assert books_client.get(path).status_code == 404, path
    assert books_client.post("/trade", data={}).status_code == 404
    # the console never renders on this domain — / is the shelf
    assert "Place & Share" not in books_client.get("/").text


def test_books_host_admin_lives_at_slash_admin(books_client):
    assert books_client.get("/admin").status_code == 200
    r = books_client.post("/admin", data={"title": "Gated", "text": GATED,
                                          "free_chapters": 1}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin?")

    edit = books_client.get("/admin/b/gated")
    assert edit.status_code == 200
    assert 'action="/admin/b/gated"' in edit.text
    r = books_client.post("/admin/b/gated", data={"title": "Gated", "free_chapters": 1},
                          follow_redirects=False)
    assert r.headers["location"] == "/admin/b/gated?flash=Saved."


def test_books_host_gate_uses_short_paths(books_client):
    books_client.post("/admin", data={"title": "Gated", "text": GATED, "free_chapters": 1})
    page = books_client.get("/b/gated").text
    assert "Paid words here." not in page
    assert "https://t.me/onepunchcall" in page
    assert "millsandgoons" not in page
    books_client.post("/admin/b/gated", data={"title": "Gated", "free_chapters": 1,
                                              "unlocked": "1"})
    assert "Paid words here." in books_client.get("/b/gated").text


def test_books_host_serves_images_on_short_paths(books_client):
    books_client.post("/admin", data={"title": "Pics"},
                      files={"bookfile": ("p.docx", make_docx(), "application/octet-stream")})
    slug = "pics"
    assert f'/b/{slug}/media/img001.png' in books_client.get(f"/b/{slug}").text
    img = books_client.get(f"/b/{slug}/media/img001.png")
    assert img.status_code == 200 and img.content == PNG


def test_long_paths_still_work_on_the_books_host(books_client):
    """A link someone already shared shouldn't break."""
    books_client.post("/admin", data={"title": "Mills & Goons", "text": SAMPLE})
    assert books_client.get("/millsandgoons").status_code == 200
    assert books_client.get("/millsandgoons/b/mills-goons").status_code == 200


def test_other_hosts_keep_the_console_and_reject_short_paths(books_client):
    """Same app, reached on the onrender hostname."""
    books_client.post("/admin", data={"title": "Mills & Goons", "text": SAMPLE})
    other = {"host": "kitecast.onrender.com"}
    assert books_client.get("/millsandgoons", headers=other).status_code == 200
    assert books_client.get("/millsandgoonsadmin", headers=other).status_code == 200
    assert books_client.get("/board", headers=other).status_code == 200
    # the pretty paths belong to the books domain only
    assert books_client.get("/b/mills-goons", headers=other).status_code == 404
    assert books_client.get("/admin", headers=other).status_code == 404
    # and its pages link with the long prefix
    assert 'href="/millsandgoons/b/mills-goons"' in books_client.get(
        "/millsandgoons", headers=other).text


def test_www_and_port_forms_count_as_the_books_host(books_client):
    books_client.post("/admin", data={"title": "Mills & Goons", "text": SAMPLE})
    for host in ("www.millsandgoon.com", "MillsAndGoon.com", "millsandgoon.com:443"):
        r = books_client.get("/", headers={"host": host})
        assert r.status_code == 200 and "Mills &amp; Goons" in r.text, host


def test_without_books_host_nothing_changes(client):
    """The default deployment: one hostname, console at /, short paths gone."""
    client.post("/millsandgoonsadmin", data={"title": "Mills & Goons", "text": SAMPLE})
    assert client.get("/millsandgoons").status_code == 200
    assert client.get("/b/mills-goons").status_code == 404
    assert client.get("/admin").status_code == 404
    assert client.get("/board").status_code == 200

# ----------------------------------------------------------------- covers

GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def test_cover_src_prefers_the_upload_then_the_first_picture():
    assert cover_src({"slug": "b", "cover_file": "cover.png", "cover_v": 2}) \
        == "/b/b/cover?v=2"
    assert cover_src({"slug": "b", "blocks": [{"t": "p", "x": "hi"},
                                              {"t": "img", "x": "img001.png"}]}) \
        == "/b/b/media/img001.png"
    assert cover_src({"slug": "b", "blocks": []}) == ""


def test_library_cover_survives_a_content_replacement(tmp_path):
    lib = Library(str(tmp_path / "ebooks"))
    lib.save("b", "B", "", parse_text("Chapter 1\n\nOne."), {})
    lib.set_cover("b", "art.png", PNG)
    assert lib.cover_path("b").read_bytes() == PNG
    assert lib.get("b")["cover_v"] == 1

    # replacing the text (and therefore the media folder) leaves it alone
    lib.save("b", "B", "", parse_text("Chapter 1\n\nTwo."), {"img001.png": PNG})
    assert lib.cover_path("b").read_bytes() == PNG

    # a new upload in another format replaces the old file, not adds to it
    lib.set_cover("b", "art.gif", GIF)
    assert lib.get("b")["cover_file"] == "cover.gif"
    assert lib.cover_path("b").read_bytes() == GIF
    assert lib.get("b")["cover_v"] == 2          # bumped, so caches miss
    assert sorted(p.name for p in (tmp_path / "ebooks" / "b").glob("cover.*")) == ["cover.gif"]

    lib.clear_cover("b")
    assert lib.cover_path("b") is None and lib.get("b")["cover_file"] == ""


def test_library_rejects_a_cover_that_is_not_an_image(tmp_path):
    lib = Library(str(tmp_path / "ebooks"))
    lib.save("b", "B", "", parse_text("Hi."), {})
    with pytest.raises(ValueError, match="PNG"):
        lib.set_cover("b", "notes.pdf", b"%PDF-")
    with pytest.raises(ValueError, match="empty or too large"):
        lib.set_cover("b", "art.png", b"")


def test_admin_uploads_a_cover_and_the_shelf_shows_it(client):
    client.post("/millsandgoonsadmin",
                data={"title": "Mills & Goons", "text": SAMPLE},
                files={"coverfile": ("art.png", PNG, "image/png")})

    shelf = client.get("/millsandgoons").text
    assert "/millsandgoons/b/mills-goons/cover?v=1" in shelf

    img = client.get("/millsandgoons/b/mills-goons/cover")
    assert img.status_code == 200 and img.content == PNG
    assert img.headers["content-type"] == "image/png"


def test_admin_replaces_and_removes_a_cover(client):
    client.post("/millsandgoonsadmin", data={"title": "Mills & Goons", "text": SAMPLE})
    assert client.get("/millsandgoons/b/mills-goons/cover").status_code == 404

    client.post("/millsandgoonsadmin/b/mills-goons", data={"title": "Mills & Goons"},
                files={"coverfile": ("art.png", PNG, "image/png")})
    assert client.get("/millsandgoons/b/mills-goons/cover").content == PNG
    assert 'name="remove_cover"' in client.get("/millsandgoonsadmin/b/mills-goons").text

    client.post("/millsandgoonsadmin/b/mills-goons",
                data={"title": "Mills & Goons", "remove_cover": "1"})
    assert client.get("/millsandgoons/b/mills-goons/cover").status_code == 404


def test_admin_rejects_a_bad_cover_upload(client):
    client.post("/millsandgoonsadmin", data={"title": "Mills & Goons", "text": SAMPLE})
    r = client.post("/millsandgoonsadmin/b/mills-goons", data={"title": "Mills & Goons"},
                    files={"coverfile": ("notes.pdf", b"%PDF-", "application/pdf")})
    assert r.status_code == 400


def test_docx_first_picture_still_covers_a_book_with_no_upload(client):
    client.post("/millsandgoonsadmin", data={"title": "Pics"},
                files={"bookfile": ("p.docx", make_docx(), "application/octet-stream")})
    assert "/millsandgoons/b/pics/media/img001.png" in client.get("/millsandgoons").text
