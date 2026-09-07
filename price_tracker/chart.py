"""Optional price-history chart (stretch goal).

Kept in its own module so that matplotlib - by far the heaviest dependency - is
only imported when the user actually runs ``chart``. Everything else in the
tool works without it.
"""

from __future__ import annotations

import os
from datetime import datetime

from . import db


def build_chart(identifier: str, output_path: str | None = None) -> str | None:
    """Render a PNG line chart of a product's price history.

    Returns the path written, or None if there was nothing to plot.
    """
    product = db.get_product(identifier)
    if product is None:
        print(f"no product matching {identifier!r}")
        return None

    points = db.get_history(product.id)
    if len(points) < 2:
        print("need at least 2 price points to draw a chart - run 'check' a few times")
        return None

    # Import here (not at module top) so 'import price_tracker.chart' is cheap
    # and the dependency is truly optional.
    import matplotlib

    matplotlib.use("Agg")  # headless: no display needed, just write a file
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    times = [datetime.fromisoformat(p.checked_at) for p in points]
    prices = [p.price for p in points]

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(times, prices, marker="o", linewidth=1.6)

    if product.target_price is not None:
        ax.axhline(
            product.target_price,
            color="tab:red",
            linestyle="--",
            linewidth=1,
            label=f"target {product.target_price:.2f}",
        )
        ax.legend(loc="best")

    ax.set_title(f"Price history - {product.name or product.url}")
    ax.set_ylabel(f"price ({product.currency or 'currency'})")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d\n%H:%M"))
    fig.autofmt_xdate()
    fig.tight_layout()

    if output_path is None:
        os.makedirs("charts", exist_ok=True)
        output_path = os.path.join("charts", f"product_{product.id}.png")

    fig.savefig(output_path, dpi=120)
    plt.close(fig)
    print(f"chart written to {output_path}")
    return output_path
