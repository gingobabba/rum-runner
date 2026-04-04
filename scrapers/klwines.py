"""KL Wines scraper — catalog new-product detection + price/stock watch.

KL Wines uses a custom .NET platform with DataDome bot protection.
If you see alerts like 'Request blocked', the GitHub Actions IP may be flagged.
Mitigation: the scraper uses realistic headers and rate limiting, which is
usually sufficient for low-frequency runs (hourly).

Catalog URL:  https://www.klwines.com/Products?filters=sv2_44&...
Product URLs: https://shop.klwines.com/products/details/{SKU}

NOTE ON SELECTORS: These were built from known URL patterns and common
KL Wines page structure. Run with --debug to save raw HTML and verify/fix
any selectors that break if KL redesigns their site.
"""

import re
import logging
from typing import List, Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, Product, WatchedProduct, parse_price

logger = logging.getLogger(__name__)

CATALOG_BASE = "https://www.klwines.com"
SHOP_BASE = "https://shop.klwines.com"
PAGE_SIZE = 96


class KLWinesScraper(BaseScraper):

    # ------------------------------------------------------------------
    # Catalog scraping (new product detection)
    # ------------------------------------------------------------------

    def get_catalog(self) -> List[Product]:
        """Page through the rum category and return all products."""
        products: List[Product] = []
        offset = 0

        while True:
            url = (
                f"{CATALOG_BASE}/Products"
                f"?filters=sv2_44&limit={PAGE_SIZE}&offset={offset}"
                f"&orderBy=0&searchText=*"
            )
            logger.info(f"KL catalog: fetching offset={offset}")
            soup = self._soup(url)

            if soup is None:
                logger.warning("KL catalog: fetch failed — skipping remainder")
                break

            if self._is_blocked(soup):
                logger.error("KL catalog: DataDome block detected — stopping")
                break

            page_products = self._parse_catalog_page(soup)
            if not page_products:
                logger.info("KL catalog: empty page, done paginating")
                break

            products.extend(page_products)
            logger.info(f"KL catalog: got {len(page_products)} products (total {len(products)})")

            if len(page_products) < PAGE_SIZE:
                break  # Last partial page

            offset += PAGE_SIZE

        return products

    def _parse_catalog_page(self, soup: BeautifulSoup) -> List[Product]:
        """
        Parse one catalog result page.

        KL Wines renders product cards as <li> elements inside a results list.
        Each card contains a link matching /products/details/{SKU}.
        Selector priority: specific class selectors → fallback link scan.
        """
        products: List[Product] = []

        # Primary selectors (adjust if KL redesigns)
        items = (
            soup.select("li.result") or
            soup.select(".product-listing-item") or
            soup.select("div.result")
        )

        if items:
            for item in items:
                product = self._parse_product_card(item)
                if product:
                    products.append(product)
        else:
            # Fallback: scan all links matching the product URL pattern
            logger.debug("KL catalog: primary selectors found nothing, falling back to link scan")
            seen_skus = set()
            for link in soup.find_all("a", href=re.compile(r"/products/details/\d+")):
                href = link.get("href", "")
                sku_m = re.search(r"/products/details/(\d+)", href)
                if not sku_m or sku_m.group(1) in seen_skus:
                    continue
                sku = sku_m.group(1)
                seen_skus.add(sku)
                url = href if href.startswith("http") else SHOP_BASE + href
                name = link.get_text(strip=True)
                if name:
                    products.append(Product(id=sku, name=name, price=0.0, url=url))

        return products

    def _parse_product_card(self, item) -> Optional[Product]:
        """Extract product info from a single result card element."""
        try:
            link = item.find("a", href=re.compile(r"/products/details/\d+"))
            if not link:
                return None

            href = link.get("href", "")
            sku_m = re.search(r"/products/details/(\d+)", href)
            if not sku_m:
                return None
            sku = sku_m.group(1)
            url = href if href.startswith("http") else SHOP_BASE + href

            # Name — prefer dedicated name element, fall back to link text
            name_el = (
                item.select_one(".result-info h3") or
                item.select_one(".product-name") or
                item.select_one("h2") or
                item.select_one("h3") or
                link
            )
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                return None

            # Price
            price_el = (
                item.select_one(".price") or
                item.select_one(".product-price") or
                item.select_one(".current-price") or
                item.select_one("[data-price]")
            )
            price_text = price_el.get_text(strip=True) if price_el else ""
            price = parse_price(price_text)

            # Description (may not appear on listing page — that's OK)
            desc_el = item.select_one(".notes, .description, .product-desc, .tasting-notes")
            description = desc_el.get_text(strip=True) if desc_el else ""

            return Product(id=sku, name=name, price=price, url=url, description=description)

        except Exception as e:
            logger.debug(f"KL: error parsing product card: {e}")
            return None

    # ------------------------------------------------------------------
    # Watched-product scraping (price + stock)
    # ------------------------------------------------------------------

    def get_watched_product(self, url: str) -> Optional[WatchedProduct]:
        """Scrape a specific KL product page for current price and stock."""
        soup = self._soup(url)
        if soup is None:
            # Network failure — caller decides how to handle
            return None

        if self._is_blocked(soup):
            logger.error(f"KL watch: DataDome block on {url}")
            return None

        try:
            # Name
            name_el = (
                soup.select_one("h1.detail-title") or
                soup.select_one("h1.product-title") or
                soup.select_one("h1.product-name") or
                soup.select_one("h1")
            )
            name = name_el.get_text(strip=True) if name_el else "Unknown"

            # Price
            price_el = (
                soup.select_one(".price-current") or
                soup.select_one(".product-price .price") or
                soup.select_one(".price") or
                soup.select_one("[data-price]") or
                soup.select_one(".current-price")
            )
            price = parse_price(price_el.get_text(strip=True)) if price_el else 0.0

            # Stock — KL shows quantities like "3 in stock" or "Only 2 left"
            stock, in_stock = self._extract_stock(soup)

            return WatchedProduct(name=name, price=price, stock=stock, url=url, in_stock=in_stock)

        except Exception as e:
            logger.error(f"KL watch: error parsing {url}: {e}")
            return None

    def _extract_stock(self, soup: BeautifulSoup):
        """Return (quantity: int, in_stock: bool). quantity=-1 if unknown."""
        full_text = soup.get_text(separator=" ", strip=True)

        # Explicit out-of-stock signals
        if re.search(r"\b(out of stock|sold out|not available|unavailable)\b", full_text, re.I):
            return 0, False

        # Dedicated stock element
        for sel in [".stock-quantity", ".inventory-quantity", ".availability", "[data-stock]"]:
            el = soup.select_one(sel)
            if el:
                m = re.search(r"(\d+)", el.get_text())
                if m:
                    qty = int(m.group(1))
                    return qty, qty > 0

        # Text patterns: "3 in stock", "Only 2 left", "2 remaining"
        m = re.search(r"(\d+)\s+(?:in stock|left|available|remaining)", full_text, re.I)
        if m:
            qty = int(m.group(1))
            return qty, qty > 0

        m = re.search(r"only\s+(\d+)\b", full_text, re.I)
        if m:
            qty = int(m.group(1))
            return qty, qty > 0

        # "In stock" with no quantity
        if re.search(r"\bin stock\b", full_text, re.I):
            return -1, True

        # Default: assume in stock if page loaded without error
        return -1, True

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _is_blocked(self, soup: BeautifulSoup) -> bool:
        """Detect a DataDome CAPTCHA/block page."""
        title = soup.find("title")
        if title and "datadome" in title.get_text().lower():
            return True
        if soup.find(id="datadome"):
            return True
        # Block pages often have very little content
        body = soup.find("body")
        if body and len(body.get_text(strip=True)) < 200:
            page_text = body.get_text(strip=True).lower()
            if any(w in page_text for w in ("blocked", "captcha", "access denied", "robot")):
                return True
        return False
