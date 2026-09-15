# Ear — voice-only interval training

A rebuild of the Beato ear trainer's Intervals chapter with no visual
interface. You hear an interval, you say what it is. The screen is a monitor,
not a control surface — everything is reachable by voice.

Target deployment: `millsandboon.com/ear`. Static files, no backend.

## Running it

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
| `audio.js` | instrument synthesis, interval playback, reverb |
| `intervals.js` | interval table, 13-lesson ladder, speech→answer matching |
| `app.js` | session loop, recognition, scoring, view |
| `body.html` | shared markup fragment |
| `index.html` | deployable page (built from `body.html` + `style.css`) |
| `artifact.html` | same page built for Claude Artifact hosting |

Design is dark-first — this gets used in dim rooms and on headphones at night —
with a full light theme rather than an inversion. The screen is an instrument
panel: one oversized readout you can catch peripherally without actually
looking, since not looking is the whole point.

## Voice commands

`repeat` · `skip` · `score` · `listen` · `drill` · `lesson 7` ·
`melodic sixths` · `stop`

## Known limits

Web Speech sends audio to Google and needs a network connection. The Android
build should swap it for an on-device recogniser with a genuinely constrained
grammar (Vosk supports this), which removes the network dependency and improves
accuracy on a 12-word vocabulary.
