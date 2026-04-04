"""Potomac Wine & Spirits scraper — OpenCart platform.

OpenCart renders standard server-side HTML. Products are in a grid/list of
.product-thumb elements (OpenCart's default template class).

Catalog URL: https://potomacwines.com/spirits/rum?limit=100&page={N}

NOTE: If Potomac has updated their theme, adjust the selectors in
_parse_product_card() and run with --debug to inspect raw HTML.
"""

import re
import logging
from typing import List, Optional
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from .base import BaseScraper, Product, parse_price

logger = logging.getLogger(__name__)

BASE = "https://potomacwines.com"
RUM_PATH = "/spirits/rum"
PAGE_SIZE = 100


class PotomacScraper(BaseScraper):

    def get_catalog(self) -> List[Product]:
        products: List[Product] = []
        page = 1

        while True:
            url = f"{BASE}{RUM_PATH}?limit={PAGE_SIZE}&page={page}"
            logger.info(f"Potomac: fetching page {page}")
            soup = self._soup(url)

            if soup is None:
                logger.warning("Potomac: fetch failed")
                break

            page_products = self._parse_catalog_page(soup)
            if not page_products:
                logger.info("Potomac: no products on page, done")
                break

            products.extend(page_products)
            logger.info(f"Potomac: page {page} → {len(page_products)} products")

            if len(page_products) < PAGE_SIZE:
                break
            page += 1

        logger.info(f"Potomac: total {len(products)} products")
        return products

    def _parse_catalog_page(self, soup) -> List[Product]:
        products: List[Product] = []

        # OpenCart default: .product-thumb wraps each item
        items = (
            soup.select(".product-thumb") or
            soup.select(".product-layout") or
            soup.select(".product-item") or
            soup.select("div.product")
        )

        if not items:
            logger.debug("Potomac: primary selectors empty, falling back to link scan")
            seen = set()
            for a in soup.find_all("a", href=re.compile(r"/spirits/rum/")):
                href = a.get("href", "")
                url = href if href.startswith("http") else urljoin(BASE, href)
                if url in seen:
                    continue
                seen.add(url)
                name = a.get_text(strip=True)
                if name and len(name) > 3:
                    slug = url.rstrip("/").rsplit("/", 1)[-1]
                    products.append(Product(id=slug, name=name, price=0.0, url=url))
            return products

        for item in items:
            p = self._parse_product_card(item)
            if p:
                products.append(p)

        return products

    def _parse_product_card(self, item) -> Optional[Product]:
        try:
            # Link — OpenCart wraps the image and name in <a> tags
            link = (
                item.select_one(".caption a") or
                item.select_one("h4 a") or
                item.select_one("h3 a") or
                item.find("a", href=re.compile(r"/spirits/rum/"))
            )
            if not link:
                return None

            href = link.get("href", "")
            url = href if href.startswith("http") else urljoin(BASE, href)
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            if not slug:
                return None

            # Name
            name_el = (
                item.select_one(".caption h4") or
                item.select_one("h4") or
                item.select_one("h3") or
                link
            )
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                return None

            # Price — OpenCart puts price in .price
            price_el = (
                item.select_one(".price") or
                item.select_one(".product-price") or
                item.select_one("span.price")
            )
            price_text = price_el.get_text(strip=True) if price_el else ""
            # OpenCart sometimes shows "Ex Tax: $XX.XX" — grab the first dollar amount
            price = parse_price(price_text)

            # Description (usually not on listing page in OpenCart)
            desc_el = item.select_one(".description, p.description")
            description = desc_el.get_text(strip=True) if desc_el else ""

            return Product(id=slug, name=name, price=price, url=url, description=description)

        except Exception as e:
            logger.debug(f"Potomac: error parsing card: {e}")
            return None
