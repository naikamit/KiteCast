# Ear — Android

The commute build. Same drill as `millsandgoon.com/ear`, but it keeps working
with the phone in your pocket, which a browser cannot do: when the screen
locks, the OS takes the microphone back and no web API gets it returned.

A foreground service typed `microphone` holds it. That is the entire reason
this exists.

## What is different from the web app

**Recognition is offline and grammar-constrained.** Vosk is told the only
things you could sensibly say — twelve interval names, their aliases, and the
commands. It chooses between those, not between them and the whole English
language, and never goes near a network. `[unk]` is in the grammar, so humming
to find a note comes back as out-of-grammar and is ignored rather than scored.

**Most of the web app's session code is gone.** The self-echo gate, the restart
churn, the reopen-gap backoff, the visibility juggling, the wake lock, the
interim-versus-final race — all of it existed to fight the browser. Vosk
streams continuously and the service simply keeps running.

**Synthesis is the same, ported.** Additive piano with string inharmonicity,
Karplus-Strong guitars with a fractionally interpolated delay line. The Kotlin
was measured against the verified JavaScript: guitars within 0.4 cents
absolute, interval error under 0.35 cents.

**Progress is shared.** It posts to the same `/ear/api/*` endpoints, so mastery
follows you between the phone and the desk.

## Before it will build

The speech model is not in git — see `app/src/main/assets/PUT-THE-MODEL-HERE.md`.
Roughly 40 MB, downloaded once.

## Source of truth

`kitecast/ear.py` holds the canonical interval table and lesson ladder.
`tests/test_ear.py` parses both `ear/intervals.js` and
`android/.../Intervals.kt` and fails if any of the three drift apart —
no compiler involved, which is exactly what catches a transcription slip.

## Honest status

Written without a compiler or an Android SDK to hand: this container has
neither, and `dl.google.com`, Gradle's distribution host and Maven Central are
all blocked from it. The interval table, the lesson ladder and the synthesis
constants are verified by test. The Kotlin itself has never been compiled, so
expect the first build to surface ordinary compile errors, and read
`android/FIRST-RUN.md` before starting.
