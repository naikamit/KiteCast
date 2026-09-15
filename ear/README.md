# Ear — voice-only interval training

A rebuild of the Beato ear trainer's Intervals chapter with no visual
interface. You hear an interval, you say what it is. The screen is a monitor,
not a control surface — everything is reachable by voice.

Target deployment: `millsandgoon.com/ear`. Static files, no backend.

## Installing it on a phone

Served by the KiteCast app at `/ear` on both its hostnames, so in production
that is `https://millsandgoon.com/ear`. The app mounts this directory as static
files; there is no separate host or build step, and deploying the app deploys
this with it.

It is a PWA, so Chrome on Android offers *Install app* and runs it full-screen
from the home screen with the microphone intact. HTTPS is required — a plain
HTTP or LAN address gets no microphone.

All paths are relative and the service worker scopes itself to `/ear/`, so it
works unchanged at that subpath.

While a session runs it holds a screen wake lock, since a sleeping screen would
otherwise end the drill. Backgrounding the app still stops recognition — Web
Speech does not survive being backgrounded, which is the main reason the commute
case wants a native build rather than this.

## Running it locally

Speech recognition needs a secure origin, so `file://` will not work:

    cd ear && python3 -m http.server 8000
    # then open http://localhost:8000/

Chrome or Edge. Wired headphones.

## Design decisions

**Humming is ignored, not scored.** Singing to work out what you are hearing
is normal practice, so unmatched audio is treated as silence — no "I didn't
catch that", no attempt counted. Bare size words ("a third") are deliberately
absent from the alias table so they trigger a question rather than a guess.

**Major vs minor falls back to yes/no.** The two words differ by one vowel and
carry the most important distinction in the app. When the recogniser's
alternatives disagree on quality within a size, or the quality is missing, the
app asks "Minor? Yes or no." — two answers that sound nothing alike. Everything
else trusts the recogniser.

**Correctness only, but first-hearing accuracy is tracked separately.** With no
timer and unlimited replays every answer eventually converges on correct, so
the honest number is how often you got it without a replay.

**Instruments are synthesised, not sampled.** Piano is additive with string
inharmonicity; both guitars are Karplus-Strong with a fractionally-interpolated
delay line. Integer delay lengths detune high notes by enough cents to matter
when interval identification is the whole point. Measured: guitars within 0.4
cents absolute, interval error under 0.25 cents on all three voices.

Timbre is randomised per question across the three so you cannot anchor to one,
and the root never repeats between consecutive questions.

**Direction is randomised but not required in the answer.** Naming it doubles
the utterance length for no pedagogical gain at this stage; direction words are
accepted and discarded. Flip this if it should be scored.

## Files

| file | contents |
|---|---|
| `../kitecast/ear.py` | canonical interval table, lesson ladder, mastery store |
| `audio.js` | instrument synthesis, interval playback, reverb |
| `intervals.js` | the client's copy of the table, and speech→answer matching |
| `app.js` | session loop, recognition, scoring, view |
| `body.html` | shared markup fragment |
| `index.html` | deployable page (built from `body.html` + `style.css`) |
| `artifact.html` | same page built for Claude Artifact hosting |
| `selfecho.test.js`, `transport.test.js` | browser harnesses (see headers) |
| `manifest.webmanifest`, `sw.js`, `icons/` | PWA install and offline shell |

## What the server does

Very little, deliberately. The drill is entirely client-side — synthesis,
recognition, scoring — so it keeps working with no network. The server owns
mastery: how many times each interval has been heard, named correctly, and
named on first hearing, stored per learner under an opaque cookie id. No
accounts, and the stored row holds nothing but those counts.

That is what makes the adaptive weighting worth anything. Within one session
there is too little data to know what you are weak at; across every session
there is plenty, so the drill spends your time on the intervals you actually
miss rather than redealing the ones you have already got.

    GET  api/progress   counts, accuracy, first-hearing rate, weakest intervals
    POST api/answer     {interval, correct, first_listen}
    POST api/reset      forget this learner
    GET  api/lessons    the canonical ladder

The interval table exists in both `kitecast/ear.py` and `intervals.js` — Python
so the server can validate and report, JavaScript so the drill runs offline.
`tests/test_ear.py` parses the JS and fails if the two ever disagree.

## The log

A panel at the foot of the page records timings for every step: when the
recogniser opens and closes, how long the gap is between an utterance ending
and the stream reopening, when the first interim transcript arrives and how
long the final takes, and when the speech gate shuts and reopens. Copy sends
the lot to the clipboard.

"Sometimes it lags" is only diagnosable with numbers against each step, and the
usual culprit is visible as `mic closed, reopening in Nms` — Android ends the
stream after every utterance, and anything said during that gap is lost.

It never prints the interval being asked, which would spoil the drill you are
logging; the answer only appears once the question is resolved.

## Voice commands

`repeat` · `skip` · `score` · `listen` · `drill` · `pause` · `resume` ·
`lesson 7` · `melodic sixths` · `stop`

## Known limits

Web Speech sends audio to Google and needs a network connection. The Android
build should swap it for an on-device recogniser with a genuinely constrained
grammar (Vosk supports this), which removes the network dependency and improves
accuracy on a 12-word vocabulary.
