"""The ear trainer: mastery storage, its API, and its own domain."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kitecast.app import build_app
from kitecast.config import settings as app_settings
from kitecast.ear import ALL, INTERVALS, LESSONS, Progress, lesson, safe_learner

EAR_DIR = Path(__file__).resolve().parent.parent / "ear"


@pytest.fixture
def client(ledger, kite, telegram, settings, monkeypatch):
    monkeypatch.setattr(app_settings, "db_path", settings.db_path)
    monkeypatch.setattr(app_settings, "base_url", settings.base_url)
    return TestClient(build_app(ledger=ledger, kite=kite, telegram=telegram))


@pytest.fixture
def ear_client(ledger, kite, telegram, settings, monkeypatch):
    """A client whose requests arrive on the trainer's own domain."""
    monkeypatch.setattr(app_settings, "db_path", settings.db_path)
    monkeypatch.setattr(app_settings, "ear_host", "ear.example.com")
    c = TestClient(build_app(ledger=ledger, kite=kite, telegram=telegram))
    c.headers.update({"host": "ear.example.com"})
    return c


# ------------------------------------------------------------------ the model

def test_every_lesson_only_names_real_intervals():
    for l in LESSONS:
        assert l.set, l.title
        assert l.mode in ("harmonic", "melodic")
        for i in l.set:
            assert i in INTERVALS, (l.title, i)


def test_the_ladder_covers_every_interval():
    assert set(ALL) == set(INTERVALS)
    assert set().union(*(set(l.set) for l in LESSONS)) == set(INTERVALS)
    assert [l.n for l in LESSONS] == list(range(1, 14))


def test_semitones_are_distinct_and_ordered():
    semis = [INTERVALS[i][0] for i in ALL]
    assert semis == sorted(semis) == list(range(1, 13))


def test_lesson_lookup():
    assert lesson(5).title == "Harmonic: Fourths and Fifths"
    assert lesson(99) is None


# --------------------------------------------------------------- the storage

def test_records_and_totals(tmp_path):
    p = Progress(str(tmp_path / "ear"))
    p.record("abc", "m3", correct=True, first_listen=True)
    p.record("abc", "m3", correct=False, first_listen=False)
    p.record("abc", "M6", correct=True, first_listen=False)
    s = p.summary("abc")
    assert s["seen"] == 3 and s["correct"] == 2 and s["first"] == 1
    assert s["intervals"]["m3"] == {"seen": 2, "correct": 1, "first": 1}
    assert s["intervals"]["P5"] == {"seen": 0, "correct": 0, "first": 0}


def test_first_hearing_only_counts_when_also_correct(tmp_path):
    p = Progress(str(tmp_path / "ear"))
    p.record("abc", "m3", correct=False, first_listen=True)
    assert p.summary("abc")["first"] == 0


def test_progress_survives_a_new_instance(tmp_path):
    root = str(tmp_path / "ear")
    Progress(root).record("abc", "TT", correct=True, first_listen=True)
    assert Progress(root).summary("abc")["intervals"]["TT"]["seen"] == 1


def test_learners_do_not_see_each_other(tmp_path):
    p = Progress(str(tmp_path / "ear"))
    p.record("alice", "m3", correct=True, first_listen=True)
    assert p.summary("bob")["seen"] == 0


def test_reset_clears_only_that_learner(tmp_path):
    p = Progress(str(tmp_path / "ear"))
    p.record("alice", "m3", correct=True, first_listen=True)
    p.record("bob", "m3", correct=True, first_listen=True)
    p.reset("alice")
    assert p.summary("alice")["seen"] == 0
    assert p.summary("bob")["seen"] == 1


def test_weakest_ignores_barely_drilled_intervals(tmp_path):
    p = Progress(str(tmp_path / "ear"))
    for _ in range(4):
        p.record("abc", "m6", correct=False, first_listen=False)
    for _ in range(4):
        p.record("abc", "P5", correct=True, first_listen=True)
    p.record("abc", "m2", correct=False, first_listen=False)   # only once
    weakest = p.summary("abc")["weakest"]
    assert weakest[0] == "m6"
    assert "m2" not in weakest


def test_weakest_never_names_an_interval_you_have_not_missed(tmp_path):
    p = Progress(str(tmp_path / "ear"))
    for _ in range(5):
        p.record("abc", "P5", correct=True, first_listen=True)
    for _ in range(5):
        p.record("abc", "m6", correct=False, first_listen=False)
    weakest = p.summary("abc")["weakest"]
    assert weakest == ["m6"]


def test_in_memory_mode_needs_no_disk():
    p = Progress(None)
    p.record("abc", "M7", correct=True, first_listen=True)
    assert p.summary("abc")["seen"] == 1


def test_corrupt_file_reads_as_empty(tmp_path):
    root = tmp_path / "ear"
    root.mkdir()
    (root / "abc.json").write_text("{not json")
    assert Progress(str(root)).summary("abc")["seen"] == 0


