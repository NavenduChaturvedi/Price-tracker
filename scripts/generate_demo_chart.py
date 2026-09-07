"""Generate the sample chart shown in the README.

The demo target sites have stable prices, so a chart built from a few live
checks would just be a flat line. This script builds a *throwaway* database
with SYNTHETIC price history (clearly fake, for illustration only) and runs the
real ``price_tracker.chart`` code path against it to produce
``sample_output/price_history_chart.png``.

Run from the repo root:  python scripts/generate_demo_chart.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

# Point the tool at a scratch DB before anything imports the config module.
DEMO_DB = os.path.join("sample_output", "_demo.db")
os.environ["DB_PATH"] = DEMO_DB
os.makedirs("sample_output", exist_ok=True)
if os.path.exists(DEMO_DB):
    os.remove(DEMO_DB)

sys.path.insert(0, os.getcwd())

from price_tracker import chart, db  # noqa: E402

# A made-up but plausible two-week price slide for a fictional product.
SYNTHETIC_PRICES = [
    79.99, 79.99, 74.50, 74.50, 74.50, 69.99,
    69.99, 65.00, 65.00, 59.99, 62.50, 59.99, 54.99, 54.99,
]

db.init_db()
product = db.add_product(
    "https://example.com/demo-product",
    target_price=60.00,
    name="Demo product (synthetic data)",
)

start = datetime.now(timezone.utc) - timedelta(days=len(SYNTHETIC_PRICES))
with db._connect() as conn:  # internal helper is fine for a dev script
    conn.execute("UPDATE products SET currency = 'GBP' WHERE id = ?", (product.id,))
    for day, price in enumerate(SYNTHETIC_PRICES):
        ts = (start + timedelta(days=day)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO price_history (product_id, price, currency, in_stock, checked_at) "
            "VALUES (?, ?, 'GBP', 1, ?)",
            (product.id, price, ts),
        )

chart.build_chart(str(product.id), output_path=os.path.join("sample_output", "price_history_chart.png"))
os.remove(DEMO_DB)
print("done")
