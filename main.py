#!/usr/bin/env python3
"""
Offline Stock Quant Engine
==========================
Runs entirely on localhost. No API keys, no broker, no internet.

This is the batch "engine" that ties the layers together over a local
file of stock data you provide:

  Layer 1  — Local SQLite store (persists each run's analyses + snapshot)
  Layer 2  — Rule-based signal scan (momentum / oversold / volume / rebalance)
  Layer 3  — 5-factor quant scoring (Value, Growth, Profitability,
             EPS Revisions, Momentum) — replaces the old Claude agent
  Layer 5  — Risk-sized recommendations (position size, stop-loss,
             single-stock + sector + deployment caps)
  Layer 6  — Performance review from stored history

For a quick one-off screen without persistence, use daily_check.py instead.

USAGE
-----
  # Generate a template CSV (includes optional signal columns):
  python main.py --template > stocks.csv

  # Run the full engine on your data:
  python main.py --input stocks.csv

  # Import holdings from Webull, then enrich + score:
  python main.py --webull-csv webull_orders.csv --export stocks.csv
  python main.py --input stocks.csv

  # Save the report and skip the database:
  python main.py --input stocks.csv --output report.txt --no-db
"""

import argparse
import sys
from datetime import date

from config.settings import config
from layer1_data.database import Database
from layer2_market_making.signal_scanner import SignalScanner
from layer3_ai_agent.quant_scorer import QuantScorer
from layer5_risk.risk_calculator import RiskCalculator
from layer6_improvement.performance_tracker import PerformanceTracker