@pytest.mark.parametrize("token", ["", "../etc/passwd", "a/b", "a.b", "x" * 65])
def test_unsafe_learner_tokens_are_rejected(token):
    assert not safe_learner(token)


# ------------------------------------------------------------------- the API

def test_first_visit_issues_a_learner_cookie(client):
    r = client.get("/ear/")
    assert r.status_code == 200
    assert "Say the Interval" in r.text
    token = r.cookies.get("ear_learner")
    assert token and safe_learner(token)


def test_answers_accumulate_against_the_cookie(client):
    client.get("/ear/")
    for correct in (True, True, False):
        r = client.post("/ear/api/answer",
                        json={"interval": "m3", "correct": correct, "first_listen": correct})
        assert r.status_code == 200
    body = client.get("/ear/api/progress").json()
    assert body["seen"] == 3 and body["correct"] == 2
    assert body["intervals"]["m3"]["seen"] == 3


def test_unknown_interval_is_refused(client):
    client.get("/ear/")
    assert client.post("/ear/api/answer", json={"interval": "wat", "correct": True}).status_code == 400


def test_answer_without_a_learner_is_refused(client):
    assert client.post("/ear/api/answer",
                       json={"interval": "m3", "correct": True}).status_code == 400


def test_reset_empties_the_tally(client):
    client.get("/ear/")
    client.post("/ear/api/answer", json={"interval": "m3", "correct": True})
    client.post("/ear/api/reset")
    assert client.get("/ear/api/progress").json()["seen"] == 0


def test_lessons_endpoint_publishes_the_ladder(client):
    body = client.get("/ear/api/lessons").json()
    assert len(body["lessons"]) == 13
    assert body["lessons"][4]["title"] == "Harmonic: Fourths and Fifths"
    assert body["intervals"]["TT"]["semitones"] == 6


# ------------------------------------------------------- client/server drift

def _js_intervals() -> dict[str, tuple[int, str]]:
    src = (EAR_DIR / "intervals.js").read_text()
    block = src[src.index("const INTERVALS"):src.index("const ALL")]
    found = {}
    for m in re.finditer(r"(\w+):\s*\{\s*semi:\s*(\d+),\s*name:\s*'([^']+)'", block):
        found[m.group(1)] = (int(m.group(2)), m.group(3))
    return found


def _js_lessons() -> list[tuple[int, str, str, list[str]]]:
    src = (EAR_DIR / "intervals.js").read_text()
    block = src[src.index("const LESSONS"):src.index("/* Spoken forms")]
    out = []
    for m in re.finditer(
            r"\{\s*n:\s*(\d+),\s*title:\s*'([^']+)',\s*mode:\s*'(\w+)',\s*set:\s*(ALL|\[[^\]]*\])",
            block):
        raw = m.group(4)
        names = list(_js_intervals()) if raw == "ALL" else re.findall(r"'(\w+)'", raw)
        out.append((int(m.group(1)), m.group(2), m.group(3), names))
    return out


def test_the_browser_copy_of_the_interval_table_matches():
    """The drill carries its own table so it runs offline; this is what stops
    the two definitions drifting apart."""
    assert _js_intervals() == {i: (s, n) for i, (s, n) in INTERVALS.items()}


def test_the_browser_copy_of_the_ladder_matches():
    assert _js_lessons() == [(l.n, l.title, l.mode, list(l.set)) for l in LESSONS]


# ---------------------------------------------------------- the ear domain

def test_ear_domain_serves_the_trainer_at_the_root(ear_client):
    r = ear_client.get("/")
    assert r.status_code == 200 and "Say the Interval" in r.text


def test_ear_domain_folds_assets_under_the_prefix(ear_client):
    """The page uses relative paths, so /style.css has to reach /ear/style.css."""
    for path in ("/style.css", "/app.js", "/audio.js", "/intervals.js",
                 "/sw.js", "/manifest.webmanifest", "/icons/icon-512.png"):
        assert ear_client.get(path).status_code == 200, path


def test_ear_domain_has_no_other_app(ear_client):
    for path in ("/board", "/friends", "/trade", "/kite/postback",
                 "/millsandgoons", "/admin"):
        assert ear_client.get(path).status_code == 404, path


def test_ear_domain_api_still_works(ear_client):
    ear_client.get("/")
    assert ear_client.post("/api/answer",
                           json={"interval": "P5", "correct": True,
                                 "first_listen": True}).status_code == 200
    assert ear_client.get("/api/progress").json()["seen"] == 1


def test_long_ear_paths_still_work_on_its_own_domain(ear_client):
    assert ear_client.get("/ear/").status_code == 200


def test_without_ear_host_the_trainer_is_only_at_the_prefix(client):
    assert client.get("/ear/").status_code == 200
    assert client.get("/").status_code == 200        # console, not the trainer
    assert "Say the Interval" not in client.get("/").text
