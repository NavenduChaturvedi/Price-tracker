# Design decisions

This file explains **why** the tool is built the way it is. It is the primary
study material — the code shows *what*, this shows *why*.

---

## 1. Target sites: `books.toscrape.com` and `scrapeme.live`

**Decision:** ship configured for two sandbox stores that exist specifically for
scraping practice, not Amazon India / Flipkart.

**Why:**

- **A portfolio piece has to run for the person reviewing it.** Amazon and
  Flipkart actively fight scrapers (rotating layouts, JS challenges, IP bans,
  CAPTCHAs). A reviewer who clones the repo and hits a bot-wall learns nothing
  good about my code.
- Both demo sites have **stable, static HTML** and **no login**, so `check`
  works the same today and in six months.
- `scrapeme.live` is a real **WooCommerce** storefront — the same markup
  (`p.price .amount`, `h1.product_title`) that a large share of small shops use.
  So the WooCommerce parser is genuinely reusable, not a toy.
- The brief itself says "don't burn a day fighting Amazon's bot detection" and
  "pick a smaller e-commerce site instead". This is that choice, made
  deliberately and documented.

**Consequence / how to extend:** supporting a new store is one function in
`price_tracker/parsers.py` plus one line in the `_PARSERS` dict. Nothing else
changes. The generic fallback parser (Open Graph / `itemprop` price meta tags,
then common CSS classes) already handles many simple sites with no code at all.

---

## 2. `requests` + `BeautifulSoup`, not Playwright / Selenium

**Decision:** static scraping only.

**Why:**

- The target pages render their price in the initial HTML — verified by fetching
  the raw response. No JavaScript execution needed.
- A headless browser adds ~300 MB of dependencies, needs a browser binary
  installed, is an order of magnitude slower, and is far more fragile in CI.
- "Try static first, reach for a browser only if the price is JS-rendered" is
  the right instinct to demonstrate. Reaching for Selenium by reflex is a common
  junior mistake.

**Consequence:** if a future target is a React SPA, the fix is isolated to
`scraper.fetch` (swap the transport) — parsers and everything downstream stay
the same because they already take `(url, html)`.

`lxml` is used as BeautifulSoup's backend: it is faster and more lenient with
broken markup than the stdlib `html.parser`.

---

## 3. SQLite for price history

**Decision:** one local `price_history.db` file, two tables.

**Why:**

- The brief wants "a database" for portfolio credibility, but a Postgres/MySQL
  server would be pure friction for a single-user CLI.
- SQLite ships with Python, needs zero setup, and the whole history is one file
  you can hand over or open in any SQLite browser.
- It is still a *real* relational store: foreign keys (`ON DELETE CASCADE`), an
  index on `(product_id, checked_at)` for the history queries, `AUTOINCREMENT`
  ids.

**Schema shape:**

- `products` — the "what to watch" list: URL (unique), thresholds, and
  `last_alert_price` / `last_alert_at` for alert de-duplication.
- `price_history` — append-only time series, one row per successful check. This
  is what makes the price-over-time chart possible.

**Connection style:** every DB function opens a short-lived connection via a
context manager and commits/closes on exit. Slightly less efficient than a
shared connection, but it keeps the module free of global mutable state and
safe to call from anywhere, including tests.

---

## 4. Alerts: Telegram *or* email, chosen automatically

**Decision:** implement both; `ALERT_METHOD=auto` picks Telegram if it is
configured, else email, else prints to the console.

**Why:**

- **Telegram is the fastest to demo:** one HTTPS POST to the Bot API, no SMTP
  handshake, no app-passwords, delivery in under a second. Recommended in the
  README.
- **Email (`smtplib`) is the fallback** everyone can use without creating a bot,
  and it shows I can do MIME + STARTTLS + auth.
- **`auto`** means the tool does something sensible with zero config (prints the
  alert) and upgrades itself the moment credentials appear. A reviewer sees the
  alert body immediately without setting anything up.

**Key rule: `alerts.send()` never raises.** A notification that fails to
deliver must not abort a `check` run that already scraped and stored prices
successfully. Failures are printed and reported through the return value.

