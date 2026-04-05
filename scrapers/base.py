"""Base scraper class and shared data structures."""

import re
import time
import random
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import List, Optional

# curl_cffi impersonates Chrome's TLS fingerprint, bypassing DataDome and
# similar bot-detection systems that block standard requests/urllib stacks.
from curl_cffi import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# safari17_2_ios bypasses Cloudflare on KL Wines (www + shop subdomains)
# while still working on all other retailers
IMPERSONATE = "safari17_2_ios"


@dataclass
class Product:
    """A product found in a retailer's catalog."""
    id: str           # Unique ID within the retailer (SKU, handle, etc.)
    name: str
    price: float
    url: str
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Product":
        return cls(
            id=d["id"],
            name=d["name"],
            price=float(d.get("price", 0)),
            url=d["url"],
            description=d.get("description", ""),
        )


@dataclass
class WatchedProduct:
    """Current state of a specific product URL being monitored."""
    name: str
    price: float
    stock: int          # -1 means unknown quantity (but in stock)
    url: str
    in_stock: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def parse_price(text: str) -> float:
    """Extract a dollar price from a string like '$89.99' or '89.99'."""
    if not text:
        return 0.0
    cleaned = text.replace(",", "").strip()
    match = re.search(r"\$?\s*([\d]+\.?\d*)", cleaned)
    if match:
        return float(match.group(1))
    return 0.0


def keywords_match(text: str, keywords: List[str]) -> Optional[str]:
    """Return the first keyword found in text (case-insensitive), or None."""
    lower = text.lower()
    for kw in keywords:
        if kw.lower() in lower:
            return kw
    return None


class BaseScraper(ABC):
    """Abstract base for all retailer scrapers."""

    def __init__(self, config: dict, debug: bool = False):
        self.config = config
        self.debug = debug
        self.session = requests.Session(impersonate=IMPERSONATE)
        self.access_denied = False  # Set True when we hit a 403 (bot protection)
        
    def _get(self, url: str, **kwargs) -> Optional[requests.Response]:
        """GET a URL with polite rate limiting and error handling."""
        delay = random.uniform(1.5, 3.0)
        logger.debug(f"Sleeping {delay:.1f}s before fetching {url}")
        time.sleep(delay)
        try:
            resp = self.session.get(url, timeout=30, **kwargs)
            resp.raise_for_status()
            if self.debug:
                slug = re.sub(r"[^a-z0-9]", "_", url.lower())[:60]
                debug_path = f"/tmp/debug_{self.__class__.__name__}_{slug}.html"
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(resp.text)
                logger.debug(f"Saved raw HTML to {debug_path}")
            return resp
        except Exception as e:
            if "403" in str(e):
                self.access_denied = True
                logger.warning(f"Access denied (403) for {url} — likely bot protection")
            else:
                logger.error(f"Request failed for {url}: {e}")
            return None

    def _soup(self, url: str, **kwargs) -> Optional[BeautifulSoup]:
        """Fetch and parse a page into BeautifulSoup."""
        resp = self._get(url, **kwargs)
        if resp is None:
            return None
        return BeautifulSoup(resp.text, "lxml")

    @abstractmethod
    def get_catalog(self) -> List[Product]:
        """Return all products from the rum catalog."""
        pass

    def get_watched_product(self, url: str) -> Optional[WatchedProduct]:
        """Return current price/stock for a specific product URL.
        Override in scrapers that support watched-product monitoring."""
        return None
