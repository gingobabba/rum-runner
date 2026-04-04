"""Astor Wines scraper — custom ASP.NET platform.

Astor's spirits search page returns paginated results for a given category.
Product cards are rendered in .item-teaser containers.

Catalog URL: https://www.astorwines.com/SpiritsSearchResult.aspx?p=1&search=Advanced&searchtype=Contains&term=&cat=2&style=1_22&srt=4
Product URL: https://www.astorwines.com/item/{NUMERIC_ID}

Pagination is handled via &Page=N appended to the base URL.
"""

import re
import logging
from typing import List, Optional
from urllib.parse import urljoin

from .base import BaseScraper, Product, parse_price

logger = logging.getLogger(__name__)

BASE = "https://www.astorwines.com"
# Rum category: cat=2 (spirits), style=1_22 (rum)
SEARCH_URL = f"{BASE}/SpiritsSearchResult.aspx?p=1&search=Advanced&searchtype=Contains&term=&cat=2&style=1_22&srt=4"


class AstorScraper(BaseScraper):

    def get_catalog(self) -> List[Product]:
        products: List[Product] = []
        page = 1

        while True:
            url = SEARCH_URL if page == 1 else f"{SEARCH_URL}&Page={page}"
            logger.info(f"Astor: fetching page {page}")
            soup = self._soup(url)
            if soup is None:
                break

            items = soup.select(".item-teaser")
            if not items:
                break

            page_products = [p for item in items for p in [self._parse_product_card(item)] if p]
            if not page_products:
                break

            products.extend(page_products)
            logger.info(f"Astor: page {page} → {len(page_products)} products")

            # Check for a next-page link
            next_page = str(page + 1)
            has_next = bool(soup.select_one(f'.pagination a[href*="Page={next_page}"]'))
            if not has_next:
                break
            page += 1

        logger.info(f"Astor: total {len(products)} products")
        return products

    def _parse_product_card(self, item) -> Optional[Product]:
        try:
            link = item.select_one("a.item-name") or item.find("a", href=re.compile(r"^item/"))
            if not link:
                return None

            href = link.get("href", "")
            if not href.startswith("http"):
                href = urljoin(BASE + "/", href)
            m = re.search(r"/item/(\d+)", href)
            if not m:
                return None
            item_id = m.group(1)

            name = link.get_text(strip=True)
            if not name:
                return None

            price_el = item.select_one(".price-value.price-bottle.display-2")
            price = parse_price(price_el.get_text(strip=True)) if price_el else 0.0

            desc_el = item.select_one(".item-description")
            description = desc_el.get_text(strip=True) if desc_el else ""

            return Product(id=item_id, name=name, price=price, url=href, description=description)

        except Exception as e:
            logger.debug(f"Astor: error parsing card: {e}")
            return None
