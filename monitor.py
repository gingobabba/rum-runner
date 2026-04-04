#!/usr/bin/env python3
"""
Rum Price & Inventory Monitor
==============================
Runs all retailer scrapers, compares results against stored state,
fires Telegram alerts for new products, price drops, and low stock,
then writes updated state.json back to disk.

Usage:
    python monitor.py                  # normal run
    python monitor.py --debug          # save raw HTML to /tmp/ for selector debugging
    python monitor.py --dry-run        # run scrapers but don't send Telegram messages
    python monitor.py --retailer kl    # run only KL Wines
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

import yaml

from notifier import Notifier
from scrapers.base import Product, keywords_match
from scrapers.klwines import KLWinesScraper
from scrapers.bittersandbottles import BittersAndBottlesScraper
from scrapers.potomac import PotomacScraper
from scrapers.astor import AstorScraper
from scrapers.baytowne import BaytowneScraper

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("monitor")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.yml")
STATE_PATH = os.path.join(REPO_ROOT, "state.json")

# ---------------------------------------------------------------------------
# Config & state helpers
# ---------------------------------------------------------------------------


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        raw = f.read()
    # Expand ${ENV_VAR} placeholders
    for key, val in os.environ.items():
        raw = raw.replace(f"${{{key}}}", val)
    return yaml.safe_load(raw)


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"last_run": None, "retailers": {}}
    with open(STATE_PATH, "r") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
    logger.info(f"State saved to {STATE_PATH}")


def retailer_state(state: dict, key: str) -> dict:
    """Get (or initialise) the state sub-dict for a retailer."""
    if key not in state.setdefault("retailers", {}):
        state["retailers"][key] = {"catalog": {}, "watches": {}}
    return state["retailers"][key]


# ---------------------------------------------------------------------------
# Scraper factory
# ---------------------------------------------------------------------------

SCRAPER_MAP = {
    "klwines": KLWinesScraper,
    "bittersandbottles": BittersAndBottlesScraper,
    "potomac": PotomacScraper,
    "astor": AstorScraper,
    "baytowne": BaytowneScraper,
}

# ---------------------------------------------------------------------------
# Catalog monitoring (new product detection)
# ---------------------------------------------------------------------------


def process_catalog(
    retailer_key: str,
    retailer_name: str,
    products: List[Product],
    global_keywords: List[str],
    extra_keywords: List[str],
    r_state: dict,
    notifier: Notifier,
) -> None:
    """Compare fetched catalog against state; alert on new matching products."""
    known_catalog: dict = r_state.setdefault("catalog", {})
    all_keywords = global_keywords + extra_keywords

    new_count = 0
    for product in products:
        pid = product.id
        if pid not in known_catalog:
            # Brand-new product — check keywords
            search_text = f"{product.name} {product.description}"
            matched = keywords_match(search_text, all_keywords)
            if matched:
                logger.info(
                    f"[{retailer_name}] NEW MATCH: '{product.name}' (keyword: {matched})"
                )
                notifier.alert_new_product(
                    retailer=retailer_name,
                    product_name=product.name,
                    price=product.price,
                    url=product.url,
                    matched_keyword=matched,
                )
            # Record in state regardless of keyword match
            known_catalog[pid] = {
                "name": product.name,
                "price": product.price,
                "url": product.url,
                "first_seen": datetime.now(timezone.utc).isoformat(),
            }
            new_count += 1
        else:
            # Update name/price in case they changed (informational only for catalog)
            known_catalog[pid]["name"] = product.name
            known_catalog[pid]["price"] = product.price

    if new_count:
        logger.info(f"[{retailer_name}] {new_count} new products added to catalog state")
    else:
        logger.info(f"[{retailer_name}] No new products found (catalog: {len(known_catalog)})")


# ---------------------------------------------------------------------------
# Watched-product monitoring (KL Wines price + stock)
# ---------------------------------------------------------------------------


def process_watches(
    retailer_name: str,
    watches_config: List[dict],
    scraper: KLWinesScraper,
    r_state: dict,
    notifier: Notifier,
) -> None:
    """Check each watched URL for price drops, increases, and low stock."""
    watches_state: dict = r_state.setdefault("watches", {})

    for watch in watches_config:
        url = watch["url"]
        low_stock_threshold = watch.get("low_stock_threshold", 2)

        logger.info(f"[{retailer_name}] Checking watched product: {url}")
        current = scraper.get_watched_product(url)

        if current is None:
            # Network/scrape error — skip silently, do not send OOS alert.
            # A 403 or timeout doesn't mean the product is gone; we'll retry next run.
            logger.warning(f"[{retailer_name}] Could not fetch {url} — skipping (will retry next run)")
            continue

        prev = watches_state.get(url, {})
        prev_price = float(prev.get("price", 0))
        prev_in_stock = prev.get("in_stock", True)
        alerted_low_stock = prev.get("alerted_low_stock", False)

        # ---- Stock alerts ----
        if not current.in_stock and prev_in_stock:
            logger.info(f"[{retailer_name}] OUT OF STOCK: {current.name}")
            notifier.alert_out_of_stock(
                retailer=retailer_name,
                product_name=current.name,
                url=url,
            )
        elif current.in_stock and not prev_in_stock:
            logger.info(f"[{retailer_name}] BACK IN STOCK: {current.name}")
            notifier.alert_back_in_stock(
                retailer=retailer_name,
                product_name=current.name,
                price=current.price,
                url=url,
            )
            alerted_low_stock = False  # Reset so we can alert again if stock gets low

        if (
            current.in_stock
            and current.stock >= 0
            and current.stock <= low_stock_threshold
            and not alerted_low_stock
        ):
            logger.info(f"[{retailer_name}] LOW STOCK ({current.stock}): {current.name}")
            notifier.alert_low_stock(
                retailer=retailer_name,
                product_name=current.name,
                stock=current.stock,
                url=url,
            )
            alerted_low_stock = True
        elif current.stock > low_stock_threshold:
            alerted_low_stock = False  # Stock recovered — allow future low-stock alerts

        # ---- Price alerts ----
        if prev_price > 0 and current.price > 0:
            if current.price < prev_price:
                logger.info(
                    f"[{retailer_name}] PRICE DROP: {current.name} "
                    f"${prev_price:.2f} → ${current.price:.2f}"
                )
                notifier.alert_price_drop(
                    retailer=retailer_name,
                    product_name=current.name,
                    old_price=prev_price,
                    new_price=current.price,
                    url=url,
                )
            elif current.price > prev_price:
                logger.info(
                    f"[{retailer_name}] PRICE INCREASE: {current.name} "
                    f"${prev_price:.2f} → ${current.price:.2f}"
                )
                notifier.alert_price_increase(
                    retailer=retailer_name,
                    product_name=current.name,
                    old_price=prev_price,
                    new_price=current.price,
                    url=url,
                )

        # Persist updated watch state
        watches_state[url] = {
            "name": current.name,
            "price": current.price,
            "stock": current.stock,
            "in_stock": current.in_stock,
            "alerted_low_stock": alerted_low_stock,
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Rum price & inventory monitor")
    parser.add_argument("--debug", action="store_true", help="Save raw HTML to /tmp/ for debugging")
    parser.add_argument("--dry-run", action="store_true", help="Scrape but don't send Telegram alerts")
    parser.add_argument("--retailer", help="Run only this retailer key (e.g. klwines)")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load config & state
    config = load_config()
    state = load_state()

    # Set up Telegram notifier
    tg_cfg = config.get("telegram", {})
    bot_token = "" if args.dry_run else tg_cfg.get("bot_token", "")
    chat_id = "" if args.dry_run else tg_cfg.get("chat_id", "")
    notifier = Notifier(bot_token=bot_token, chat_id=chat_id)

    global_keywords: List[str] = config.get("global_keywords", [])
    retailers_cfg: dict = config.get("retailers", {})

    errors = []

    for retailer_key, retailer_cfg in retailers_cfg.items():
        if not retailer_cfg.get("enabled", True):
            logger.info(f"Skipping disabled retailer: {retailer_key}")
            continue
        if args.retailer and retailer_key != args.retailer:
            continue

        retailer_name = retailer_cfg.get("name", retailer_key)
        logger.info(f"=== Processing {retailer_name} ===")

        ScraperClass = SCRAPER_MAP.get(retailer_key)
        if not ScraperClass:
            logger.warning(f"No scraper found for {retailer_key}")
            continue

        scraper = ScraperClass(config=retailer_cfg, debug=args.debug)
        r_state = retailer_state(state, retailer_key)

        # --- Catalog (new product detection) ---
        try:
            products = scraper.get_catalog()
            if products:
                extra_kw = retailer_cfg.get("extra_keywords", [])
                process_catalog(
                    retailer_key=retailer_key,
                    retailer_name=retailer_name,
                    products=products,
                    global_keywords=global_keywords,
                    extra_keywords=extra_kw,
                    r_state=r_state,
                    notifier=notifier,
                )
            elif scraper.access_denied:
                logger.warning(
                    f"[{retailer_name}] Blocked by bot protection (403) — skipping, will retry next run"
                )
            else:
                logger.warning(f"[{retailer_name}] Catalog returned 0 products — scraper may need selector update")
                errors.append(retailer_name)
        except Exception as e:
            logger.exception(f"[{retailer_name}] Catalog scrape error: {e}")
            errors.append(retailer_name)

        # --- Watched products (KL Wines only) ---
        watches_config = retailer_cfg.get("watches", [])
        if watches_config and isinstance(scraper, KLWinesScraper):
            try:
                process_watches(
                    retailer_name=retailer_name,
                    watches_config=watches_config,
                    scraper=scraper,
                    r_state=r_state,
                    notifier=notifier,
                )
            except Exception as e:
                logger.exception(f"[{retailer_name}] Watch scrape error: {e}")
                errors.append(f"{retailer_name} (watches)")

    # Save updated state
    save_state(state)

    if errors:
        logger.warning(f"Retailers with errors or empty results: {', '.join(errors)}")
        sys.exit(1)  # Non-zero exit signals GH Actions that something went wrong
    else:
        logger.info("All retailers processed successfully")


if __name__ == "__main__":
    main()
    
