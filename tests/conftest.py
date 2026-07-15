import pytest

from kitecast.config import Settings
from kitecast.db import Ledger
from kitecast.kite import KiteClient
from kitecast.service import TradeShareService


SAMPLE_INSTRUMENTS_CSV = """\
instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange
1,1,CRUDEOIL25JULFUT,CRUDEOIL,0,2026-07-17,0,0.1,100,FUT,MCX-FUT,MCX
2,2,CRUDEOIL25AUGFUT,CRUDEOIL,0,2026-08-19,0,0.1,100,FUT,MCX-FUT,MCX
3,3,GOLD25AUGFUT,GOLD,0,2026-08-05,0,1,100,FUT,MCX-FUT,MCX
4,4,NIFTY25JUL25000CE,NIFTY,0,2026-07-30,25000,0.05,75,CE,NFO-OPT,NFO
5,5,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.05,1,EQ,NSE,NSE
6,6,GIFTNIFTY,GIFT NIFTY,0,2026-07-30,0,0.5,50,FUT,IFSC-FUT,NSEIX
7,7,CRUDEOIL26JUL5500CE,CRUDEOIL,0,2026-07-15,5500,0.1,100,CE,MCX-OPT,MCX
"""

SAMPLE_QUOTE = {
    "last_price": 6250.0,
    "net_change": 42.5,
    "volume": 12345,
    "oi": 6789,
    "ohlc": {"open": 6200.0, "high": 6280.0, "low": 6180.0, "close": 6207.5},
    "upper_circuit_limit": 6800.0,
    "lower_circuit_limit": 5700.0,
    "depth": {
        "buy": [{"price": 6249.9, "quantity": 3, "orders": 2}],
        "sell": [{"price": 6250.1, "quantity": 5, "orders": 1}],
    },
}


class FakeKite(KiteClient):
    """Real checksum verification; network calls stubbed."""

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        self._next_id += 1
        return str(self._next_id)

    def instruments_csv(self):
        self._auth_headers()  # same login requirement as the real call
        return SAMPLE_INSTRUMENTS_CSV

    def __init__(self):
        super().__init__("test_key", "test_secret", access_token="tok")
        self.orders: list[dict] = []
        self._next_id = 100
        self.quote_data = dict(SAMPLE_QUOTE)

    def quote(self, *keys):
        self._auth_headers()
        return {k: dict(self.quote_data) for k in keys}

    def order_margins(self, order):
        self._auth_headers()
        return {"total": 150000.0, "span": 90000.0, "exposure": 60000.0,
                "option_premium": 0, "type": "commodity"}


class FakeTelegram:
    def __init__(self):
        self.sent: list[tuple] = []

    def send(self, chat_id, text, button_text, url):
        self.sent.append((chat_id, text, button_text, url))
        return True

    def fan_out(self, messages):
        return [self.send(*m) for m in messages]


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.kite_api_key = "test_key"
    s.kite_api_secret = "test_secret"
    s.base_url = "https://vps.test"
    s.db_path = str(tmp_path / "test.db")
    s.share_timing_entry = "fill"
    s.share_timing_exit = "fill"
    return s


@pytest.fixture
def store(kite):
    from kitecast.instruments import InstrumentStore

    return InstrumentStore(kite)


@pytest.fixture
def ledger(settings):
    return Ledger(settings.db_path)


@pytest.fixture
def kite():
    return FakeKite()


@pytest.fixture
def telegram():
    return FakeTelegram()


@pytest.fixture
def service(ledger, kite, telegram, settings, store):
    return TradeShareService(ledger, kite, telegram, settings, store=store)


@pytest.fixture
def friends(ledger):
    """Four named friends (NFR-4), one with a multiplier, one inactive."""
    ids = [
        ledger.add_friend("Ravi", "1001"),
        ledger.add_friend("Sameer", "1002", multiplier=0.5),
        ledger.add_friend("Dee", "1003", multiplier=2.0),
        ledger.add_friend("Kiran", "1004"),
    ]
    ledger.update_friend(ids[3], active=False)
    return ids


def postback(kite, order_id, status="COMPLETE", avg_price=6250.0, filled_qty=None, ts="2026-07-10 11:00:00"):
    """Build a checksum-valid Kite postback payload."""
    import hashlib

    payload = {
        "order_id": str(order_id),
        "order_timestamp": ts,
        "exchange_timestamp": ts,
        "status": status,
        "average_price": avg_price,
        "filled_quantity": filled_qty,
    }
    payload["checksum"] = hashlib.sha256(
        (str(order_id) + ts + kite.api_secret).encode()
    ).hexdigest()
    return payload
