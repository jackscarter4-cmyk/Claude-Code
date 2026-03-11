#!/usr/bin/env python3
"""
Autonomous Prediction Market Trading Engine
==========================================

Starts all 6 layers concurrently:

  Layer 1  — Data ingestion (WebSocket, RSS, Twitter, events)
  Layer 2  — Market-making (arbitrage on binary markets)
  Layer 3  — AI directional trading agent (Claude API)
  Layer 4  — Sentiment and hype detection
  Layer 5  — Risk management and portfolio oversight
  Layer 6  — Self-improvement loop

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


async def main(layers: set[int]):
    _ensure_data_dirs()

    # -- Imports here so the CLI starts fast even with missing deps
    from config.settings import config
    from layer1_data.database import Database
    from layer5_risk.alert_system import AlertSystem
    from utils.polymarket_client import PolymarketClient

    db = Database()
    client = PolymarketClient()
    alert = AlertSystem()

    tasks = []

    # ----------------------------------------------------------------
    # Layer 5 — Risk guardian (starts first; other layers inject it)
    # ----------------------------------------------------------------
    risk_guardian = None
    if 5 in layers:
        from layer5_risk.risk_guardian import RiskGuardian
        risk_guardian = RiskGuardian(db, client, alert)
        tasks.append(
            asyncio.create_task(risk_guardian.run_forever(), name="layer5_risk")
        )
        logger.info("Layer 5 (Risk Guardian) started")

    # ----------------------------------------------------------------
    # Layer 1 — Data pipeline
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
    # Layer 2 — Market making
    # ----------------------------------------------------------------
    if 2 in layers:
        from layer2_market_making.market_maker import MarketMaker
        market_maker = MarketMaker(db, client, risk_guardian)
        tasks.append(
            asyncio.create_task(market_maker.run_forever(), name="layer2_mm")
        )
        logger.info("Layer 2 (Market Maker) started")

    # ----------------------------------------------------------------
    # Layer 3 — AI trading agent
    # ----------------------------------------------------------------
    if 3 in layers:
        from layer3_ai_agent.agent import AITradingAgent
        agent = AITradingAgent(db, client, risk_guardian)
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
        "Engine running with %d tasks. Layers: %s",
        len(tasks),
        sorted(layers),
    )

    # Graceful shutdown on SIGINT / SIGTERM
    loop = asyncio.get_running_loop()
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
        await client.close()
        db.close()
        logger.info("Engine stopped cleanly")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Prediction Market Trading Engine")
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
