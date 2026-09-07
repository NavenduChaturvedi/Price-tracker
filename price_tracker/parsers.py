"""Turn a product page's HTML into a price.

Design: one small parser function per site, chosen by hostname. A generic
parser is tried as a last resort (and for any site we have not written a
dedicated function for yet). Adding support for a new store therefore means
writing one function and adding one line to ``_PARSERS`` - nothing else in the
codebase changes.

Each parser returns a ``ParsedProduct`` or raises ``ParseError`` when the
element it needs is missing (page redesign, bot-block page, wrong URL, ...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup


class ParseError(Exception):
    """Raised when a price cannot be extracted from a page."""


@dataclass
class ParsedProduct:
    price: float
    currency: Optional[str]
    name: Optional[str]
    in_stock: Optional[bool]


_CURRENCY_SYMBOLS = {
    "£": "GBP",
    "$": "USD",
    "€": "EUR",
    "₹": "INR",
}


def _money_to_float(text: str) -> float:
    """Extract the first numeric amount from a messy price string.

    Handles things like '£51.77', '$1,299.00', 'Now 45.50 EUR'. Raises
    ParseError if nothing number-like is present.
    """
    cleaned = text.replace(",", "").strip()
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    if not match:
        raise ParseError(f"no numeric amount in price text: {text!r}")
    return float(match.group())


def _detect_currency(text: str) -> Optional[str]:
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    match = re.search(r"\b(USD|GBP|EUR|INR|CAD|AUD)\b", text, re.IGNORECASE)
    return match.group(1).upper() if match else None


# --------------------------------------------------------------------------
# site-specific parsers
# --------------------------------------------------------------------------
def parse_books_toscrape(soup: BeautifulSoup) -> ParsedProduct:
    """books.toscrape.com - a static scraping sandbox."""
    price_el = soup.select_one("div.product_main p.price_color") or soup.select_one(
        "p.price_color"
    )
    if price_el is None:
        raise ParseError("books.toscrape: price_color element not found")
    price_text = price_el.get_text(strip=True)

    name_el = soup.select_one("div.product_main h1")
    stock_el = soup.select_one("p.availability, p.instock.availability")
    in_stock = None
    if stock_el is not None:
        in_stock = "in stock" in stock_el.get_text(strip=True).lower()

    return ParsedProduct(
        price=_money_to_float(price_text),
        currency=_detect_currency(price_text),
        name=name_el.get_text(strip=True) if name_el else None,
        in_stock=in_stock,
    )


def parse_scrapeme_live(soup: BeautifulSoup) -> ParsedProduct:
    """scrapeme.live - a WooCommerce scraping sandbox."""
    price_el = soup.select_one("p.price .amount") or soup.select_one("p.price")
    if price_el is None:
        raise ParseError("scrapeme.live: price element not found")
    price_text = price_el.get_text(" ", strip=True)

    name_el = soup.select_one("h1.product_title")
    stock_el = soup.select_one("p.stock")
    in_stock = None
    if stock_el is not None:
        classes = stock_el.get("class", [])
        in_stock = "in-stock" in classes or "in stock" in stock_el.get_text().lower()

    return ParsedProduct(
        price=_money_to_float(price_text),
        currency=_detect_currency(price_text),
        name=name_el.get_text(strip=True) if name_el else None,
        in_stock=in_stock,
    )


def parse_generic(soup: BeautifulSoup) -> ParsedProduct:
    """Best-effort fallback for sites without a dedicated parser.

    Tries, in order: Open Graph / itemprop price meta tags, then a handful of
    common price CSS classes. This will not beat serious anti-bot defences, but
    it makes the tool useful against simple sites for free.
    """
    # 1. structured metadata
    for selector, attr in (
        ('meta[property="product:price:amount"]', "content"),
        ('meta[property="og:price:amount"]', "content"),
        ('meta[itemprop="price"]', "content"),
    ):
        el = soup.select_one(selector)
        if el and el.get(attr):
            text = el[attr]
            return ParsedProduct(
                price=_money_to_float(text),
                currency=_detect_currency(text) or _og_currency(soup),
                name=_generic_name(soup),
                in_stock=None,
            )

    # 2. visible price-ish elements
    for selector in (
        "[itemprop=price]",
        ".price .amount",
        ".product-price",
        ".price",
        "#priceblock_ourprice",
    ):
        el = soup.select_one(selector)
        if el:
            text = el.get_text(" ", strip=True)
            if re.search(r"\d", text):
                return ParsedProduct(
                    price=_money_to_float(text),
                    currency=_detect_currency(text),
                    name=_generic_name(soup),
                    in_stock=None,
                )

    raise ParseError("generic parser: could not locate a price on the page")


def _og_currency(soup: BeautifulSoup) -> Optional[str]:
    el = soup.select_one(
        'meta[property="product:price:currency"], meta[property="og:price:currency"]'
    )
    return el["content"].upper() if el and el.get("content") else None


def _generic_name(soup: BeautifulSoup) -> Optional[str]:
    og = soup.select_one('meta[property="og:title"]')
    if og and og.get("content"):
        return og["content"].strip()
    if soup.h1:
        return soup.h1.get_text(strip=True)
    if soup.title:
        return soup.title.get_text(strip=True)
    return None


# hostname -> parser. Suffix match, so 'www.' and other sub-domains still work.
_PARSERS: dict[str, Callable[[BeautifulSoup], ParsedProduct]] = {
    "books.toscrape.com": parse_books_toscrape,
    "scrapeme.live": parse_scrapeme_live,
}


def parse(url: str, html: str) -> ParsedProduct:
    """Public entry point: pick a parser by hostname and run it."""
    host = (urlparse(url).netloc or "").lower()
    soup = BeautifulSoup(html, "lxml")

    for known_host, parser in _PARSERS.items():
        if host == known_host or host.endswith("." + known_host):
            return parser(soup)

    return parse_generic(soup)


def supported_sites() -> list[str]:
    return sorted(_PARSERS)
