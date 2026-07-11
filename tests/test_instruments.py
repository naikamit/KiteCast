import pytest

from kitecast.kite import KiteError


def test_search_prefix_ranks_first_and_nearest_expiry(store):
    hits = store.search("crudeoil")
    assert [h["tradingsymbol"] for h in hits] == ["CRUDEOIL25JULFUT", "CRUDEOIL25AUGFUT"]
    assert hits[0]["lot_size"] == 100
    assert hits[0]["expiry"] == "2026-07-17"


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


def test_store_requires_kite_login(store, kite):
    kite.access_token = None
    with pytest.raises(KiteError):
        store.search("crudeoil")
