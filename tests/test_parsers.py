"""Unit tests for the HTML parsers and price cleaning helpers.

These run offline against small hand-written HTML snippets that mirror the real
markup of the target sites, so the test suite never touches the network.

    python -m pytest            (needs: pip install pytest)
"""

from price_tracker import parsers
from price_tracker.parsers import ParseError


def test_money_to_float_variants():
    assert parsers._money_to_float("£51.77") == 51.77
    assert parsers._money_to_float("$1,299.00") == 1299.0
    assert parsers._money_to_float("Now 45.50 EUR") == 45.50


def test_money_to_float_rejects_non_numeric():
    try:
        parsers._money_to_float("call for price")
    except ParseError:
        pass
    else:
        raise AssertionError("expected ParseError")


def test_detect_currency():
    assert parsers._detect_currency("£10") == "GBP"
    assert parsers._detect_currency("total: 10 usd") == "USD"
    assert parsers._detect_currency("10.00") is None


BOOKS_HTML = """
<div class="product_main">
  <h1>A Light in the Attic</h1>
  <p class="price_color">£51.77</p>
  <p class="instock availability"><i class="icon-ok"></i> In stock (22 available)</p>
</div>
"""

SCRAPEME_HTML = """
<div class="summary entry-summary">
  <h1 class="product_title entry-title">Bulbasaur</h1>
  <p class="price"><span class="woocommerce-Price-amount amount"><bdi>
    <span class="woocommerce-Price-currencySymbol">&pound;</span>63.00</bdi></span></p>
  <p class="stock in-stock">45 in stock</p>
</div>
"""


def test_parse_books_toscrape():
    result = parsers.parse("https://books.toscrape.com/catalogue/x_1/index.html", BOOKS_HTML)
    assert result.price == 51.77
    assert result.currency == "GBP"
    assert result.name == "A Light in the Attic"
    assert result.in_stock is True


def test_parse_scrapeme_live():
    result = parsers.parse("https://scrapeme.live/shop/Bulbasaur/", SCRAPEME_HTML)
    assert result.price == 63.00
    assert result.currency == "GBP"
    assert result.name == "Bulbasaur"
    assert result.in_stock is True


def test_parse_missing_price_raises():
    try:
        parsers.parse("https://books.toscrape.com/catalogue/x_1/index.html", "<html></html>")
    except ParseError:
        pass
    else:
        raise AssertionError("expected ParseError")


def test_generic_parser_reads_meta_tag():
    html = (
        '<meta property="product:price:amount" content="19.99">'
        '<meta property="product:price:currency" content="USD">'
    )
    result = parsers.parse("https://some-unknown-shop.example/item/5", html)
    assert result.price == 19.99
    assert result.currency == "USD"
