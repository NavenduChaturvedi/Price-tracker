"""Command orchestration - the glue between the CLI and the other modules.

Each ``run_*`` function maps to one CLI sub-command. They own the control flow
and all the "one product failing must not kill the batch" error handling; the
lower-level modules just do their one job and raise typed exceptions.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Optional

from . import alerts, db, parsers, scraper
from .config import settings


# --------------------------------------------------------------------------
# add / import / list / remove
# --------------------------------------------------------------------------
def run_add(
    url: str,
    target_price: Optional[float],
    name: Optional[str] = None,
    pct_drop: Optional[float] = None,
) -> None:
    db.init_db()
    if target_price is None and pct_drop is None:
        raise ValueError("provide a target price and/or --pct-drop")
    product = db.add_product(url, target_price, name=name, pct_drop=pct_drop)
    print(f"tracking #{product.id}: {product.name or product.url}")
    _print_thresholds(product)


def run_import(path: str) -> None:
    """Load products from a products.json seed file."""
    db.init_db()
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    items = data.get("products", data if isinstance(data, list) else [])
    added = 0
    for item in items:
        url = item.get("url")
        if not url:
            print(f"  skipping entry without url: {item}")
            continue
        product = db.add_product(
            url,
            item.get("target_price"),
            name=item.get("name"),
            pct_drop=item.get("pct_drop"),
        )
        added += 1
        print(f"  #{product.id}: {product.name or product.url}")
    print(f"imported {added} product(s) from {path}")


def run_list() -> None:
    db.init_db()
    products = db.list_products(include_inactive=True)
    if not products:
        print("no products tracked yet - add one with 'python tracker.py add <url> <price>'")
        return

    for p in products:
        history = db.get_history(p.id, limit=1)
        latest = f"{history[-1].price:.2f}" if history else "-"
        flag = "" if p.active else "  (inactive)"
        print(f"#{p.id}  {(p.name or p.url)[:60]}{flag}")
        print(f"     url    : {p.url}")
        print(f"     latest : {latest} {p.currency or ''}".rstrip())
        _print_thresholds(p, indent="     ")


def run_remove(identifier: str) -> None:
    db.init_db()
    product = db.get_product(identifier)
    if product is None:
        print(f"no product matching {identifier!r}")
        return
    db.set_product_active(product.id, False)
    print(f"stopped tracking #{product.id} ({product.name or product.url})")


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------
@dataclass
class CheckOutcome:
    product: db.Product
    price: Optional[float] = None
    currency: Optional[str] = None
    in_stock: Optional[bool] = None
    alerted: bool = False
    error: Optional[str] = None


def run_check(only: Optional[str] = None) -> list[CheckOutcome]:
    """Scrape every active product once, store the price, and alert on drops.

    A failure on one product (site down, layout changed, robots.txt) is caught,
    recorded in the outcome, and the loop moves on.
    """
    db.init_db()

    if only is not None:
        product = db.get_product(only)
        products = [product] if product else []
        if not products:
            print(f"no product matching {only!r}")
            return []
    else:
        products = db.list_products()

    if not products:
        print("nothing to check - add a product first")
        return []

    session = _new_session()
    outcomes: list[CheckOutcome] = []

    for index, product in enumerate(products):
        if index > 0:
            _polite_pause()

        label = product.name or product.url
        print(f"checking #{product.id}: {label[:60]}")
        outcome = _check_one(product, session)
        outcomes.append(outcome)

        if outcome.error:
            print(f"    ! {outcome.error}")
        else:
            stock = "" if outcome.in_stock is None else (
                " (in stock)" if outcome.in_stock else " (out of stock)"
            )
            print(f"    price: {outcome.price:.2f} {outcome.currency or ''}{stock}".rstrip())
            if outcome.alerted:
                print("    -> alert sent")

    _print_summary(outcomes)
    return outcomes


def _check_one(product: db.Product, session) -> CheckOutcome:
    outcome = CheckOutcome(product=product)
    try:
        html = scraper.fetch(product.url, session=session)
        parsed = parsers.parse(product.url, html)
    except scraper.RobotsDisallowed as exc:
        outcome.error = f"skipped: {exc}"
        return outcome
    except scraper.FetchError as exc:
        outcome.error = f"fetch failed: {exc}"
        return outcome
    except parsers.ParseError as exc:
        outcome.error = f"parse failed: {exc}"
        return outcome
    except Exception as exc:  # defensive: never let one product crash the run
        outcome.error = f"unexpected error: {exc!r}"
        return outcome

    outcome.price = parsed.price
    outcome.currency = parsed.currency
    outcome.in_stock = parsed.in_stock

    db.update_product_meta(product.id, parsed.name, parsed.currency)
    db.record_price(product.id, parsed.price, parsed.currency, parsed.in_stock)

    should, reason = _should_alert(product, parsed.price)
    if should:
        subject = f"Price drop: {parsed.name or product.name or product.url}"
        body = _alert_body(product, parsed, reason)
        if alerts.send(subject, body):
            outcome.alerted = True
        db.record_alert(product.id, parsed.price)

    return outcome


def _should_alert(product: db.Product, price: float) -> tuple[bool, str]:
    """Decide whether this price warrants an alert.

    Rules:
      * fire if price <= target_price, or price is pct_drop% below the first
        recorded price;
      * but stay quiet if we already alerted at this price or lower, so a
        product that sits below target does not notify on every run. A *further*
        drop re-arms the alert.
    """
    triggers: list[str] = []

    if product.target_price is not None and price <= product.target_price:
        triggers.append(f"at/below target {product.target_price:.2f}")

    if product.pct_drop is not None:
        baseline = db.first_recorded_price(product.id)
        if baseline:
            drop_pct = (baseline - price) / baseline * 100
            if drop_pct >= product.pct_drop:
                triggers.append(
                    f"down {drop_pct:.1f}% from first seen {baseline:.2f}"
                )

    if not triggers:
        return False, ""

    if product.last_alert_price is not None and price >= product.last_alert_price:
        return False, ""  # already told the user about this (or a better) price

    return True, "; ".join(triggers)


def _alert_body(product: db.Product, parsed: parsers.ParsedProduct, reason: str) -> str:
    lines = [
        parsed.name or product.name or "(unnamed product)",
        product.url,
        "",
        f"Current price : {parsed.price:.2f} {parsed.currency or ''}".rstrip(),
    ]
    if product.target_price is not None:
        lines.append(f"Target price  : {product.target_price:.2f}")
    baseline = db.first_recorded_price(product.id)
    if baseline:
        lines.append(f"First seen at : {baseline:.2f}")
    lines.append(f"Why           : {reason}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------
def run_history(identifier: str, limit: Optional[int] = None) -> None:
    db.init_db()
    product = db.get_product(identifier)
    if product is None:
        print(f"no product matching {identifier!r}")
        return

    points = db.get_history(product.id, limit=limit)
    print(f"#{product.id}  {product.name or product.url}")
    if not points:
        print("  no price history yet - run 'python tracker.py check'")
        return

    prev = None
    for point in points:
        delta = ""
        if prev is not None:
            change = point.price - prev
            if abs(change) >= 0.005:
                delta = f"  ({'+' if change > 0 else ''}{change:.2f})"
        stock = "" if point.in_stock is None else (" in-stock" if point.in_stock else " OOS")
        print(f"  {point.checked_at}  {point.price:8.2f} {point.currency or '':<3}{delta}{stock}")
        prev = point.price

    low = min(p.price for p in points)
    high = max(p.price for p in points)
    noun = "point" if len(points) == 1 else "points"
    print(f"  --- {len(points)} {noun}, low {low:.2f}, high {high:.2f}")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _new_session():
    import requests

    return requests.Session()


def _polite_pause() -> None:
    """Wait between product requests: fixed delay + random jitter."""
    delay = settings.request_delay + random.uniform(0, settings.request_jitter)
    time.sleep(delay)


def _print_thresholds(product: db.Product, indent: str = "  ") -> None:
    parts = []
    if product.target_price is not None:
        parts.append(f"target <= {product.target_price:.2f}")
    if product.pct_drop is not None:
        parts.append(f"or drop >= {product.pct_drop:.0f}%")
    if parts:
        print(f"{indent}alert when {' '.join(parts)}")


def _print_summary(outcomes: list[CheckOutcome]) -> None:
    checked = len(outcomes)
    ok = sum(1 for o in outcomes if o.error is None)
    failed = checked - ok
    alerted = sum(1 for o in outcomes if o.alerted)
    print(
        f"\ndone: {checked} checked, {ok} ok, {failed} failed, {alerted} alert(s) sent"
    )
