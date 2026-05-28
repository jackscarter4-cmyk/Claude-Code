#!/usr/bin/env python3
"""
Stock Trading Engine
====================

Starts all 6 layers concurrently:

  Layer 1  — Data ingestion (Alpaca IEX WebSocket, RSS, Twitter, watchlist bootstrap)
  Layer 2  — Rule-based signal trading (momentum, volume spikes, rebalance)
  Layer 3  — AI directional trading agent (Claude API stock analysis)
  Layer 4  — Sentiment and hype detection (Reddit, Twitter)
  Layer 5  — Risk management and portfolio oversight
  Layer 6  — Self-improvement loop (performance review + prompt update)

Usage:
    python main.py [--layers 1,2,3,4,5,6]

Environment:
    Copy .env.example to .env and fill in your API keys.
    Source with: export $(cat .env | xargs)
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/engine.log", mode="a"),
    ],
)
# Quiet noisy third-party loggers
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)

logger = logging.getLogger("main")


def _ensure_data_dirs():
    """Create runtime data directories if they don't exist."""
    for d in ["data", "data/csv"]:
        Path(d).mkdir(parents=True, exist_ok=True)


def _check_market_hours():
    """Log a warning if starting outside market hours."""
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute
    # Market open 9:30am-4pm ET = 13:30-20:00 UTC (EDT)
    is_weekday = weekday < 5
    after_open = (hour > 13) or (hour == 13 and minute >= 30)
    before_close = hour < 20
    is_market_open = is_weekday and after_open and before_close

    if not is_market_open:
        day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday]
        logger.warning(
            "Starting outside market hours (%s %02d:%02d UTC). "
            "Layer 2/3 trading will be paused until 9:30am ET (13:30 UTC) on a weekday.",
            day_name, hour, minute,
        )
    else:
        logger.info("Market is currently OPEN — all layers active")


async def main(layers: set):
    _ensure_data_dirs()
    _check_market_hours()

    from config.settings import config
    from layer1_data.database import Database
    from layer5_risk.alert_system import AlertSystem
    from utils.broker_client import BrokerClient

    db = Database()
    broker = BrokerClient()
    alert = AlertSystem()

    tasks = []

    # ----------------------------------------------------------------
    # Layer 5 — Risk guardian (starts first; other layers inject it)
    # ----------------------------------------------------------------
    risk_guardian = None
    if 5 in layers:
        from layer5_risk.risk_guardian import RiskGuardian
        risk_guardian = RiskGuardian(db, broker, alert)
        tasks.append(
            asyncio.create_task(risk_guardian.run_forever(), name="layer5_risk")
        )
        logger.info("Layer 5 (Risk Guardian) started")

    # ----------------------------------------------------------------
    # Layer 1 — Data pipeline (watchlist bootstrap + price feed)
    # ----------------------------------------------------------------
    if 1 in layers:
        from layer1_data.pipeline import DataPipeline
        pipeline = DataPipeline(db)
        tasks.append(
            asyncio.create_task(pipeline.run_forever(), name="layer1_data")
        )
        logger.info("Layer 1 (Data Pipeline) started")

    # ----------------------------------------------------------------
    # Layer 4 — Sentiment monitor
    # ----------------------------------------------------------------
    if 4 in layers:
        from layer4_sentiment.sentiment_analyzer import SentimentMonitor
        from layer4_sentiment.reddit_monitor import RedditMonitor
        sentiment = SentimentMonitor(db)
        reddit = RedditMonitor(db)
        tasks.append(
            asyncio.create_task(sentiment.run_forever(), name="layer4_sentiment")
        )
        tasks.append(
            asyncio.create_task(reddit.run_forever(), name="layer4_reddit")
        )
        logger.info("Layer 4 (Sentiment) started")

    # ----------------------------------------------------------------
    # Layer 2 — Signal-based rule trader
    # ----------------------------------------------------------------
    if 2 in layers:
        from layer2_market_making.market_maker import SignalTrader
        signal_trader = SignalTrader(db, broker, risk_guardian)
        tasks.append(
            asyncio.create_task(signal_trader.run_forever(), name="layer2_signal")
        )
        logger.info("Layer 2 (Signal Trader) started")

    # ----------------------------------------------------------------
    # Layer 3 — AI trading agent
    # ----------------------------------------------------------------
    if 3 in layers:
        from layer3_ai_agent.agent import AITradingAgent
        agent = AITradingAgent(db, broker, risk_guardian)
        tasks.append(
            asyncio.create_task(agent.run_forever(), name="layer3_ai")
        )
        logger.info("Layer 3 (AI Agent) started")

    # ----------------------------------------------------------------
    # Layer 6 — Self-improvement optimizer
    # ----------------------------------------------------------------
    if 6 in layers:
        from layer6_improvement.system_optimizer import SystemOptimizer
        optimizer = SystemOptimizer(db)
        tasks.append(
            asyncio.create_task(optimizer.run_forever(), name="layer6_optimizer")
        )
        logger.info("Layer 6 (Optimizer) started")

    if not tasks:
        logger.error("No layers selected. Exiting.")
        return

    logger.info(
        "Stock trading engine running with %d tasks. Layers: %s",
        len(tasks),
        sorted(layers),
    )

    # Graceful shutdown on SIGINT / SIGTERM
    shutdown_event = asyncio.Event()

    def _shutdown(signum, frame):
        logger.info("Shutdown signal received — stopping engine")
        shutdown_event.set()
        for task in tasks:
            task.cancel()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await broker.close()
        db.close()
        logger.info("Engine stopped cleanly")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Stock Trading Engine")
    parser.add_argument(
        "--layers",
        default="1,2,3,4,5,6",
        help="Comma-separated list of layers to run (default: all)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        active_layers = {int(x.strip()) for x in args.layers.split(",") if x.strip()}
    except ValueError:
        print("Invalid --layers argument. Use comma-separated integers, e.g. --layers 1,3,5")
        sys.exit(1)

    asyncio.run(main(active_layers))
