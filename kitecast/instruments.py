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
from datetime import datetime, timedelta, timezone

TRADABLE_EXCHANGES = {"MCX", "NFO", "NSE", "BSE", "CDS", "BFO"}
IST = timezone(timedelta(hours=5, minutes=30))


def days_to_expiry(expiry: str, today=None) -> int | None:
    """Calendar days from today (IST) to the expiry date string."""
    if not expiry:
        return None
    try:
        d = datetime.strptime(expiry[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (d - (today or datetime.now(IST).date())).days


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
            groups: dict[tuple[str, str], dict] = {}
            for x in rows:
                name = x["name"] or x["tradingsymbol"]
                g = groups.setdefault((name, x["exchange"]), {
                    "name": name, "exchange": x["exchange"],
                    "futures": 0, "options": 0, "equity": 0,
                })
                if x["instrument_type"] == "FUT":
                    g["futures"] += 1
                elif x["instrument_type"] in ("CE", "PE"):
                    g["options"] += 1
                else:
                    g["equity"] += 1
            self._underlyings = list(groups.values())
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

    def search_underlyings(self, query: str, limit: int = 15) -> list[dict]:
        """Search distinct underlyings (name × exchange) instead of raw
        symbols — the front option series can't flood the results."""
        self._refresh_if_stale()
        q = query.strip().upper()
        if not q:
            return []
        scored = []
        for g in self._underlyings:
            name = g["name"].upper()
            if name.startswith(q):
                score = 0
            elif q in name:
                score = 1
            else:
                continue
            scored.append((score, name, g["exchange"], g))
        scored.sort(key=lambda t: t[:3])
        return [t[3] for t in scored[:limit]]

    def chain(self, exchange: str, name: str) -> dict:
        """Everything tradable for one underlying: futures by expiry, the
        full option grid (expiry × strike × CE/PE), and cash instruments."""
        self._refresh_if_stale()
        rows = [x for x in self._rows
                if x["exchange"] == exchange and (x["name"] or x["tradingsymbol"]) == name]
        futures = sorted(
            ({"tradingsymbol": x["tradingsymbol"], "expiry": x["expiry"],
              "dte": days_to_expiry(x["expiry"]), "lot_size": x["lot_size"]}
             for x in rows if x["instrument_type"] == "FUT"),
            key=lambda f: f["expiry"] or "9999-99-99")
        options = sorted(
            ({"tradingsymbol": x["tradingsymbol"], "expiry": x["expiry"],
              "dte": days_to_expiry(x["expiry"]), "strike": x["strike"],
              "type": x["instrument_type"], "lot_size": x["lot_size"]}
             for x in rows if x["instrument_type"] in ("CE", "PE")),
            key=lambda o: (o["expiry"] or "9999-99-99", o["strike"], o["type"]))
        equities = [{"tradingsymbol": x["tradingsymbol"], "lot_size": x["lot_size"]}
                    for x in rows if x["instrument_type"] not in ("FUT", "CE", "PE")]
        return {"exchange": exchange, "name": name,
                "futures": futures, "options": options, "equities": equities}

    def underlying_future(self, exchange: str, name: str) -> dict | None:
        """Nearest-expiry future on the same exchange/name — the reference
        price for how far an option strike sits from ATM."""
        self._refresh_if_stale()
        futs = [x for x in self._rows
                if x["exchange"] == exchange and x["name"] == name
                and x["instrument_type"] == "FUT"]
        return min(futs, key=lambda x: x["expiry"] or "9999-99-99") if futs else None
