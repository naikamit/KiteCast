"""Ear trainer: the interval model, the lesson ladder, and per-learner mastery.

The third app in this deployment, alongside the trading console and the ebook
library, and as self-contained as the other two: no templates, no shared
tables, nothing imported from the trading side.

The drill itself runs in the browser — the audio is synthesised there and the
answers are spoken — so what the server owns is the part a browser cannot keep:
what you have actually learned, across sessions and devices. The interval table
below is canonical; `tests/test_ear.py` fails if the client's copy drifts from
it.
"""

import json
import re
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# id -> (semitones, spoken name). Ordered by size, which is the order the
# lesson ladder walks.
INTERVALS: dict[str, tuple[int, str]] = {
    "m2": (1, "minor second"),
    "M2": (2, "major second"),
    "m3": (3, "minor third"),
    "M3": (4, "major third"),
    "P4": (5, "perfect fourth"),
    "TT": (6, "tritone"),
    "P5": (7, "perfect fifth"),
    "m6": (8, "minor sixth"),
    "M6": (9, "major sixth"),
    "m7": (10, "minor seventh"),
    "M7": (11, "major seventh"),
    "P8": (12, "octave"),
}

ALL = list(INTERVALS)

# Chords are the same exercise with more notes: offsets above the root, played
# as a block. Naming a chord quality is the same act of recall as naming an
# interval, so they share the drill, the scoring and the mastery store.
CHORDS: dict[str, tuple[tuple[int, ...], str]] = {
    "maj":  ((4, 7), "major"),
    "min":  ((3, 7), "minor"),
    "dim":  ((3, 6), "diminished"),
    "aug":  ((4, 8), "augmented"),
    "maj7": ((4, 7, 11), "major seventh"),
    "dom7": ((4, 7, 10), "dominant seventh"),
    "min7": ((3, 7, 10), "minor seventh"),
    "m7b5": ((3, 6, 10), "half diminished"),
    "dim7": ((3, 6, 9), "diminished seventh"),
}

ALL_CHORDS = list(CHORDS)


class Group(str, Enum):
    """The top level of the menu: what kind of thing you are naming."""
    HARMONIC = "harmonic"
    MELODIC = "melodic"
    CHORDS = "chords"


class Kind(str, Enum):
    INTERVAL = "interval"
    CHORD = "chord"


@dataclass(frozen=True)
class Lesson:
    n: int
    title: str
    mode: str            # "harmonic" | "melodic" — how the notes are played
    set: tuple[str, ...]
    group: Group = Group.HARMONIC
    kind: Kind = Kind.INTERVAL


H, M, C = Group.HARMONIC, Group.MELODIC, Group.CHORDS
IV, CH = Kind.INTERVAL, Kind.CHORD

LESSONS: tuple[Lesson, ...] = (
    Lesson(1,  "Seconds",                     "harmonic", ("m2", "M2"), H, IV),
    Lesson(2,  "Thirds",                      "harmonic", ("m3", "M3"), H, IV),
    Lesson(3,  "Fourths and Fifths",          "harmonic", ("P4", "TT", "P5"), H, IV),
    Lesson(4,  "Sixths",                      "harmonic", ("m6", "M6"), H, IV),
    Lesson(5,  "Sevenths",                    "harmonic", ("m7", "M7"), H, IV),
    Lesson(6,  "Tritones and Major Sevenths", "harmonic", ("TT", "M7"), H, IV),
    Lesson(7,  "All Intervals",               "harmonic", tuple(ALL), H, IV),

    Lesson(8,  "Seconds",                     "melodic",  ("m2", "M2"), M, IV),
    Lesson(9,  "Thirds",                      "melodic",  ("m3", "M3"), M, IV),
    Lesson(10, "Fourths and Fifths",          "melodic",  ("P4", "TT", "P5"), M, IV),
    Lesson(11, "Sixths",                      "melodic",  ("m6", "M6"), M, IV),
    Lesson(12, "Sevenths",                    "melodic",  ("m7", "M7"), M, IV),
    Lesson(13, "All Intervals",               "melodic",  tuple(ALL), M, IV),

    Lesson(14, "Major and Minor Triads",      "harmonic", ("maj", "min"), C, CH),
    Lesson(15, "All Four Triads",             "harmonic", ("maj", "min", "dim", "aug"), C, CH),
    Lesson(16, "Sevenths: Three",             "harmonic", ("maj7", "dom7", "min7"), C, CH),
    Lesson(17, "All Sevenths",                "harmonic", tuple(ALL_CHORDS[4:]), C, CH),
)

_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def safe_learner(token: str) -> bool:
    return bool(token and _SAFE.match(token))


def lesson(n: int) -> Lesson | None:
    for l in LESSONS:
        if l.n == n:
            return l
    return None


def display(item_id: str) -> str:
    """The spoken name of an interval or a chord."""
    if item_id in INTERVALS:
        return INTERVALS[item_id][1]
    return CHORDS[item_id][1]


def every_item() -> list[str]:
    return ALL + ALL_CHORDS


def _blank() -> dict:
    return {"seen": 0, "correct": 0, "first": 0}


class Progress:
    """Per-learner interval mastery; `root=None` keeps everything in memory.

    One JSON file per learner. Mastery is counted per interval rather than per
    lesson, because "you miss descending minor sixths" is the useful unit and
    a lesson percentage is not.
    """

    def __init__(self, root: str | None):
        self._root = Path(root) if root else None
        if self._root:
            self._root.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _path(self, learner: str) -> Path | None:
        return self._root / f"{learner}.json" if self._root else None

    def _read(self, learner: str) -> dict:
        p = self._path(learner)
        if p is None:
            return json.loads(json.dumps(self._mem.get(learner, {})))
        if not p.is_file():
            return {}
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, learner: str, data: dict) -> None:
        p = self._path(learner)
        if p is None:
            self._mem[learner] = data
            return
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(p)

    # -- reads

    def intervals(self, learner: str) -> dict[str, dict]:
        """Counts for every interval, including ones never drilled."""
        stored = self._read(learner)
        return {i: {**_blank(), **stored.get(i, {})} for i in every_item()}

    def summary(self, learner: str) -> dict:
        by = self.intervals(learner)
        seen = sum(v["seen"] for v in by.values())
        correct = sum(v["correct"] for v in by.values())
        first = sum(v["first"] for v in by.values())
        # Only intervals actually being missed. Naming a flawless one "weakest"
        # because it sorted third would just be untrue.
        drilled = {i: v for i, v in by.items()
                   if v["seen"] >= 3 and v["correct"] < v["seen"]}
        weakest = sorted(drilled, key=lambda i: drilled[i]["correct"] / drilled[i]["seen"])
        return {
            "seen": seen,
            "correct": correct,
            "first": first,
            "accuracy": round(correct / seen, 4) if seen else None,
            "first_hearing": round(first / seen, 4) if seen else None,
            "weakest": weakest[:3],
            "intervals": by,
        }

    # -- writes

    def record(self, learner: str, interval: str, *, correct: bool,
               first_listen: bool) -> dict:
        """Log one answer. Unknown intervals are rejected by the caller."""
        with self._lock:
            data = self._read(learner)
            row = {**_blank(), **data.get(interval, {})}
            row["seen"] += 1
            if correct:
                row["correct"] += 1
                if first_listen:
                    row["first"] += 1
            data[interval] = row
            self._write(learner, data)
            return row

    def reset(self, learner: str) -> None:
        with self._lock:
            p = self._path(learner)
            if p is None:
                self._mem.pop(learner, None)
            elif p.is_file():
                p.unlink()
