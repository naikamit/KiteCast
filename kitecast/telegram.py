"""Owner ops channel — a private bot DM to ME only (login reminders,
mirror-pricing alerts). Friends never get bot messages: their links are
shared manually via the console's copy / WhatsApp buttons."""

import httpx


class TelegramClient:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token

    def send(self, chat_id: str, text: str, button_text: str, url: str) -> bool:
        if not self.bot_token or not chat_id:
            return False
        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": [[{"text": button_text, "url": url}]]},
                },
                timeout=5.0,
            )
            return resp.status_code == 200 and resp.json().get("ok", False)
        except httpx.HTTPError:
            return False
