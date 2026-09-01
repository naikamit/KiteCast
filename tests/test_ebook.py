"""Ebook module: chapter parsing, store round-trip, reader/admin routes."""

import pytest
from fastapi.testclient import TestClient

from kitecast.app import build_app
from kitecast.config import settings as app_settings
from kitecast.ebook import EbookStore, parse_chapters, word_count


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

# The Interval

Something happened.
"""


def test_parse_chapters_headings_and_paragraphs():
    chapters = parse_chapters(SAMPLE)
    assert [c["title"] for c in chapters] == ["Prologue", "Chapter 1", "The Interval"]
    ch1 = chapters[1]
    # single newlines join into one paragraph; blank lines split
    assert ch1["paragraphs"] == [
        "It was a dark and stormy night. The rain fell in sheets.",
        "New paragraph here.",
    ]


def test_parse_chapters_no_headings_is_one_chapter():
    chapters = parse_chapters("Just some prose.\n\nMore prose.")
    assert len(chapters) == 1
    assert chapters[0]["title"] == ""
    assert chapters[0]["paragraphs"] == ["Just some prose.", "More prose."]


def test_parse_chapters_long_chaptery_line_is_body_text():
    line = "Chapter meetings were held every Tuesday " + "x" * 60
    chapters = parse_chapters(line)
    assert chapters[0]["paragraphs"] == [line]


def test_store_roundtrip(tmp_path):
    store = EbookStore(str(tmp_path / "book.json"))
    assert store.load() is None
    store.save("  My Book  ", "hello world")
    assert store.load() == {"title": "My Book", "text": "hello world"}
    assert word_count("hello world") == 2


def test_reader_empty_then_admin_upload(client):
    r = client.get("/millsandgoons")
    assert r.status_code == 200
    assert "No book yet" in r.text

    assert client.get("/millsandgoonsadmin").status_code == 200

    r = client.post("/millsandgoonsadmin",
                    data={"title": "Mills & Goons", "text": SAMPLE})
    assert r.status_code == 200
    assert "Book saved." in r.text

    r = client.get("/millsandgoons")
    assert r.status_code == 200
    assert "dark and stormy" in r.text
    assert "Prologue" in r.text


def test_admin_file_upload_overrides_textarea(client):
    r = client.post("/millsandgoonsadmin",
                    data={"title": "T", "text": "ignored"},
                    files={"textfile": ("book.txt", b"Chapter 1\n\nFrom the file.",
                                        "text/plain")})
    assert r.status_code == 200
    assert client.get("/millsandgoons").text.count("From the file.") == 1


def test_admin_rejects_empty_text(client):
    assert client.post("/millsandgoonsadmin",
                       data={"title": "T", "text": "   "}).status_code == 400
