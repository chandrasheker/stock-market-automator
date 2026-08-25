# crude-research

Market-data collection and Black-76 research for **MCX CRUDEOIL** (100 bbl) and **CRUDEOILM** (10 bbl) options, using **Zerodha Kite Connect** as the primary data source.

> This project currently performs market-data collection and quantitative research only. It does not place or manage trades.

## 1. Purpose

Build a trustworthy quantitative foundation:

1. Download and classify the MCX instrument master without mixing CRUDEOIL and CRUDEOILM.
2. Reconstruct option chains against the **mapped underlying futures** contract (options on futures, not spot).
3. Compute **Black-76** implied volatility and greeks from quality-tagged quotes.

Later milestones (bias engine, scoring, backtests, paper/live trading, GTT) are **out of scope** until explicitly approved.

## 2. Implemented scope (M1–M3 only)

| Milestone | What you get |
|-----------|----------------|
| **M1** | Kite credentials via env, daily MCX instrument cache (Parquet), contract discovery, option→future resolver, full-quote ingestion, KiteTicker FULL-mode interface, quote-quality flags |
| **M2** | Option-chain snapshot, ATM from the mapped future, ATM straddle with explicit price source, distance / distance-to-straddle research fields, append-only Parquet |
| **M3** | Black-76 pricing, exact timezone-aware year fraction, bounded Brent IV, delta/gamma/theta/vega with documented units |

## 3. Intentionally NOT implemented

There is no implementation of:

* trading strategy / directional bias / strike scoring
* backtesting
* paper trading or live trading
* `place_order` / modify / cancel
* GTT create / modify
* stop-loss, targets, trailing stops, position sizing
* Telegram / WhatsApp alerts
* dashboards / frontends

Do not treat chain tables or IV as trade recommendations.

## 4. Environment setup

Python **3.12+**. Windows and Linux. No Docker required.

The library lives under `src/`. **`python -m crude_research.cli` will fail with `ModuleNotFoundError` until the package is installed into the same interpreter you are using** (your `venv`, not a different Python).

```bash
python -m venv venv
# Windows Git Bash / Linux:
source venv/bin/activate
# Windows cmd: venv\Scripts\activate

python -m pip install -U pip
python -m pip install -e ".[dev]"
cp .env.example .env
# then edit .env: KITE_API_KEY, KITE_API_SECRET, RISK_FREE_RATE
# KITE_ACCESS_TOKEN is minted daily via: python -m crude_research.cli kite session
```

Confirm the install:

```bash
python -c "import crude_research; print(crude_research.__version__)"
python -m crude_research.cli doctor
```

If you cannot install yet, either set `PYTHONPATH=src` or use the repo launcher:

```bash
export PYTHONPATH=src          # Git Bash; PowerShell: $env:PYTHONPATH="src"
python -m crude_research.cli doctor
# or
python run.py doctor
```

## 5. Zerodha credential requirements

Set in `.env` (never commit real values):

```text
KITE_API_KEY=
KITE_API_SECRET=
KITE_ACCESS_TOKEN=
DATA_DIR=./data
TIMEZONE=Asia/Kolkata
RISK_FREE_RATE=
```

This project **does not** automate Kite login (no password, PIN, TOTP, or browser automation). `KITE_API_SECRET` is only used to exchange a one-time `request_token` for today's `access_token`. **`git pull` does not update `.env`.** If that file was created before `KITE_API_SECRET` existed, the line is missing until you add it.

Run these **one command at a time** (do not paste the whole block):

```bash
python -m crude_research.cli kite set-secret
python -m crude_research.cli kite login-url
# open the printed URL, log in in the browser, copy request_token from the redirect
python -m crude_research.cli kite session --request-token <value-from-redirect>
python -m crude_research.cli doctor
```

If credentials are missing:

* unit tests still run
* cached instrument files can still be read
* live `instruments sync` / `chain snapshot` will refuse to call the API

Optional knobs (defaults shown):

```text
QUOTE_BATCH_SIZE=500          # Kite /quote limit; do not raise above 500
STALE_QUOTE_SECONDS=120
IV_VOL_LOWER=0.000001
IV_VOL_UPPER=5.0              # 500% vol search cap
OPTION_EXPIRY_TIME=23:30:00   # explicit assumption; see §15
```

## 6. Instrument sync

After `python -m pip install -e ".[dev]"` (see §4):

```bash
python -m crude_research.cli doctor
python -m crude_research.cli instruments sync
python -m crude_research.cli instruments expiries CRUDEOILM
```

`YYYY-MM-DD` in examples is a placeholder. Use a real expiry from `instruments expiries`.

The MCX master is refreshed **once per calendar session date** in `TIMEZONE` and cached at:

```text
data/instruments/mcx_instruments_YYYY-MM-DD.parquet
```

A second copy is written under `data/instruments/history/` so re-syncs remain reconstructable. The raw MCX dump is preserved (including GOLD and anything unexpected). Classification of CRUDEOIL vs CRUDEOILM matches `CRUDEOILM` **before** `CRUDEOIL` so a mini contract is never swallowed by the full-size name.