---

## 5. Alert trigger logic + de-duplication

**Decision:** fire when `price <= target_price` **or** price is `pct_drop`%
below the *first recorded* price — but stay silent if we already alerted at that
price or lower.

**Why:**

- Two trigger modes cover the two things people actually want: "tell me when
  it's cheap enough" (absolute) and "tell me when there's a real sale"
  (relative).
- The percentage baseline is the **first** recorded price, not the previous
  one, so a slow multi-day slide still counts as one big drop rather than a
  series of ignorable 1% steps.
- **De-duplication via `last_alert_price`:** without it, a product sitting below
  target would alert on *every single run*. Storing the last-alerted price means
  the user is notified once, and then again only if the price drops *further*
  (which re-arms the alert). Price going back up does not spam them either.

---

## 6. `robots.txt`, delays, and back-off

**Decision:** check `robots.txt` before every host (cached per run), sleep
`REQUEST_DELAY + random jitter` between products, and retry `429`/`5xx` with
exponential back-off that honours a `Retry-After` header.

**Why:**

- **`robots.txt`:** `urllib.robotparser` from the stdlib. If the file cannot be
  read we *fail open* (allow) — that matches how mainstream crawlers behave and
  how the standard defines a missing file ("no rules"). A disallow raises
  `RobotsDisallowed`, the product is skipped, the run continues.
- **Delay + jitter:** a fixed delay is polite; the jitter avoids a perfectly
  periodic request pattern, which is both more considerate and less bot-like.
  The pause lives in `core.run_check` (the caller) because only it knows how
  many URLs are in the batch — `fetch` shouldn't sleep on a single call.
- **Back-off:** `429` and `5xx` are transient, so retrying with `1s, 2s, 4s…`
  (or the server's `Retry-After`, whichever is longer) is worth it. `404`/`403`
  are permanent, so we fail fast instead of hammering.

---

## 7. Error-handling philosophy

**Decision:** typed exceptions at the boundaries (`FetchError`,
`RobotsDisallowed`, `ParseError`), caught per-product in `core._check_one`,
recorded in a `CheckOutcome`, and summarised at the end.

**Why:**

- A price tracker runs unattended. "One product's HTML changed" must never mean
  "I stopped getting alerts for the other nine".
- Each low-level module does exactly one job and signals failure with a specific
  exception type, so the orchestrator can tell *skipped* (robots) from *broken*
  (parse) from *down* (fetch) and report accordingly.
- There is also a catch-all `except Exception` in `_check_one` as a last line of
  defence — genuinely unexpected bugs degrade to "this product failed", not a
  crash.

---

## 8. Structure: thin CLI + a package of small modules

**Decision:** `tracker.py` only parses arguments and dispatches. All logic is in
`price_tracker/`, split by responsibility (`config`, `db`, `scraper`,
`parsers`, `alerts`, `core`, `chart`).

**Why:**

- Each file is small enough to hold in your head and test in isolation.
- The dependency direction is one-way: `core` imports the others; the others
  don't import `core`. No circular imports, easy to follow.
- `chart.py` imports `matplotlib` *inside* the function, not at module top, so
  the heaviest dependency is only loaded when you actually ask for a chart.
- Config is read once into a frozen dataclass (`config.settings`) rather than
  calling `os.getenv` scattered through the code.

---

## 9. Manual `check`, no built-in scheduler

**Decision:** `check` is a one-shot command; scheduling is left to cron / Task
Scheduler and documented in the README.

**Why:** the brief explicitly scopes the scheduler as "a next step not
required". A long-running daemon adds process-management concerns (restarts,
logging, supervision) that a portfolio CLI doesn't need, and cron already solves
this well.

---

## 10. The demo-chart script uses synthetic data — on purpose

`scripts/generate_demo_chart.py` builds a throwaway DB with a made-up two-week
price slide, because the demo sites' prices don't move. It runs the **real**
`chart.build_chart` code path, and the README labels the image as synthetic.
Faking the data in a hidden way would be dishonest; showing the feature with
clearly-labelled sample data is normal.
