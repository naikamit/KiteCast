import pytest

from kitecast.kite import KiteError


def test_search_prefix_ranks_first_and_nearest_expiry(store):
    hits = store.search("crudeoil")
    assert [h["tradingsymbol"] for h in hits] == [
        "CRUDEOIL26JUL5500CE", "CRUDEOIL26JUL6000PE",
        "CRUDEOIL25JULFUT", "CRUDEOIL25AUGFUT",
    ]
    assert [h["tradingsymbol"] for h in store.search("crudeoil fut")] == [
        "CRUDEOIL25JULFUT", "CRUDEOIL25AUGFUT",
    ]
    assert hits[2]["lot_size"] == 100
    assert hits[2]["expiry"] == "2026-07-17"   # the July future


def test_search_multi_word_and_name(store):
    assert [h["tradingsymbol"] for h in store.search("crudeoil aug")] == ["CRUDEOIL25AUGFUT"]
    assert store.search("reliance ind")[0]["tradingsymbol"] == "RELIANCE"
    assert store.search("nifty 25000")[0]["tradingsymbol"] == "NIFTY25JUL25000CE"
    assert store.search("") == []
    assert store.search("zzzz") == []


def test_untradable_exchanges_excluded(store):
    assert store.search("giftnifty") == []


def test_get_is_case_insensitive(store):
    inst = store.get("mcx", "crudeoil25julfut")
    assert inst is not None and inst["tick_size"] == 0.1
    assert store.get("MCX", "NOPE") is None


def _pin_today(monkeypatch):
    """Fixture contracts have fixed expiries; pin 'today' so dte-based
    filtering (e.g. the resolver dropping expired contracts) is stable."""
    from datetime import date

    from kitecast import instruments as inst_mod

    real = inst_mod.days_to_expiry
    monkeypatch.setattr(inst_mod, "days_to_expiry",
                        lambda expiry, today=None: real(expiry, date(2026, 7, 11)))


def test_store_requires_kite_login(store, kite):
    kite.access_token = None
    with pytest.raises(KiteError):
        store.search("crudeoil")


def test_dump_disk_cache_survives_restart_without_login(kite, tmp_path):
    """A restarted process with a dead token must still resolve symbols from
    the on-disk dump copy."""
    from kitecast.instruments import InstrumentStore

    cache = str(tmp_path / "instruments_cache.csv")
    warm = InstrumentStore(kite, cache_path=cache)
    assert warm.search("crudeoil fut")          # live fetch, writes the cache

    kite.access_token = None                    # overnight: token dead...
    cold = InstrumentStore(kite, cache_path=cache)   # ...and process restarted
    hits = cold.search("crudeoil fut")
    assert hits[0]["tradingsymbol"] == "CRUDEOIL25JULFUT"
    assert cold.get("MCX", "CRUDEOIL26JUL5500CE") is not None


def test_resolve_screenshot(store, monkeypatch):
    _pin_today(monkeypatch)
    parsed = {"underlying": "CRUDEOIL", "expiry_day": 15, "expiry_month": 7,
              "expiry_year": None, "strike": 5500, "instrument_type": "CE"}
    inst = store.resolve_screenshot(parsed)
    assert inst["tradingsymbol"] == "CRUDEOIL26JUL5500CE"

    # Futures: expiry parts alone pick the right contract.
    fut = store.resolve_screenshot({"underlying": "CRUDEOIL", "expiry_day": None,
                                    "expiry_month": 8, "expiry_year": 26,
                                    "strike": None, "instrument_type": "FUT"})
    assert fut["tradingsymbol"] == "CRUDEOIL25AUGFUT"

    # Wrong strike or unknown name -> None, never a guess.
    assert store.resolve_screenshot({**parsed, "strike": 9999}) is None
    assert store.resolve_screenshot({**parsed, "underlying": "PLUTONIUM"}) is None


def test_days_to_expiry():
    from datetime import date

    from kitecast.instruments import days_to_expiry

    today = date(2026, 7, 11)
    assert days_to_expiry("2026-07-17", today) == 6
    assert days_to_expiry("2026-07-11", today) == 0
    assert days_to_expiry("", today) is None
    assert days_to_expiry("garbage", today) is None


def test_moneyness_labels():
    from kitecast.instruments import moneyness

    assert moneyness(5.0, "CE") == "5.0% OTM"      # call above spot
    assert moneyness(-5.0, "CE") == "5.0% ITM"     # call below spot
    assert moneyness(5.0, "PE") == "5.0% ITM"      # put above spot
    assert moneyness(-5.0, "PE") == "5.0% OTM"     # put below spot
    assert moneyness(0.0, "CE") == "ATM"
    assert moneyness(None, "CE") is None
    assert moneyness(5.0, "FUT") is None


def test_underlying_future_is_nearest_expiry(store):
    fut = store.underlying_future("MCX", "CRUDEOIL")
    assert fut["tradingsymbol"] == "CRUDEOIL25JULFUT"   # 07-17 beats 08-19
    assert store.underlying_future("MCX", "NOPE") is None