Lot sizes, strike intervals, and tokens are taken from Zerodha metadata. They are not hard-coded.

## 7. Chain snapshot example

```bash
python -m crude_research.cli chain snapshot \
  --underlying CRUDEOILM \
  --expiry 2026-10-15 \
  --risk-free-rate 0.065
```

Prints a compact research table (future, ATM, ATM straddle, IV, delta, OI, quote-quality, distance, distance/straddle) and writes an append-only Parquet partition:

```text
data/chains/date=YYYY-MM-DD/underlying=CRUDEOILM/expiry=YYYY-MM-DD/snapshot_*.parquet
```

Existing snapshot files are never overwritten.

Watch (FULL-mode websocket; illiquid strikes may not tick):

```bash
python -m crude_research.cli chain watch --underlying CRUDEOILM --expiry 2026-10-15
```

## 8. Black-76 model

MCX crude options are **options on futures**. Prices are Black (1976), not equity Black–Scholes:

```text
d1 = [ln(F/K) + 0.5 σ² T] / (σ √T)
d2 = d1 − σ √T

C = e^{−rT} [ F N(d1) − K N(d2) ]
P = e^{−rT} [ K N(−d2) − F N(−d1) ]
```

`F` is the mapped futures research price (mid when valid; otherwise `LTP_FALLBACK`, never silently relabelled as mid).
`T` is an exact timezone-aware year fraction (see §15). `r` is the configured continuously compounded rate stored on every greeks row.

### Greek units

| Field | Meaning |
|-------|---------|
| `delta` | ∂V/∂F, discounted. Call: `e^{−rT} N(d1)`. Put: `−e^{−rT} N(−d1)`. |
| `gamma` | ∂²V/∂F² = `e^{−rT} n(d1) / (F σ √T)` |
| `theta` | calendar decay of premium **per year** |
| `theta_per_day` | `theta / 365` (display convenience, not the pricing day count) |
| `vega` | ∂V/∂σ for a **+1.00** move in volatility (0.20 → 1.20) |
| `vega_1pct` | `vega / 100` (one vol point, +0.01) |

Premiums are in the same units as the MCX quote (INR per barrel), not rupee P&L per lot.

IV is inverted with Brent’s method on `[IV_VOL_LOWER, IV_VOL_UPPER]`. Failures return a status and `iv=None`. **Zero is never used as a fake IV.**

Statuses: `OK`, `NO_PRICE`, `STALE_PRICE`, `BELOW_INTRINSIC_BOUND`, `ABOVE_MODEL_BOUND`, `NO_CONVERGENCE`, `INVALID_INPUT`.

`OK` requires a **valid bid/ask mid**, a non-stale quote, no-arbitrage bounds, and solver convergence. LTP-only prices may still be inverted for diagnostics but are never `OK`.

## 9. IV price-quality rules

* Mid = `(best_bid + best_ask) / 2` **only** when both sides are positive and uncrossed.
* Bid `0` / missing ask / crossed book → `NO_VALID_MID`. LTP is **not** substituted as mid.
* If a fallback last-trade price is used, `price_source = LTP_FALLBACK` and `iv_status` is not `OK`.
* Stale books (`exchange_timestamp` older than `STALE_QUOTE_SECONDS`, default 120s) are flagged `STALE` / `STALE_PRICE`.
* Missing ticks are treated as possible **illiquidity**, not as a feed bug.

ATM is the listed strike closest to the mapped futures price. **Ties pick the lower strike.** The strike interval is derived from the actual ladder; ₹50 is not assumed.

ATM straddle source:

* `MID` — both ATM legs have valid mids
* `MIXED` — one mid, one LTP
* `LTP_FALLBACK` — both LTP
* `UNAVAILABLE` — cannot form a straddle

## 10. Test commands

```bash
pytest
pytest -m network          # live Kite tests; skipped unless credentials exist
```

Network tests never place orders.

## 11. Lint / type-check commands

```bash
ruff check src tests
mypy
pytest
```

## 12. Troubleshooting: `No module named 'crude_research'`

The CLI is a package under `src/crude_research`. Copying `.env.example` is not enough; the active venv must have the project installed:

```bash
source venv/bin/activate          # you already do this
python -m pip install -e ".[dev]"
python -c "import crude_research; print('ok', crude_research.__version__)"
python -m crude_research.cli doctor
```

Use a real option expiry from `instruments expiries`, not the placeholder `YYYY-MM-DD`.

On this dump, CRUDEOILM option last-trading-days were `2026-09-17`, `2026-10-15`, `2026-11-17` — **not** `2026-10-16` (that is near the October *futures* expiry `2026-10-19`). MCX options expire two business days before the futures last trading day.

## 13. Troubleshooting: `No such command 'kite'`