# Reuse the loaders / report renderer from the CLI screener
from daily_check import (
    load_csv,
    load_json,
    load_webull_positions_json,
    load_webull_orders_csv,
    write_screener_csv,
    render_report,
    TEMPLATE_CSV,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VERDICT_TO_OUTLOOK = {
    "BUY": "BULLISH",
    "SELL": "BEARISH",
    "AVOID": "BEARISH",
    "HOLD": "NEUTRAL",
}


def _portfolio_from_stocks(stocks) -> tuple[float, dict, dict]:
    """Build (portfolio_value, position_data, sector_by_symbol) from holdings."""
    position_data: dict = {}
    sector_by_symbol: dict = {}
    portfolio_value = 0.0
    for s in stocks:
        if s.sector:
            sector_by_symbol[s.symbol] = s.sector
        if s.shares_held and s.price:
            mv = s.shares_held * s.price
            portfolio_value += mv
            position_data[s.symbol] = {
                "market_value": round(mv, 2),
                "qty": s.shares_held,
                "avg_entry_price": s.cost_basis or s.price,
                "current_price": s.price,
            }
    if portfolio_value <= 0:
        portfolio_value = 100_000.0  # fallback when no positions provided
    return portfolio_value, position_data, sector_by_symbol


def _market_data_from_stocks(stocks) -> dict:
    """Build the SignalScanner market_data dict from optional StockData fields."""
    md = {}
    for s in stocks:
        md[s.symbol] = {
            "high_52w":       s.high_52w,
            "low_52w":        s.low_52w,
            "low_20d":        s.low_20d,
            "avg_volume":     s.avg_volume,
            "current_volume": s.current_volume,
            "prev_close":     s.prev_close,
            "price_2d_ago":   s.price_2d_ago,
            "earnings_date":  s.earnings_date,
            "target_weight":  s.target_weight,
            # Enhanced signal inputs (Bollinger, VWAP, OBV)
            "sma_20":          s.sma_20,
            "bollinger_upper": s.bollinger_upper,
            "bollinger_lower": s.bollinger_lower,
            "obv_trend":       s.obv_trend,
            "vwap":            s.vwap,
        }
    return md


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def render_signals(signals, output=sys.stdout) -> None:
    print("\n" + "=" * 80, file=output)
    print("  LAYER 2 — TRADING SIGNALS", file=output)
    print("=" * 80, file=output)
    if not signals:
        print("  No signals fired. (Add 52-week high / volume columns to enable.)", file=output)
        return
    for sig in signals:
        print(
            f"\n  [{sig.signal_type}] {sig.symbol} {sig.direction} "
            f"(strength {sig.strength:.2f}) ~${sig.suggested_size_usd:,.0f}",
            file=output,
        )
        print(f"    {sig.reasoning}", file=output)


def render_risk(decisions, output=sys.stdout) -> None:
    print("\n" + "=" * 80, file=output)
    print("  LAYER 5 — RISK-SIZED RECOMMENDATIONS", file=output)
    print("=" * 80, file=output)
    actionable = [d for d in decisions if d.approved]
    if not actionable:
        print("  No actions pass risk checks right now.", file=output)
    for d in actionable:
        line = f"\n  {d.side:<4} {d.symbol:<8}"
        if d.side == "BUY":
            line += f" ${d.size_usd:,.0f} ({d.shares:g} sh) stop=${d.stop_loss_price:.2f}"
        else:
            line += f" {d.shares:g} sh (${d.size_usd:,.0f})"
        print(line, file=output)
        for r in d.reasons:
            print(f"      - {r}", file=output)
        if d.kelly_fraction is not None:
            print(f"      - ½-Kelly fraction: {d.kelly_fraction*100:.2f}% of portfolio", file=output)
        if d.var_95_usd is not None:
            print(f"      - 1-day VaR (95%):  ${d.var_95_usd:,.2f}", file=output)
        if d.cvar_95_usd is not None:
            print(f"      - 1-day CVaR (95%): ${d.cvar_95_usd:,.2f}  (Expected Shortfall)", file=output)

    blocked = [d for d in decisions if not d.approved and d.side != "HOLD"]
    if blocked:
        print("\n  Blocked by risk limits:", file=output)
        for d in blocked:
            print(f"    {d.symbol}: {'; '.join(d.reasons)}", file=output)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def run_engine(stocks, persist: bool, review_days: int, output=sys.stdout) -> None:
    portfolio_value, position_data, sector_by_symbol = _portfolio_from_stocks(stocks)
    symbols = [s.symbol for s in stocks]
    prices = {s.symbol: s.price for s in stocks}

    # Layer 3 — quant scoring
    scorer = QuantScorer()
    results = scorer.score(stocks)

    # Layer 2 — signal scan
    scanner = SignalScanner()
    market_data = _market_data_from_stocks(stocks)
    signals = scanner.scan(symbols, prices, position_data, market_data)

    # Layer 5 — risk-sized recommendations
    risk = RiskCalculator()
    decisions = [
        risk.evaluate(
            r, portfolio_value,
            {k: v["market_value"] for k, v in position_data.items()},
            sector_by_symbol,
        )
        for r in results
    ]

    # ---- Report ----
    print("#" * 80, file=output)
    print(f"#  OFFLINE STOCK QUANT ENGINE — {date.today().isoformat()}", file=output)
    print(f"#  Portfolio value: ${portfolio_value:,.0f}  |  {len(stocks)} symbols", file=output)
    print("#" * 80, file=output)

    render_report(results, output=output)        # Layer 3 detail
    render_signals(signals, output=output)        # Layer 2
    render_risk(decisions, output=output)         # Layer 5

    # Layer 1 — persist
    if persist:
        db = Database()
        try:
            for r in results:
                db.log_stock_analysis({
                    "symbol": r.symbol,
                    "ai_outlook": _VERDICT_TO_OUTLOOK.get(r.verdict, "NEUTRAL"),
                    "confidence": r.confidence,
                    "price_at_analysis": r.price,
                    "reasoning": f"composite={r.composite} "
                                 f"(V{r.score_value} G{r.score_growth} "
                                 f"P{r.score_profitability} E{r.score_eps_revisions} "
                                 f"M{r.score_momentum})",
                    "model": "offline_quant_v1",
                })
            positions_list = [{"symbol": k, **v} for k, v in position_data.items()]
            db.log_portfolio_snapshot(
                total_value=portfolio_value, cash=0.0, positions=positions_list,
            )

            # Layer 6 — performance review from accumulated history
            tracker = PerformanceTracker(db)
            stats = tracker.compute_stats(review_days)
            if "status" not in stats:
                print("\n" + "=" * 80, file=output)
                print("  LAYER 6 — PERFORMANCE REVIEW", file=output)
                print("=" * 80, file=output)
                print(tracker.generate_report(review_days), file=output)
        finally:
            db.close()
        print(f"\n[saved analyses + snapshot to {config.database.path}]", file=output)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Offline stock quant engine — no API keys required.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", "-i", help="CSV or JSON file of stock data")
    parser.add_argument("--webull-json", help="Import holdings from a Webull positions JSON dump")
    parser.add_argument("--webull-csv", help="Import holdings from a Webull order-history CSV")
    parser.add_argument("--export", help="Write an enriched screener CSV (then add fundamentals) and exit")
    parser.add_argument("--output", "-o", help="Save report to this file (default: stdout)")
    parser.add_argument("--no-db", action="store_true", help="Do not persist to the SQLite database")
    parser.add_argument("--days", type=int, default=30, help="Performance review window (default 30)")
    parser.add_argument("--template", "-t", action="store_true", help="Print a template CSV and exit")
    args = parser.parse_args()

    if args.template:
        print(TEMPLATE_CSV.rstrip())
        return

    if args.webull_json:
        stocks = load_webull_positions_json(args.webull_json)
    elif args.webull_csv:
        stocks = load_webull_orders_csv(args.webull_csv)
    elif args.input:
        stocks = load_json(args.input) if args.input.endswith(".json") else load_csv(args.input)
    else:
        parser.print_help()
        print("\nError: provide --input, --webull-json, --webull-csv, or --template")
        sys.exit(1)

    if not stocks:
        print("No stocks loaded. Check your input file.")
        sys.exit(1)

    if args.export:
        write_screener_csv(stocks, args.export)
        print(f"Wrote {len(stocks)} holdings to {args.export}")
        print("Fill in the fundamentals columns, then run: python main.py --input " + args.export)
        return

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            run_engine(stocks, persist=not args.no_db, review_days=args.days, output=f)
        print(f"Report saved to {args.output}")
    else:
        run_engine(stocks, persist=not args.no_db, review_days=args.days)


if __name__ == "__main__":
    main()
