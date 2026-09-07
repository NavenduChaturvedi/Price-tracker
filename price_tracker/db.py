"""SQLite persistence layer.

Two tables:

    products        - one row per tracked URL (the "what to watch" list)
    price_history   - one row per successful check (the time series)

SQLite is used because it needs no server, ships with Python, and a single
``.db`` file is trivial to inspect or hand over - while still being a real
relational store with foreign keys and indexes for the portfolio story.

Every public function opens a short-lived connection and closes it. That is
slightly less efficient than a shared connection but keeps the module free of
global state and safe to call from anywhere.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT    NOT NULL UNIQUE,
    name            TEXT,
    target_price    REAL,
    pct_drop        REAL,            -- alert if price falls this % below the first recorded price
    currency        TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL,
    last_alert_price REAL,           -- price at which we last alerted (dedupe)
    last_alert_at   TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    price       REAL    NOT NULL,
    currency    TEXT,
    in_stock    INTEGER,             -- 1 / 0 / NULL (unknown)
    checked_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_product_time
    ON price_history (product_id, checked_at);
"""


@dataclass
class Product:
    id: int
    url: str
    name: str | None
    target_price: float | None
    pct_drop: float | None
    currency: str | None
    active: int
    created_at: str
    last_alert_price: float | None
    last_alert_at: str | None


@dataclass
class PricePoint:
    price: float
    currency: str | None
    in_stock: int | None
    checked_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables/indexes if they do not exist. Safe to call every run."""
    with _connect() as conn:
        conn.executescript(SCHEMA)


# --------------------------------------------------------------------------
# products
# --------------------------------------------------------------------------
def _row_to_product(row: sqlite3.Row) -> Product:
    return Product(**dict(row))


def add_product(
    url: str,
    target_price: float | None,
    name: str | None = None,
    pct_drop: float | None = None,
) -> Product:
    """Insert a product, or update the thresholds if the URL already exists.

    Re-adding an existing URL is treated as "change my alert settings" rather
    than an error - that is what a user typing the command again expects.
    """
    with _connect() as conn:
        existing = conn.execute("SELECT * FROM products WHERE url = ?", (url,)).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO products (url, name, target_price, pct_drop, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (url, name, target_price, pct_drop, _utc_now_iso()),
            )
        else:
            conn.execute(
                """UPDATE products
                      SET target_price = ?,
                          pct_drop     = ?,
                          name         = COALESCE(?, name),
                          active       = 1
                    WHERE url = ?""",
                (target_price, pct_drop, name, url),
            )
        row = conn.execute("SELECT * FROM products WHERE url = ?", (url,)).fetchone()
        return _row_to_product(row)


def get_product(identifier: str) -> Product | None:
    """Look a product up by numeric id or by URL."""
    with _connect() as conn:
        if identifier.isdigit():
            row = conn.execute("SELECT * FROM products WHERE id = ?", (int(identifier),)).fetchone()
        else:
            row = conn.execute("SELECT * FROM products WHERE url = ?", (identifier,)).fetchone()
    return _row_to_product(row) if row else None


def list_products(include_inactive: bool = False) -> list[Product]:
    query = "SELECT * FROM products"
    if not include_inactive:
        query += " WHERE active = 1"
    query += " ORDER BY id"
    with _connect() as conn:
        return [_row_to_product(r) for r in conn.execute(query).fetchall()]


def set_product_active(product_id: int, active: bool) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE products SET active = ? WHERE id = ?",
            (1 if active else 0, product_id),
        )


def update_product_meta(product_id: int, name: str | None, currency: str | None) -> None:
    """Backfill name/currency once we have learned them from a real scrape."""
    with _connect() as conn:
        conn.execute(
            """UPDATE products
                  SET name     = COALESCE(name, ?),
                      currency = COALESCE(?, currency)
                WHERE id = ?""",
            (name, currency, product_id),
        )


def record_alert(product_id: int, price: float) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE products SET last_alert_price = ?, last_alert_at = ? WHERE id = ?",
            (price, _utc_now_iso(), product_id),
        )


# --------------------------------------------------------------------------
# price_history
# --------------------------------------------------------------------------
def record_price(
    product_id: int,
    price: float,
    currency: str | None,
    in_stock: bool | None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO price_history (product_id, price, currency, in_stock, checked_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                product_id,
                price,
                currency,
                None if in_stock is None else int(in_stock),
                _utc_now_iso(),
            ),
        )


def get_history(product_id: int, limit: int | None = None) -> list[PricePoint]:
    query = (
        "SELECT price, currency, in_stock, checked_at "
        "FROM price_history WHERE product_id = ? ORDER BY checked_at"
    )
    params: tuple = (product_id,)
    if limit is not None:
        query += " DESC LIMIT ?"
        params = (product_id, limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    points = [
        PricePoint(
            price=r["price"],
            currency=r["currency"],
            in_stock=r["in_stock"],
            checked_at=r["checked_at"],
        )
        for r in rows
    ]
    if limit is not None:
        points.reverse()  # undo the DESC we used to grab the most recent N
    return points


def first_recorded_price(product_id: int) -> float | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT price FROM price_history WHERE product_id = ? ORDER BY checked_at LIMIT 1",
            (product_id,),
        ).fetchone()
    return row["price"] if row else None
