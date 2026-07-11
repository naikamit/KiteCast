"""Thin Kite Connect REST client + postback verification.

Only my side of the split uses this (PRD §4): order placement and fill
postbacks run on my api_key/access_token, which never leave the server
(NFR-3). Friends never touch this module — they get Publisher basket links.
"""

import hashlib

import httpx

API_ROOT = "https://api.kite.trade"
LOGIN_URL = "https://kite.zerodha.com/connect/login"


class KiteError(Exception):
    pass


class KiteClient:
    def __init__(self, api_key: str, api_secret: str, access_token: str | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = access_token

    # ---- daily login flow ----

    def login_url(self) -> str:
        return f"{LOGIN_URL}?v=3&api_key={self.api_key}"

    def generate_session(self, request_token: str) -> str:
        """Exchange the request_token from the login redirect for an access_token."""
        checksum = hashlib.sha256(
            (self.api_key + request_token + self.api_secret).encode()
        ).hexdigest()
        resp = httpx.post(
            f"{API_ROOT}/session/token",
            data={"api_key": self.api_key, "request_token": request_token, "checksum": checksum},
            headers={"X-Kite-Version": "3"},
        )
        data = self._unwrap(resp)
        self.access_token = data["access_token"]
        return self.access_token

    def _auth_headers(self) -> dict:
        if not self.access_token:
            raise KiteError("No access token — complete the daily Kite login first (/auth/login).")
        return {
            "X-Kite-Version": "3",
            "Authorization": f"token {self.api_key}:{self.access_token}",
        }

    # ---- market data ----

    def instruments_csv(self) -> str:
        """Kite's full instrument-master dump (CSV, refreshed daily)."""
        resp = httpx.get(f"{API_ROOT}/instruments", headers=self._auth_headers(), timeout=30.0)
        if resp.status_code != 200:
            raise KiteError(f"Instrument dump failed (HTTP {resp.status_code})")
        return resp.text

    def quote(self, *keys: str) -> dict:
        """Full quotes for EXCHANGE:TRADINGSYMBOL keys (price, OI, depth, circuits)."""
        resp = httpx.get(f"{API_ROOT}/quote", params=[("i", k) for k in keys],
                         headers=self._auth_headers(), timeout=10.0)
        return self._unwrap(resp)

    # ---- orders ----

    def place_order(self, *, tradingsymbol: str, exchange: str, transaction_type: str,
                    quantity: int, product: str, order_type: str,
                    price: float | None = None, autoslice: bool = False,
                    tag: str = "kitecast") -> str:
        """Place an order on MY account and return the Kite order_id."""
        payload = {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "transaction_type": transaction_type,
            "quantity": str(quantity),
            "product": product,
            "order_type": order_type,
            "validity": "DAY",
            "tag": tag,
        }
        if order_type == "LIMIT" and price is not None:
            payload["price"] = str(price)
        if autoslice:
            payload["autoslice"] = "true"
        resp = httpx.post(f"{API_ROOT}/orders/regular", data=payload, headers=self._auth_headers())
        return self._unwrap(resp)["order_id"]

    def verify_postback(self, payload: dict) -> bool:
        """Kite signs each postback: sha256(order_id + order_timestamp + api_secret)."""
        expected = hashlib.sha256(
            (
                str(payload.get("order_id", ""))
                + str(payload.get("order_timestamp", ""))
                + self.api_secret
            ).encode()
        ).hexdigest()
        return payload.get("checksum") == expected

    @staticmethod
    def _unwrap(resp: httpx.Response) -> dict:
        try:
            body = resp.json()
        except ValueError:
            raise KiteError(f"Kite returned non-JSON (HTTP {resp.status_code})")
        if resp.status_code != 200 or body.get("status") != "success":
            raise KiteError(body.get("message", f"Kite error (HTTP {resp.status_code})"))
        return body["data"]
