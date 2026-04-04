"""Astor Wines scraper — custom ASP.NET platform.

Astor's search results page returns all spirits matching a term.
Product cards are rendered in a standard .product-listing-item structure.

Catalog URL: https://www.astorwines.com/SearchResult.aspx?searchtype=Contains&term=Rum&viewall=true
Product URL: https://www.astorwines.com/item/{NUMERIC_ID}

NOTE: Astor's ASP.NET page uses viewstate — but since we only need to read
(not submit forms), a simple GET of the search results URL works fine.
If pagination is needed (viewall=true returns everything on one page), set
viewall=false and iterate Page=1, Page=2, etc.
"""

import re
import logging
from typing import List, Optional
from urllib.parse import urljoin

from .base import BaseScraper, Product, parse_price

logger = logging.getLogger(__name__)

BASE = "https://www.astorwines.com"
SEARCH_URL = f"{BASE}/SearchResult.aspx?searchtype=Contains&term=Rum&viewall=true"
# Fallback paginated URL if viewall doesn't work
PAGED_URL = f"{BASE}/SearchResult.aspx?searchtype=Contains&term=Rum&Page={{page}}"


class AstorScraper(BaseScraper):

    def get_catalog(self) -> List[Product]:
        logger.info("Astor: fetching rum search results (viewall=true)")
        soup = self._soup(SEARCH_URL)

        if soup is None:
            logger.warning("Astor: viewall fetch failed, trying paginated fallback")
            return self._paginated_catalog()

        products = self._parse_results_page(soup)

        # If viewall returned zero results, fall back to pagination
        if not products:
            logger.info("Astor: viewall returned nothing, trying paginated")
            return self._paginated_catalog()

        logger.info(f"Astor: total {len(products)} products")
        return products

    def _paginated_catalog(self) -> List[Product]:
        """Iterate through paginated search results."""
        products: List[Product] = []
        page = 1

        while True:
            url = PAGED_URL.format(page=page)
            logger.info(f"Astor: fetching page {page}")
            soup = self._soup(url)
            if soup is None:
                break

            page_products = self._parse_results_page(soup)
            if not page_products:
                break

            products.extend(page_products)
            logger.info(f"Astor: page {page} → {len(page_products)} products")

            # Check for a "next page" link
            next_link = soup.find("a", string=re.compile(r"next", re.I))
            if not next_link:
                break
            page += 1

        return products

    def _parse_results_page(self, soup) -> List[Product]:
        products: List[Product] = []

        # Astor wraps each result in .product-listing-item or similar
        items = (
            soup.select(".product-listing-item") or
            soup.select("li.product") or
            soup.select(".search-result-item") or
            soup.select("div.product-wrap") or
            soup.select(".product-row")
        )

        if not items:
            logger.debug("Astor: primary selectors empty, using link scan")
            seen = set()
            for a in soup.find_all("a", href=re.compile(r"/item/\d+")):
                href = a.get("href", "")
                url = href if href.startswith("http") else urljoin(BASE, href)
                m = re.search(r"/item/(\d+)", url)
                if not m or m.group(1) in seen:
                    continue
                item_id = m.group(1)
                seen.add(item_id)
                name = a.get_text(strip=True)
                if name and len(name) > 3:
                    products.append(Product(id=item_id, name=name, price=0.0, url=url))
            return products

        for item in items:
            p = self._parse_product_card(item)
            if p:
                products.append(p)

        return products

    def _parse_product_card(self, item) -> Optional[Product]:
        try:
            # Product link
            link = item.find("a", href=re.compile(r"/item/\d+"))
            if not link:
                return None

            href = link.get("href", "")
            url = href if href.startswith("http") else urljoin(BASE, href)
            m = re.search(r"/item/(\d+)", url)
            if not m:
                return None
            item_id = m.group(1)

            # Name
            name_el = (
                item.select_one(".product-title") or
                item.select_one(".item-name") or
                item.select_one("h2") or
                item.select_one("h3") or
                item.select_one("h4") or
                link
            )
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                return None

            # Price
            price_el = (
                item.select_one(".price") or
                item.select_one(".product-price") or
                item.select_one("span.price") or
                item.select_one(".item-price")
            )
            price = parse_price(price_el.get_text(strip=True)) if price_el else 0.0

            # Description
            desc_el = item.select_one(".description, .product-description, p.notes")
            description = desc_el.get_text(strip=True) if desc_el else ""

            return Product(id=item_id, name=name, price=price, url=url, description=description)

        except Exception as e:
            logger.debug(f"Astor: error parsing card: {e}")
            return None
