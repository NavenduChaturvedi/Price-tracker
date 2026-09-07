"""Flask dashboard for the price tracker.

Routes map one-to-one onto CLI actions:

    GET  /                      list tracked products, add form, "check now"
    POST /add                   -> core.run_add
    POST /check                 -> core.run_check (all)
    POST /product/<id>/check    -> core.run_check (one)
    POST /product/<id>/remove   -> core.run_remove
    GET  /product/<id>          price history + chart
    GET  /product/<id>/chart.png
"""

from __future__ import annotations

import os

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for

from price_tracker import chart, core, db

CHART_DIR = os.path.join(os.path.dirname(__file__), "_charts")


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET", "price-tracker-dev")
    os.makedirs(CHART_DIR, exist_ok=True)
    db.init_db()

    @app.get("/")
    def index():
        products = db.list_products(include_inactive=True)
        rows = [_summarise(p) for p in products]
        return render_template("index.html", rows=rows)

    @app.post("/add")
    def add():
        url = (request.form.get("url") or "").strip()
        name = (request.form.get("name") or "").strip() or None
        target = _parse_float(request.form.get("target_price"))
        pct = _parse_float(request.form.get("pct_drop"))

        if not url:
            flash("A product URL is required.", "error")
            return redirect(url_for("index"))
        if target is None and pct is None:
            flash("Set a target price, a percentage drop, or both.", "error")
            return redirect(url_for("index"))

        try:
            core.run_add(url, target, name=name, pct_drop=pct)
            flash("Now tracking that product.", "ok")
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            flash(f"Could not add product: {exc}", "error")
        return redirect(url_for("index"))

    @app.post("/check")
    def check_all():
        _run_check_and_flash(only=None)
        return redirect(url_for("index"))

    @app.post("/product/<int:product_id>/check")
    def check_one(product_id: int):
        _run_check_and_flash(only=str(product_id))
        return redirect(request.referrer or url_for("index"))

    @app.post("/product/<int:product_id>/remove")
    def remove(product_id: int):
        core.run_remove(str(product_id))
        flash("Stopped tracking that product.", "ok")
        return redirect(url_for("index"))

    @app.get("/product/<int:product_id>")
    def product(product_id: int):
        p = db.get_product(str(product_id))
        if p is None:
            abort(404)
        history = db.get_history(p.id)
        baseline = db.first_recorded_price(p.id)
        points = _history_rows(history)
        return render_template(
            "product.html",
            product=p,
            points=points,
            baseline=baseline,
            has_chart=len(history) >= 2,
        )

    @app.get("/product/<int:product_id>/chart.png")
    def product_chart(product_id: int):
        p = db.get_product(str(product_id))
        if p is None:
            abort(404)
        out = os.path.join(CHART_DIR, f"product_{p.id}.png")
        result = chart.build_chart(str(p.id), output_path=out)
        if not result:
            abort(404)
        return send_file(out, mimetype="image/png")

    return app


# --------------------------------------------------------------------------
# view helpers - formatting only, no logic
# --------------------------------------------------------------------------
def _summarise(p: db.Product) -> dict:
    history = db.get_history(p.id, limit=2)
    latest = history[-1].price if history else None
    previous = history[0].price if len(history) == 2 else None

    status = "waiting"
    if latest is not None:
        if p.target_price is not None and latest <= p.target_price:
            status = "at-target"
        elif previous is not None and latest < previous:
            status = "dropped"
        else:
            status = "tracking"

    return {
        "id": p.id,
        "name": p.name or p.url,
        "url": p.url,
        "active": bool(p.active),
        "latest": latest,
        "previous": previous,
        "currency": p.currency or "",
        "target_price": p.target_price,
        "pct_drop": p.pct_drop,
        "last_checked": _fmt_ts(history[-1].checked_at) if history else None,
        "status": status,
    }


def _fmt_ts(iso: str | None) -> str:
    """'2026-09-07T16:34:40+00:00' -> '2026-09-07 16:34'."""
    if not iso:
        return ""
    return iso.replace("T", " ")[:16]


def _history_rows(history: list[db.PricePoint]) -> list[dict]:
    rows = []
    prev = None
    for point in history:
        change = None if prev is None else round(point.price - prev, 2)
        rows.append(
            {
                "checked_at": _fmt_ts(point.checked_at),
                "price": point.price,
                "currency": point.currency or "",
                "change": change,
                "in_stock": point.in_stock,
            }
        )
        prev = point.price
    return list(reversed(rows))


def _run_check_and_flash(only: str | None) -> None:
    try:
        outcomes = core.run_check(only=only)
    except Exception as exc:  # noqa: BLE001
        flash(f"Check failed: {exc}", "error")
        return

    if not outcomes:
        flash("Nothing to check yet.", "error")
        return

    ok = [o for o in outcomes if o.error is None]
    failed = [o for o in outcomes if o.error is not None]
    alerts = [o for o in outcomes if o.alerted]

    msg = f"Checked {len(outcomes)}: {len(ok)} ok, {len(failed)} failed"
    if alerts:
        msg += f", {len(alerts)} alert(s) sent"
    flash(msg, "ok" if not failed else "warn")
    for o in failed:
        flash(f"{o.product.name or o.product.url}: {o.error}", "warn")


def _parse_float(raw: str | None) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


app = create_app()
