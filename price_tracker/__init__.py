"""Price tracker package.

A small, dependency-light tool that scrapes product prices from a couple of
scraping-friendly demo stores, keeps a price history in SQLite, and fires an
alert (Telegram or email) when a price drops below a threshold.

Modules are intentionally split by responsibility so each one stays small and
testable:

    config   - read settings from .env / environment, with defaults
    db       - the SQLite persistence layer
    scraper  - HTTP fetching, robots.txt checks, retry/back-off
    parsers  - turn a page's HTML into a price (one function per site)
    alerts   - deliver a notification, never raising on failure
    core     - glue the pieces together for each CLI command
    chart    - optional matplotlib price-history chart
"""

__version__ = "1.0.0"
