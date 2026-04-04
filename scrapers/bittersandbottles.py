"""Bitter & Bottles scraper — Shopify JSON API.

Shopify exposes /collections/{handle}/products.json with full product data
including title, body_html (description), price, and handle. No HTML parsing
needed. Paginate with ?page=N until an empty products array is returned.

Catalog URL: https://www.bittersandbottles.com/collections/rum/products.json
"""

import logging
from typing import List, Optional
from html import unescape

from bs4 import BeautifulSoup

from .base import BaseScraper, Product, parse_price

logger = logging.getLogger(__name__)

BASE = "https://www.bittersandbottles.com"
PRODUCTS_PER_PAGE = 250  # Shopify max


class BittersAndBottlesScraper(BaseScraper):

    def get_catalog(self) -> List[Product]:
        """Fetch rum catalog via Shopify products.json endpoint."""
        products: List[Product] = []
        page = 1

        while True:
            url = (
                f"{BASE}/collections/rum/products.json"
                f"?limit={PRODUCTS_PER_PAGE}&page={page}"
            )
            logger.info(f"Bitter & Bottles: fetching page {page}")
            resp = self._get(url, headers={"Accept": "application/json"})
            if resp is None:
                logger.warning("Bitter & Bottles: fetch failed")
                break

            try:
                data = resp.json()
            except Exception as e:
                logger.error(f"Bitter & Bottles: JSON parse error: {e}")
                break

            batch = data.get("products", [])
            if not batch:
                break

            for item in batch:
                product = self._parse_shopify_product(item)
                if product:
                    products.append(product)

            logger.info(f"Bitter & Bottles: page {page} → {len(batch)} products")

            if len(batch) < PRODUCTS_PER_PAGE:
                break
            page += 1

        logger.info(f"Bitter & Bottles: total {len(products)} products")
        return products

    def _parse_shopify_product(self, item: dict) -> Optional[Product]:
        """Convert a Shopify product dict to a Product."""
        try:
            handle = item.get("handle", "")
            title = item.get("title", "").strip()
            if not title:
                return None

            url = f"{BASE}/products/{handle}"

            # Price from first variant
            variants = item.get("variants", [])
            price = 0.0
            if variants:
                price = parse_price(str(variants[0].get("price", "0")))

            # Strip HTML tags from description
            body_html = item.get("body_html") or ""
            description = BeautifulSoup(body_html, "lxml").get_text(separator=" ", strip=True)
            description = unescape(description)

            return Product(
                id=handle,
                name=title,
                price=price,
                url=url,
                description=description,
            )
        except Exception as e:
            logger.debug(f"Bitter & Bottles: error parsing product: {e}")
            return None
