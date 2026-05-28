# Offline Stock Quant Engine

A fully offline stock screening and recommendation engine. **No API keys, no
broker account, no internet** — it runs entirely on your machine using the
Python standard library. You feed it stock data; it scores each name on a
5-factor quant framework and produces risk-sized buy/sell/hold recommendations.

## What it does

```
Layer 1  Local Storage     — SQLite; persists each run's analyses + a portfolio snapshot
Layer 2  Signal Scan       — Momentum-breakout / oversold / volume-spike / rebalance signals
Layer 3  Quant Scoring     — 5-factor model (Value, Growth, Profitability, EPS Revisions, Momentum)
Layer 5  Risk Sizing       — Position size, stop-loss, single-stock / sector / deployment caps
Layer 6  Performance Review — Trends across stored runs
```

There is no live trading, no Claude API call, and no social-media scraping —
those parts of the original design required keys and have been removed.

## Two ways to use it

### `daily_check.py` — quick one-off screen

```bash
# 1. Generate a template CSV and fill it in (from Yahoo Finance, etc.)
python daily_check.py --template > stocks.csv

# 2. Score it
python daily_check.py --input stocks.csv

# Or enter stocks interactively
python daily_check.py --wizard
```

### `main.py` — the full engine (adds signals, risk sizing, persistence)

```bash
python main.py --template > stocks.csv
python main.py --input stocks.csv
```

## Importing from Webull

Webull gives you positions (symbol, price, cost basis, shares) but **not**
fundamentals, so it's a two-step flow:

```bash
# From an order-history CSV (Account -> History -> Export):
python main.py --webull-csv webull_orders.csv --export stocks.csv

# ...or from a positions JSON dump:
python main.py --webull-json positions.json --export stocks.csv

# Then open stocks.csv, add fundamentals from Yahoo Finance, and score:
python main.py --input stocks.csv
```

## The 5-factor model

Each factor is scored 0–10 via z-score normalization across the stocks you
provide, then averaged into a composite that drives the verdict.

| Factor | Inputs | Higher score when |
|--------|--------|-------------------|
| Value | P/E, forward P/E, P/B, EV/EBITDA | cheaper vs. peers |
| Growth | revenue YoY%, EPS YoY%, 3-yr EPS CAGR | faster growth |
| Profitability | ROE, ROIC, gross/net margin | more profitable vs. peers |
| EPS Revisions | 30/60/90-day estimate changes (30d weighted most) | analysts raising estimates |
| Momentum | 12-1 month return (excl. last month) + RSI | trending up, not overbought |

**Verdict:** composite ≥ 6.5 → BUY, ≤ 3.5 → SELL/AVOID, else HOLD. A red-flag
cap blocks BUY if Growth or Momentum ≤ 3.0 (mirrors Seeking Alpha's rule).

## Input columns

Required: `symbol`, `price`. Everything else is optional — provide what you
have and the model scores on available factors. Run `--template` to see the
full column list with examples. Optional fields for Layer 2 signals
(`high_52w`, `avg_volume`, `current_volume`, `prev_close`, `low_20d`,
`target_weight`, `earnings_date`) enable the signal scan when present.

## Configuration

Risk and signal thresholds live in `config/settings.py` (`RiskConfig`,
`SignalConfig`). The only environment variables are storage paths
(`DB_PATH`, `CSV_DIR`), both with defaults — you usually don't need a `.env`.

## Requirements

Python 3.11+. No third-party packages — standard library only.

## Disclaimer

This is an analysis tool, not financial advice. Scores are relative to the
universe of stocks you input; add more names for a more meaningful ranking.
