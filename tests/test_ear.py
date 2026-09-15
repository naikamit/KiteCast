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


# --------------------------------------------- the self-echo loop (regression)
#
# The app's prompts name the lesson, and a lesson name is a navigation command,
# so on a phone — where the speaker reaches the microphone — it heard itself,
# jumped, re-announced and looped. These are tripwires on the guards that stop
# it; `ear/selfecho.test.js` reproduces the loop properly in a browser.

APP_JS = (EAR_DIR / "app.js").read_text()


def test_speaking_shuts_the_microphone():
    say = APP_JS[APP_JS.index("function say("):APP_JS.index("function isSelfEcho(")]
    assert "muteRecognition(true)" in say, (
        "say() must mute the recogniser, or the app hears its own prompts")


def test_the_gate_outlives_the_utterance():
    """A transcript arrives after speech ends; dropping the gate at onend is
    what let the tail of our own sentence through."""
    say = APP_JS[APP_JS.index("function say("):APP_JS.index("function isSelfEcho(")]
    assert "speakGate = setTimeout(" in say
    assert ", 700);" in say


def test_speech_synthesis_cannot_deafen_the_app():
    """If onend never fires the gate would stay shut for good."""
    assert "const bail = setTimeout(done," in APP_JS


def test_announcing_the_current_lesson_is_not_a_jump():
    jump = APP_JS[APP_JS.index("async function jumpTo("):]
    assert "if (lesson.n === State.lesson.n) return;" in jump


def test_the_lesson_is_never_announced():
    """It is on screen, so saying it is redundant — and that sentence is what
    the app used to hear itself say and loop on."""
    start = APP_JS[APP_JS.index("async function startSession()"):APP_JS.index("async function stopSession()")]
    jump = APP_JS[APP_JS.index("async function jumpTo("):APP_JS.index("/* ---------- transport")]
    for block, where in ((start, "startSession"), (jump, "jumpTo")):
        assert "say('Lesson" not in block and 'say("Lesson' not in block, where
        assert "lesson.title" not in block or "say(" not in block, where


def test_the_voice_is_unhurried():
    assert "const VOICE_RATE = 0.88;" in APP_JS
    assert "function say(text, rate = VOICE_RATE, tone)" in APP_JS


def test_the_voice_has_no_regional_accent():
    """en-US first, so en-GB / en-AU / en-IN voices fall to the back, and the
    utterance carries the language too in case no voice object was matched."""
    pick = APP_JS[APP_JS.index("function pickVoice()"):APP_JS.index("if (typeof speechSynthesis")]
    assert "^en[-_]us$" in pick
    assert "u.lang = 'en-US'" in APP_JS


def test_feedback_is_not_curt():
    assert "'Not quite. '" in APP_JS
    assert "'No. '" not in APP_JS


# ------------------------------------------------ transport and log (tripwires)
#
# Browser-level behaviour lives in ear/transport.test.js; these catch the two
# ways the log panel silently broke while it was being built.

STYLE_CSS = (EAR_DIR / "style.css").read_text()


def test_pause_stops_the_microphone():
    """Pause means stop listening — the transport button is how you come back."""
    pause = APP_JS[APP_JS.index("function pauseSession("):APP_JS.index("function resumeSession(")]
    assert "stopListening()" in pause
    resume = APP_JS[APP_JS.index("function resumeSession("):APP_JS.index("function onTransport(")]
    assert "startListening()" in resume


def test_on_device_recognition_is_only_used_when_confirmed():
    """Web Speech is cloud-backed by default. Guessing that an on-device model
    is present when it is not would break recognition outright."""
    block = APP_JS[APP_JS.index("async function preferOnDevice()"):APP_JS.index("function initRecognition()")]
    assert "availableOnDevice" in block
    assert "return state === 'available';" in block
    # and a model that claims to be there, then refuses, must not strand us
    assert "onDevice = false;" in APP_JS


def test_pause_stops_the_drill():
    for guard in ("async function askQuestion() {\n  if (!State.running || State.paused) return;",
                  "async function runListenMode() {\n  if (!State.running || State.paused) return;"):
        assert guard in APP_JS


def test_log_rows_are_namespaced_away_from_the_mic_indicator():
    """A bare .mic class would inherit the indicator's uppercase flex styling."""
    assert "li.className = 'k-' + kind;" in APP_JS
    assert ".log li.k-mic" in STYLE_CSS