That error means the Python process is running an **old** `crude_research.cli` (before the session helper). `pip install kite` is the wrong fix: it installs an unrelated PyPI project (`kite` 0.2.x from kitelang), not Zerodha and not this CLI. Zerodha’s library is **`kiteconnect`**, already a project dependency.

```bash
# Keep secrets in .env (gitignored). Discard local edits to the example file:
git restore .env.example
git pull origin cursor/mcx-crude-quant-foundation-636b

python -m pip uninstall -y kite    # only the unrelated kitelang package
python -m pip install -e ".[dev]"

python -c "import crude_research; print(crude_research.__version__, crude_research.__file__)"
python -m crude_research.cli --version
python -m crude_research.cli --help
```

You want **`crude-research 0.1.2`** (or newer) and `--help` listing `doctor`, `instruments`, `chain`, and **`kite`**. Then, **one command at a time**:

```bash
python -m crude_research.cli kite set-secret
python -m crude_research.cli kite login-url
# log in in the browser; copy the real request_token from the redirect URL
python -m crude_research.cli kite session --request-token <value-from-redirect>
python -m crude_research.cli doctor
```

Stay on `cursor/mcx-crude-quant-foundation-636b`. Do not switch to other feature branches for this CLI.

## 14. Troubleshooting: `TokenException`

`kite_credentials: present` only means `.env` has non-empty strings. Kite still rejects them when:

* the **access_token expired** (Zerodha issues a new one every trading day)
* `KITE_ACCESS_TOKEN` is actually the **api_secret** or the one-time **request_token**
* `KITE_API_KEY` does not belong to that access_token
* values were pasted with quotes or trailing spaces (the CLI now strips these)

`doctor` prints a fingerprint only (`ab…yz (len=N)`), never the secret, plus Kite's error text.

Fix:

1. Open https://developers.kite.trade/ → your app → copy **api_key** (16 chars) into `KITE_API_KEY`.
2. Copy **api_secret** (32 chars) into `KITE_API_SECRET` — never into `KITE_ACCESS_TOKEN`.
3. Create today's access_token (no password automation in this repo):

```bash
python -m crude_research.cli kite set-secret
python -m crude_research.cli kite login-url
# open the printed URL, log in in the browser, copy request_token from the redirect
python -m crude_research.cli kite session --request-token <value-from-redirect>
python -m crude_research.cli doctor
```

4. Snapshot with a listed expiry, e.g. `2026-10-15` if that is what `instruments expiries CRUDEOILM` printed.

Today's instrument cache can still be read when the token is dead (`instruments expiries` worked from Parquet). **Live quotes still need a valid daily token.**

## 15. Known assumptions and limitations

### Option-to-futures mapping

MCX contract specs: crude **options expire two business days before** the underlying futures last trading day. Expiry dates are therefore **not equal**, so we do not map by matching dates.

We map only when Zerodha tradingsymbols share a unique contract-month token (`26OCT` in `CRUDEOILM26OCT8000CE` and `CRUDEOILM26OCTFUT`) and the futures expiry is on or after the option expiry. If that cannot be proven, the resolver **errors** and logs every candidate future. We do not apply a holiday calendar or “nearest future” heuristic.

### Expiry timestamp / `T`

The instrument master supplies an expiry **date**, not the official last-tick clock time. MCX energy sessions end at **11:30 or 11:55 IST** depending on US daylight-saving time. Until official contract specs are wired, `T` uses `OPTION_EXPIRY_TIME` (default `23:30:00`) in `TIMEZONE`, stored as `expiry_time_source = CONFIGURED_ASSUMPTION`. `time_to_expiry(now, expiry_timestamp)` itself never invents a clock time; both arguments must be timezone-aware. Year fraction is actual seconds / `(365.25 × 86400)`, not integer DTE/365.

### Risk-free rate

There is no hard-coded RBI / T-bill rate. If `RISK_FREE_RATE` is unset, IV/greeks are skipped (`INVALID_INPUT`) unless you pass `--risk-free-rate`. The rate used is persisted on every snapshot row.

### Stale quotes

Default `STALE_QUOTE_SECONDS=120`. After hours, live snapshots will typically be `STALE` / `POOR`. That is intentional.

### Websocket timestamps

Kite REST quote stamps are naive IST strings and are attached to `TIMEZONE`. KiteTicker uses `datetime.fromtimestamp` in **process local time**; we convert those to `TIMEZONE`. A host whose OS timezone is not IST can skew websocket ages. REST snapshots are the reproducibility path.

### Other

* `/quote` is batched at most 500 instruments (Kite’s documented cap).
* KiteTicker reconnect is bounded (`WEBSOCKET_RECONNECT_MAX_TRIES`, default 10). This is a library, not a production daemon.
* Strike lists, lot sizes, and tick sizes come from the instrument master of that session date only.
* We have not verified every MCX circular against a live dump in this repository; mapping tests use synthetic tradingsymbols in the documented Zerodha pattern `CRUDEOILM{YY}{MON}{STRIKE}{CE|PE}`.
