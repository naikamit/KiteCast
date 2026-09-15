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


@dataclass(frozen=True)
class Lesson:
    n: int
    title: str
    mode: str            # "harmonic" | "melodic"
    set: tuple[str, ...]


LESSONS: tuple[Lesson, ...] = (
    Lesson(1,  "Harmonic: Seconds",                     "harmonic", ("m2", "M2")),
    Lesson(2,  "Melodic: Seconds",                      "melodic",  ("m2", "M2")),
    Lesson(3,  "Harmonic: Thirds",                      "harmonic", ("m3", "M3")),
    Lesson(4,  "Melodic: Thirds",                       "melodic",  ("m3", "M3")),
    Lesson(5,  "Harmonic: Fourths and Fifths",          "harmonic", ("P4", "TT", "P5")),
    Lesson(6,  "Melodic: Fourths and Fifths",           "melodic",  ("P4", "TT", "P5")),
    Lesson(7,  "Harmonic: Sixths",                      "harmonic", ("m6", "M6")),
    Lesson(8,  "Melodic: Sixths",                       "melodic",  ("m6", "M6")),
    Lesson(9,  "Harmonic: Sevenths",                    "harmonic", ("m7", "M7")),
    Lesson(10, "Melodic: Sevenths",                     "melodic",  ("m7", "M7")),
    Lesson(11, "Harmonic: Tritones and Major Sevenths", "harmonic", ("TT", "M7")),
    Lesson(12, "All Intervals: Harmonic",               "harmonic", tuple(ALL)),
    Lesson(13, "All Intervals: Melodic",                "melodic",  tuple(ALL)),
)

_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def safe_learner(token: str) -> bool:
    return bool(token and _SAFE.match(token))


def lesson(n: int) -> Lesson | None:
    for l in LESSONS:
        if l.n == n:
            return l
    return None


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
        return {i: {**_blank(), **stored.get(i, {})} for i in ALL}

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