def test_log_label_does_not_inherit_the_hanging_indent():
    """It is an inline-block, so it inherits text-indent and renders blank."""
    block = STYLE_CSS[STYLE_CSS.index(".log .k{"):]
    assert "text-indent:0" in block[:block.index("}")]


def test_the_log_never_reveals_the_interval_being_asked():
    play = APP_JS[APP_JS.index("function playCurrent()"):APP_JS.index("/* ---------- main loop")]
    assert "INTERVALS[q.id]" not in play and "truth.name" not in play


# ------------------------------------------------- screen off (commute case)

AUDIO_JS = (EAR_DIR / "audio.js").read_text()


def test_audio_is_routed_through_a_media_element():
    """A backgrounded page has its audio suspended and its timers throttled to
    once a minute. A page playing media is exempt, which is the only way the
    commute case works at all."""
    assert "createMediaStreamDestination" in AUDIO_JS
    assert "document.createElement('audio')" in AUDIO_JS
    assert "backgroundCapable" in AUDIO_JS


def test_a_silent_stream_would_not_count_as_playing():
    """Hence the infrasonic keepalive tone."""
    assert "keepalive" in AUDIO_JS


def test_screen_off_falls_back_to_listen_mode():
    """The microphone is gone when the screen locks, so a drill left running
    would sit there deaf."""
    assert "visibilitychange" in APP_JS
    assert "autoListen" in APP_JS
    assert "screen off — no microphone, switching to listen mode" in APP_JS


def test_the_lock_screen_gets_controls():
    assert "mediaSession" in APP_JS
    for action in ("'play'", "'pause'", "'nexttrack'", "'previoustrack'"):
        assert f"setActionHandler({action}" in APP_JS


# --------------------------------------------- the Android port (third copy)
#
# The interval table now exists in three places: Python (canonical), JavaScript
# (so the web drill runs offline) and Kotlin (so the phone does). No compiler
# is involved in these tests — they parse the Kotlin as text, which is exactly
# what catches a transcription slip.

ANDROID_SRC = (Path(__file__).resolve().parent.parent
               / "android/app/src/main/java/com/millsandgoon/ear")


def _kotlin_intervals():
    src = (ANDROID_SRC / "Intervals.kt").read_text()
    block = src[src.index("val INTERVALS"):src.index("val ALL")]
    return {m.group(1): (int(m.group(2)), m.group(3)) for m in
            re.finditer(r'"(\w+)" to Interval\((\d+), "([^"]+)"', block)}


def _kotlin_lessons():
    src = (ANDROID_SRC / "Intervals.kt").read_text()
    block = src[src.index("val LESSONS"):src.index("fun lessonOf")]
    out = []
    for m in re.finditer(
            r'Lesson\((\d+), "([^"]+)", Mode\.(\w+), (ALL|listOf\([^)]*\))\)', block):
        raw = m.group(4)
        names = list(_kotlin_intervals()) if raw == "ALL" else re.findall(r'"(\w+)"', raw)
        out.append((int(m.group(1)), m.group(2), m.group(3).lower(), names))
    return out


def test_the_kotlin_interval_table_matches():
    assert _kotlin_intervals() == {i: (s, n) for i, (s, n) in INTERVALS.items()}


def test_the_kotlin_ladder_matches():
    assert _kotlin_lessons() == [(l.n, l.title, l.mode, list(l.set)) for l in LESSONS]


def test_the_kotlin_synth_carries_the_measured_constants():
    """These numbers were measured, not chosen — a slip in transcription would
    quietly detune the app."""
    synth = (ANDROID_SRC / "Synth.kt").read_text()
    for constant in ("0.00015", "-1.25", "0.82", "2.4 /", "6.9078",
                     "0.34, 1.9, 0.11", "0.56, 3.1, 0.40", "0.55f"):
        assert constant in synth, constant


def test_the_phone_holds_the_microphone_with_the_screen_off():
    """The entire reason for the native build."""
    manifest = (ANDROID_SRC.parent.parent.parent.parent / "AndroidManifest.xml").read_text()
    assert "FOREGROUND_SERVICE_MICROPHONE" in manifest
    assert 'android:foregroundServiceType="microphone|mediaPlayback"' in manifest


def test_recognition_is_grammar_constrained_and_ignores_humming():
    src = (ANDROID_SRC / "Intervals.kt").read_text()
    assert "fun voskGrammar()" in src
    assert '"[unk]"' in src
    assert "[unk]" in (ANDROID_SRC / "Listener.kt").read_text()
