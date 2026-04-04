"""Telegram notification sender."""

import logging
import time
from curl_cffi import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class Notifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)

    def send(self, message: str) -> bool:
        """Send a Telegram message. Returns True on success."""
        if not self._enabled:
            logger.info(f"[DRY RUN] Telegram message:\n{message}")
            return True

        url = TELEGRAM_API.format(token=self.bot_token)
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            logger.debug("Telegram message sent OK")
            return True
        except requests.RequestException as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Alert formatters
    # ------------------------------------------------------------------

    def alert_new_product(self, retailer: str, product_name: str, price: float,
                          url: str, matched_keyword: str) -> None:
        price_str = f"${price:.2f}" if price > 0 else "Price N/A"
        msg = (
            f"🆕 <b>New Rum — {retailer}</b>\n"
            f"{product_name}\n"
            f"{price_str}\n"
            f'Matched: <i>{matched_keyword}</i>\n'
            f"🔗 {url}"
        )
        self.send(msg)
        time.sleep(0.5)  # Avoid Telegram rate limits between back-to-back sends

    def alert_price_drop(self, retailer: str, product_name: str,
                         old_price: float, new_price: float, url: str) -> None:
        if old_price > 0:
            pct = ((old_price - new_price) / old_price) * 100
            change_str = f"${old_price:.2f} → ${new_price:.2f}  (↓{pct:.1f}%)"
        else:
            change_str = f"${new_price:.2f}"
        msg = (
            f"📉 <b>Price Drop — {retailer}</b>\n"
            f"{product_name}\n"
            f"{change_str}\n"
            f"🔗 {url}"
        )
        self.send(msg)
        time.sleep(0.5)

    def alert_price_increase(self, retailer: str, product_name: str,
                             old_price: float, new_price: float, url: str) -> None:
        if old_price > 0:
            pct = ((new_price - old_price) / old_price) * 100
            change_str = f"${old_price:.2f} → ${new_price:.2f}  (↑{pct:.1f}%)"
        else:
            change_str = f"${new_price:.2f}"
        msg = (
            f"📈 <b>Price Increase — {retailer}</b>\n"
            f"{product_name}\n"
            f"{change_str}\n"
            f"🔗 {url}"
        )
        self.send(msg)
        time.sleep(0.5)

    def alert_low_stock(self, retailer: str, product_name: str,
                        stock: int, url: str) -> None:
        stock_str = str(stock) if stock >= 0 else "≤ few"
        msg = (
            f"⚠️ <b>Low Stock — {retailer}</b>\n"
            f"{product_name}\n"
            f"Only {stock_str} left in stock!\n"
            f"🔗 {url}"
        )
        self.send(msg)
        time.sleep(0.5)

    def alert_out_of_stock(self, retailer: str, product_name: str, url: str) -> None:
        msg = (
            f"❌ <b>Out of Stock — {retailer}</b>\n"
            f"{product_name}\n"
            f"🔗 {url}"
        )
        self.send(msg)
        time.sleep(0.5)

    def alert_back_in_stock(self, retailer: str, product_name: str,
                            price: float, url: str) -> None:
        price_str = f"${price:.2f}" if price > 0 else ""
        msg = (
            f"✅ <b>Back in Stock — {retailer}</b>\n"
            f"{product_name}"
            + (f"\n{price_str}" if price_str else "") +
            f"\n🔗 {url}"
        )
        self.send(msg)
        time.sleep(0.5)

    def alert_scrape_error(self, retailer: str, url: str) -> None:
        """Only used when a scrape failure implies the product may be gone."""
        msg = (
            f"🔴 <b>Possible Out of Stock — {retailer}</b>\n"
            f"Could not fetch watched product page (may be delisted):\n"
            f"{url}"
        )
        self.send(msg)
        time.sleep(0.5)
