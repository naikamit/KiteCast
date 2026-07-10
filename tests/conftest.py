import pytest

from kitecast.config import Settings
from kitecast.db import Ledger
from kitecast.kite import KiteClient
from kitecast.service import TradeShareService


class FakeKite(KiteClient):
    """Real checksum verification; order placement stubbed."""

    def __init__(self):
        super().__init__("test_key", "test_secret", access_token="tok")
        self.orders: list[dict] = []
        self._next_id = 100

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        self._next_id += 1
        return str(self._next_id)


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
def ledger(settings):
    return Ledger(settings.db_path)


@pytest.fixture
def kite():
    return FakeKite()


@pytest.fixture
def telegram():
    return FakeTelegram()


@pytest.fixture
def service(ledger, kite, telegram, settings):
    return TradeShareService(ledger, kite, telegram, settings)


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
