"""Kite instrument master — powers the console's contract search.

Kite publishes a full CSV dump of every tradable instrument daily. We pull it
once (lazily, after login), keep it in memory, and refresh when stale, so the
ticket's contract dropdown is always real Kite data: exact tradingsymbols,
lot sizes, tick sizes, expiries.
"""

import csv
import io
import threading
import time

TRADABLE_EXCHANGES = {"MCX", "NFO", "NSE", "BSE", "CDS", "BFO"}


class InstrumentStore:
    def __init__(self, kite, ttl_seconds: int = 12 * 3600):
        self.kite = kite
        self.ttl = ttl_seconds
        self._rows: list[dict] = []
        self._by_key: dict[tuple[str, str], dict] = {}
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def _refresh_if_stale(self) -> None:
        with self._lock:
            if self._rows and time.time() - self._loaded_at < self.ttl:
                return
            text = self.kite.instruments_csv()
            rows = []
            for r in csv.DictReader(io.StringIO(text)):
                if r.get("exchange") not in TRADABLE_EXCHANGES:
                    continue
                rows.append({
                    "exchange": r["exchange"],
                    "tradingsymbol": r["tradingsymbol"],
                    "name": r.get("name") or "",
                    "expiry": r.get("expiry") or "",
                    "strike": float(r.get("strike") or 0),
                    "tick_size": float(r.get("tick_size") or 0),
                    "lot_size": int(float(r.get("lot_size") or 1)),
                    "instrument_type": r.get("instrument_type") or "",
                    "segment": r.get("segment") or "",
                })
            self._rows = rows
            self._by_key = {(x["exchange"], x["tradingsymbol"]): x for x in rows}
            self._loaded_at = time.time()

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Rank tradingsymbol prefix > tradingsymbol substring > name matches;
        multi-word queries (e.g. "crudeoil jul") must all match. Nearest
        expiry first within a rank."""
        self._refresh_if_stale()
        terms = query.strip().upper().split()
        if not terms:
            return []
        scored = []
        for x in self._rows:
            ts, name = x["tradingsymbol"], x["name"].upper()
            hay = f"{ts} {name}"
            if not all(t in hay for t in terms):
                continue
            if ts.startswith(terms[0]):
                score = 0
            elif terms[0] in ts:
                score = 1
            elif name.startswith(terms[0]):
                score = 2
            else:
                score = 3
            scored.append((score, x["expiry"] or "9999-99-99", ts, x))
        scored.sort(key=lambda t: t[:3])
        return [t[3] for t in scored[:limit]]

    def get(self, exchange: str, tradingsymbol: str) -> dict | None:
        self._refresh_if_stale()
        return self._by_key.get((exchange.upper(), tradingsymbol.upper()))
