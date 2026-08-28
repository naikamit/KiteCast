"""Screenshot → order extraction via the Claude API.

The login-free sharing path: a broker-order screenshot is parsed by Claude
(vision), then validated against the cached instrument master — no Kite
session is involved anywhere, so this works even before the daily login.
"""

import base64
import json
import logging

log = logging.getLogger("kitecast")

PROMPT = """This is a screenshot of an Indian broker's order window (Zerodha Kite or similar) \
for an F&O, commodity, or equity order. Extract the order as strict JSON with exactly these \
keys and nothing else — no prose, no code fences:
{"underlying": "<instrument name, e.g. SILVER, CRUDEOILM, NIFTY>",
 "expiry_day": <int or null>, "expiry_month": <int 1-12 or null>, "expiry_year": <int or null>,
 "strike": <number or null>, "instrument_type": "CE" | "PE" | "FUT" | "EQ",
 "side": "BUY" | "SELL", "qty_lots": <int>,
 "limit_price": <number or null>, "ltp": <number or null>}
Rules: the contract title gives underlying/expiry/strike (e.g. "SILVER 24th Sep 270000 CE" ->
underlying SILVER, expiry_day 24, expiry_month 9, expiry_year null unless shown, strike 270000,
instrument_type CE). strike is null for futures and stocks. side comes from the selected Buy/Sell
toggle. qty_lots is the lots/quantity field (default 1). limit_price is the price in the order
entry box when a Limit order is selected, else null. ltp is the last traded price shown, else null."""


class VisionExtractor:
    def __init__(self, api_key: str, model: str, workspace_id: str = ""):
        self.api_key = api_key
        self.model = model
        # Identity-linked API keys require the workspace id on every request;
        # workspace-scoped keys don't need it.
        self.workspace_id = workspace_id

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def extract(self, image_bytes: bytes, media_type: str) -> dict:
        import anthropic

        headers = {"anthropic-workspace-id": self.workspace_id} if self.workspace_id else None
        client = anthropic.Anthropic(api_key=self.api_key, default_headers=headers)
        response = client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": media_type,
                                "data": base64.standard_b64encode(image_bytes).decode()}},
                    {"type": "text", "text": PROMPT},
                ],
            }],
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(text)
        log.info("screenshot parsed: %s", parsed)
        return parsed
