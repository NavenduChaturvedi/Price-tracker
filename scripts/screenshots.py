"""Generate the dashboard screenshots used in the README.

Dev-only. Needs the web extras plus Playwright:

    pip install -r requirements-web.txt playwright
    python -m playwright install chromium
    python scripts/screenshots.py

It builds a throwaway database with a little history, serves the app on a
background thread, and saves PNGs to docs/.
"""

from __future__ import annotations

import os
import sys
import threading
import time

DEMO_DB = os.path.join("docs", "_screenshot.db")
os.environ["DB_PATH"] = DEMO_DB
os.environ.setdefault("FLASK_SECRET", "screenshot")
os.makedirs("docs", exist_ok=True)
if os.path.exists(DEMO_DB):
    os.remove(DEMO_DB)

sys.path.insert(0, os.getcwd())

from price_tracker import core, db  # noqa: E402
from webapp.app import app  # noqa: E402

# --- seed a small, believable dataset -------------------------------------
core.run_import("products.json")
core.run_check()

_history = {
    1: [(53.0, "2026-09-01"), (52.4, "2026-09-03"), (51.77, "2026-09-05")],
    2: [(55.0, "2026-09-01"), (53.74, "2026-09-04")],
}
with db._connect() as conn:
    for product_id, points in _history.items():
        for price, day in points:
            conn.execute(
                "INSERT INTO price_history (product_id, price, currency, in_stock, checked_at) "
                "VALUES (?, ?, 'GBP', 1, ?)",
                (product_id, price, f"{day}T09:00:00+00:00"),
            )

# --- serve + shoot -------------------------------------------------------
PORT = 5055
threading.Thread(target=lambda: app.run(port=PORT, use_reloader=False), daemon=True).start()
time.sleep(1.5)

from playwright.sync_api import sync_playwright  # noqa: E402

shots = [
    (f"http://127.0.0.1:{PORT}/", "docs/dashboard.png"),
    (f"http://127.0.0.1:{PORT}/product/1", "docs/product.png"),
]
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1160, "height": 900}, device_scale_factor=2)
    for url, path in shots:
        page.goto(url, wait_until="networkidle")
        page.screenshot(path=path, full_page=True)
        print(f"wrote {path}")
    browser.close()

os.remove(DEMO_DB)
