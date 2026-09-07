#!/usr/bin/env python3
"""Price Tracker with Alerts - command line entry point.

Usage examples
--------------
    python tracker.py add "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html" 55.00
    python tracker.py add <url> --pct-drop 10 --name "My product"
    python tracker.py import products.json
    python tracker.py list
    python tracker.py check
    python tracker.py check --only 2
    python tracker.py history 1
    python tracker.py chart 1 --output charts/mine.png
    python tracker.py remove 3

This file only parses arguments and dispatches. All real work lives in the
``price_tracker`` package.
"""

from __future__ import annotations

import argparse
import sys

from price_tracker import __version__, core


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracker.py",
        description="Track product prices and get alerted when they drop.",
    )
    parser.add_argument("--version", action="version", version=f"price-tracker {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="start tracking a product URL")
    p_add.add_argument("url")
    p_add.add_argument(
        "target_price",
        nargs="?",
        type=float,
        default=None,
        help="alert when price is at or below this value",
    )
    p_add.add_argument("--name", help="friendly name (otherwise learned from the page)")
    p_add.add_argument(
        "--pct-drop",
        type=float,
        dest="pct_drop",
        help="also alert when price falls this %% below the first recorded price",
    )

    p_import = sub.add_parser("import", help="load products from a products.json file")
    p_import.add_argument("path", nargs="?", default="products.json")

    sub.add_parser("list", help="show tracked products and their latest price")

    p_check = sub.add_parser("check", help="scrape all products now, store prices, send alerts")
    p_check.add_argument("--only", help="check just one product (id or url)")

    p_hist = sub.add_parser("history", help="print stored price history for a product")
    p_hist.add_argument("product", help="product id or url")
    p_hist.add_argument("--limit", type=int, help="show only the most recent N points")

    p_chart = sub.add_parser("chart", help="save a PNG chart of a product's price history")
    p_chart.add_argument("product", help="product id or url")
    p_chart.add_argument("--output", help="output PNG path (default: charts/product_<id>.png)")

    p_remove = sub.add_parser("remove", help="stop tracking a product")
    p_remove.add_argument("product", help="product id or url")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "add":
            core.run_add(args.url, args.target_price, name=args.name, pct_drop=args.pct_drop)
        elif args.command == "import":
            core.run_import(args.path)
        elif args.command == "list":
            core.run_list()
        elif args.command == "check":
            core.run_check(only=args.only)
        elif args.command == "history":
            core.run_history(args.product, limit=args.limit)
        elif args.command == "chart":
            from price_tracker import chart

            chart.build_chart(args.product, output_path=args.output)
        elif args.command == "remove":
            core.run_remove(args.product)
    except FileNotFoundError as exc:
        print(f"error: file not found - {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
