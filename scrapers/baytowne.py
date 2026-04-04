"""Baytowne Wine scraper.

Baytowne's platform is unconfirmed — the scraper tries multiple URL patterns
for their rum/spirits category and uses broad selectors to handle different
possible page structures.

Catalog URLs tried (in order):
  1. https://baytownewine.com/spirits/rum
  2. https://baytownewine.com/spirits  (scan for rum items)
  3. https://baytownewine.com/search?q=rum

Run with --debug to save raw HTML and refine selectors if needed.
"""

import re
import logging
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import Tag

from .base import BaseScraper, Product, parse_price

logger = logging.getLogger(__name__)

BASE = "https://baytownewine.com"

CATALOG_URLS = [
    f"{BASE}/spirits/rum",
    f"{BASE}/s/category/spirits/rum",
    f"{BASE}/spirits",
    f"{BASE}/search?q=rum",
]


class BaytowneScraper(BaseScraper):

    def get_catalog(self) -> List[Product]:
        for url in CATALOG_URLS:
            logger.info(f"Baytowne: trying {url}")
            soup = self._soup(url)
            if soup is None:
                continue

            products = self._parse_catalog_page(soup, base_url=url)
            if products:
                logger.info(f"Baytowne: found {len(products)} products at {url}")
                return products
            else:
                logger.info(f"Baytowne: no products at {url}, trying next URL")

        logger.warning("Baytowne: could not find rum catalog at any known URL")
        return []

    def _parse_catalog_page(self, soup, base_url: str = BASE) -> List[Product]:
        products: List[Product] = []

        # Try common product card containers
        items = (
            soup.select(".product-card") or
            soup.select(".product-item") or
            soup.select(".product-tile") or
            soup.select("li.product") or
            soup.select(".grid-item") or
            soup.select("article.product") or
            soup.select("[data-product-id]") or
            soup.select(".item-card")
        )

        if items:
            for item in items:
                p = self._parse_product_card(item, base_url)
                if p:
                    products.append(p)
        else:
            # Fallback: look for any anchor that looks like a product link
            logger.debug("Baytowne: no standard containers found, doing link scan")
            seen = set()
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                # Skip navigation/category links — product links usually have a specific slug
                if any(skip in href for skip in ["#", "mailto:", "tel:", "?sort", "?page"]):
                    continue
                if not re.search(r"/(?:product|item|spirits|rum)/[a-z0-9-]{4,}", href, re.I):
                    continue
                url = href if href.startswith("http") else urljoin(base_url, href)
                if url in seen:
                    continue
                seen.add(url)
                name = a.get_text(strip=True)
                if name and len(name) > 4:
                    slug = url.rstrip("/").rsplit("/", 1)[-1]
                    products.append(Product(id=slug, name=name, price=0.0, url=url))

        return products

    def _parse_product_card(self, item: Tag, base_url: str) -> Optional[Product]:
        try:
            # Find the main product link
            link = (
                item.select_one("a.product-link") or
                item.select_one("a.product-title") or
                item.select_one(".product-name a") or
                item.select_one("h2 a") or
                item.select_one("h3 a") or
                item.find("a", href=True)
            )
            if not link:
                return None

            href = link.get("href", "")
            url = href if href.startswith("http") else urljoin(base_url, href)
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            if not slug:
                return None

            # Name
            name_el = (
                item.select_one(".product-name") or
                item.select_one(".product-title") or
                item.select_one("h2") or
                item.select_one("h3") or
                link
            )
            name = name_el.get_text(strip=True) if name_el else ""
            if not name or len(name) < 3:
                return None

            # Price
            price_el = (
                item.select_one(".price") or
                item.select_one(".product-price") or
                item.select_one("[data-price]") or
                item.select_one("span.amount")
            )
            price = parse_price(price_el.get_text(strip=True)) if price_el else 0.0

            # Description
            desc_el = item.select_one(".description, .product-description, p")
            description = desc_el.get_text(strip=True) if desc_el else ""

            return Product(id=slug, name=name, price=price, url=url, description=description)

        except Exception as e:
            logger.debug(f"Baytowne: error parsing card: {e}")
            return None
