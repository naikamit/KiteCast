"""The ear trainer: mastery storage, its API, and its own domain."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kitecast.app import build_app
from kitecast.config import settings as app_settings
from kitecast.ear import (ALL, ALL_CHORDS, CHORDS, INTERVALS, LESSONS, Group, Kind,
                          Progress, every_item, lesson, safe_learner)

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

def test_every_lesson_is_playable():
    """What each lesson names is checked by kind further down; this is the
    shape of the lesson itself."""
    for l in LESSONS:
        assert l.set, l.title
        assert l.mode in ("harmonic", "melodic")
        assert len(set(l.set)) == len(l.set), l.title


def test_the_ladder_covers_everything_it_teaches():
    assert set(ALL) == set(INTERVALS)
    assert set(ALL_CHORDS) == set(CHORDS)
    assert set().union(*(set(l.set) for l in LESSONS)) == set(every_item())
    assert [l.n for l in LESSONS] == list(range(1, len(LESSONS) + 1))


def test_every_group_has_lessons_and_every_lesson_a_group():
    """The menu promises three categories, so all three must have content."""
    for g in Group:
        assert [l for l in LESSONS if l.group == g], g


def test_chord_lessons_hold_chords_and_interval_lessons_intervals():
    for l in LESSONS:
        source = CHORDS if l.kind == Kind.CHORD else INTERVALS
        for i in l.set:
            assert i in source, (l.title, i)


def test_chords_are_offsets_above_a_root():
    for cid, (offsets, _) in CHORDS.items():
        assert len(offsets) >= 2, cid
        assert list(offsets) == sorted(offsets), cid
        assert offsets[0] > 0, cid


def test_semitones_are_distinct_and_ordered():
    semis = [INTERVALS[i][0] for i in ALL]
    assert semis == sorted(semis) == list(range(1, 13))


def test_lesson_lookup():
    assert lesson(3).title == "Fourths and Fifths"
    assert lesson(3).group == Group.HARMONIC
    assert lesson(14).kind == Kind.CHORD
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
    assert len(body["lessons"]) == len(LESSONS)
    assert body["lessons"][2]["title"] == "Fourths and Fifths"
    assert body["lessons"][2]["group"] == "harmonic"
    assert body["intervals"]["TT"]["semitones"] == 6
    assert body["chords"]["dim7"]["offsets"] == [3, 6, 9]


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


def test_the_browser_ladder_is_intervals_and_covers_them_all():
    """The web app is intervals-only and organises its lessons flatly, so its
    ladder is its own. What may never drift is the interval table above."""
    js = _js_lessons()
    named = set()
    for _, _, mode, ids in js:
        assert mode in ("harmonic", "melodic")
        for i in ids:
            assert i in INTERVALS, i
            named.add(i)
    assert named == set(INTERVALS)


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
            r'Lesson\((\d+), "([^"]+)", Mode\.(\w+), (ALL|listOf\([^)]*\)), '
            r'Group\.(\w+), Kind\.(\w+)\)', block):
        raw = m.group(4)
        names = list(_kotlin_intervals()) if raw == "ALL" else re.findall(r'"([\w]+)"', raw)
        out.append((int(m.group(1)), m.group(2), m.group(3).lower(), names,
                    m.group(5).lower(), m.group(6).lower()))
    return out


def test_the_kotlin_interval_table_matches():
    assert _kotlin_intervals() == {i: (s, n) for i, (s, n) in INTERVALS.items()}


def test_the_kotlin_ladder_matches():
    assert _kotlin_lessons() == [
        (l.n, l.title, l.mode, list(l.set), l.group.value, l.kind.value) for l in LESSONS]


def test_the_kotlin_chord_table_matches():
    src = (ANDROID_SRC / "Intervals.kt").read_text()
    block = src[src.index("val CHORDS"):src.index("val ALL_CHORDS")]
    found = {}
    for m in re.finditer(r'"(\w+)" to Chord\(listOf\(([\d, ]+)\), "([^"]+)"\)', block):
        found[m.group(1)] = (tuple(int(x) for x in m.group(2).split(",")), m.group(3))
    assert found == {c: (tuple(o), nm) for c, (o, nm) in CHORDS.items()}


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


def test_the_speech_model_ships_with_its_uuid_marker():
    """Vosk's StorageService reads <model>/uuid to decide whether its copy is
    stale. The plain model zip has no such file, and without it unpacking fails
    and the app silently has no recogniser at all — which is exactly what it
    did on the first device run."""
    workflow = (Path(__file__).resolve().parent.parent
                / ".github/workflows/android.yml").read_text()
    assert "model-en-us/uuid" in workflow


def test_the_drill_waits_for_the_recogniser():
    """Starting before the model is unpacked means asking questions that
    nothing can hear."""
    app = (ANDROID_SRC / "EarService.kt").read_text()
    assert "startPending" in app
    assert "if (!modelReady)" in app


def test_the_voice_only_answers():
    """Controls are buttons. The recogniser no longer has to tell an answer
    from an instruction, and a stray word cannot trigger one."""
    src = (ANDROID_SRC / "Intervals.kt").read_text()
    assert "enum class Command" not in src
    assert "findJump" not in src
    grammar = src[src.index("fun voskGrammar()"):]
    for word in ('"repeat"', '"skip"', '"pause"', '"stop"', '"lesson"'):
        assert word not in grammar, word


def test_the_page_carries_one_control_and_one_reset():
    """Play again and Skip are gone: mid-question there is nothing to decide,
    and two dim buttons under the answer were two things to read past."""
    app = (ANDROID_SRC / "EarService.kt").read_text()
    assert "fun repeatQuestion()" not in app
    assert "fun skipQuestion()" not in app
    xml = LAYOUT.read_text()
    assert "@+id/again" not in xml and "@+id/skip" not in xml


def test_silence_repeats_rather_than_scoring_a_miss():
    app = (ANDROID_SRC / "EarService.kt").read_text()
    assert "const val MAX_REPEATS = 5" in app
    assert "if (q.replays >= MAX_REPEATS)" in app


def test_a_wrong_answer_is_not_told_off():
    app = (ANDROID_SRC / "EarService.kt").read_text()
    assert "Not quite" not in app


def test_the_instrument_and_direction_are_never_shown():
    """On a melodic lesson, "ascending" is half the answer."""
    assert "fun onSource" not in (ANDROID_SRC / "EarService.kt").read_text()
    layout = (EAR_DIR.parent / "android/app/src/main/res/layout/activity_main.xml").read_text()
    assert "@+id/source" not in layout


LAYOUT = (Path(__file__).resolve().parent.parent
          / "android/app/src/main/res/layout/activity_main.xml")


def test_three_voices_all_of_them_struck_or_plucked():
    synth = (ANDROID_SRC / "Synth.kt").read_text()
    assert "SAX" not in synth and "renderSax" not in synth
    for v in ("PIANO(", "NYLON(", "STEEL("):
        assert v in synth, v


def test_the_menu_leads_with_melodic():
    """Group order is the order the menu shows, so it lives in the enum."""
    assert [g.value for g in Group] == ["melodic", "harmonic", "chords"]
    kt = (ANDROID_SRC / "Intervals.kt").read_text()
    order = re.search(r"enum class Group\(val label: String\) \{([^}]*)\}", kt).group(1)
    assert order.index("MELODIC") < order.index("HARMONIC") < order.index("CHORDS")


def test_nothing_narrates_the_microphone():
    """No "listening" caption and no running transcript — neither tells you
    anything you cannot hear, and both pull the eye to the screen."""
    app = (ANDROID_SRC / "EarService.kt").read_text()
    assert 'status("listening"' not in app
    assert "fun onHeard(text: String)\n" not in app     # the Observer method
    assert "@+id/heard" not in LAYOUT.read_text()


def test_the_score_is_the_same_two_marks_everywhere():
    """Ticks and crosses on the main page exactly as in the menu — same two
    marks, and the same green and red, so one reads as the other."""
    app = (ANDROID_SRC / "EarService.kt").read_text()
    assert "\\u2713" in app and "\\u2717" in app
    assert "on first hearing" not in app
    item = (LAYOUT.parent / "item_lesson.xml").read_text()
    assert "scoreRight" in item and "scoreWrong" in item

    xml = LAYOUT.read_text()
    for view, colour in (("tallyRight", "@color/ok"), ("tallyWrong", "@color/bad")):
        block = xml[xml.index(f'@+id/{view}"'):]
        block = block[:block.index("/>")]
        assert f'android:textColor="{colour}"' in block, view


def test_every_answer_is_also_a_key():
    """Speech is the point, but a recogniser that mishears you twice running
    must not be the only way past a question."""
    xml = LAYOUT.read_text()
    assert "SPEAK / TAP THE ANSWER" in xml
    assert "com.millsandgoon.ear.FlowLayout" in xml
    assert (LAYOUT.parent / "item_answer.xml").exists()

    app = (ANDROID_SRC / "MainActivity.kt").read_text()
    assert "R.layout.item_answer" in app
    assert "service?.tapAnswer(id)" in app
    assert "fun tapAnswer(id: String)" in (ANDROID_SRC / "EarService.kt").read_text()


def test_the_score_belongs_to_the_exercise():
    """One running total across a sitting meant nothing — fifths answered
    right say nothing about your sevenths — and it read as a second, rival
    score beside the per-exercise ones in the menu."""
    app = (ANDROID_SRC / "EarService.kt").read_text()
    assert "private var seen = 0" not in app
    assert "private var correct = 0" not in app
    assert "firstHearing" not in app

    reset = app[app.index("fun resetScore()"):]
    reset = reset[:reset.index("\n    }")]
    assert "perLesson.remove(lesson.n)" in reset
    assert "perLesson.clear()" not in reset, "reset must not empty the other exercises"


def test_pausing_is_shown_by_the_button_alone():
    """The icon already flipped. A word saying the same thing is one more
    thing to read at a red light."""
    app = (ANDROID_SRC / "EarService.kt").read_text()
    assert 'status("paused"' not in app
    assert '"Paused"' not in app


def test_the_page_is_ordered_for_a_thumb():
    """Speak-the-answer, then the score with its reset, then play/pause at the
    bottom where the thumb already rests. Both buttons big enough to hit
    without looking."""
    xml = LAYOUT.read_text()
    order = [xml.index(f'@+id/{i}"') for i in
             ("sayable", "tallyRight", "tallyWrong", "reset", "transport")]
    assert order == sorted(order), "main page is out of order"
    assert xml.count('android:layout_height="74dp"') == 2      # reset and transport


def test_the_two_buttons_are_icons():
    """Driving, you read a shape, not a word — and "Resume" never fits the
    button that also means Start."""
    xml = LAYOUT.read_text()
    for view in ("reset", "transport"):
        block = xml[xml.index(f'@+id/{view}"'):]
        block = block[:block.index("/>")]
        assert "android:src=" in block and "android:text=" not in block, view

    app = (ANDROID_SRC / "MainActivity.kt").read_text()
    assert "R.drawable.ic_pause" in app and "R.drawable.ic_play" in app
    d = LAYOUT.parent.parent / "drawable"
    for icon in ("ic_play.xml", "ic_pause.xml", "ic_reset.xml"):
        assert (d / icon).exists(), icon


def test_start_and_reset_are_the_same_shape():
    """Same control, different consequence — so shape matches and only the
    fill differs."""
    d = LAYOUT.parent.parent / "drawable"
    radius = re.compile(r'android:radius="(\d+)dp"')
    a = radius.search((d / "bg_transport.xml").read_text()).group(1)
    b = radius.search((d / "bg_reset.xml").read_text()).group(1)
    assert a == b
