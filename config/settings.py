"""
Central configuration for the offline Stock Quant Engine.

This build runs fully offline — no API keys, no broker, no network.
All inputs come from local CSV/JSON files; all state lives in a local
SQLite database. There are intentionally no secret/API-key fields here.
"""

import os
from dataclasses import dataclass, field


@dataclass
class DatabaseConfig:
    path: str = field(default_factory=lambda: os.environ.get("DB_PATH", "data/trading.db"))
    csv_dir: str = field(default_factory=lambda: os.environ.get("CSV_DIR", "data/csv"))


@dataclass
class RiskConfig:
    # Maximum fraction of portfolio value to deploy at any time
    max_deployed_fraction: float = 0.80
    # Maximum fraction of portfolio value for any single stock position
    max_single_stock_fraction: float = 0.08
    # Daily loss limit as fraction of starting daily portfolio value
    daily_loss_limit_fraction: float = 0.10
    # Maximum number of simultaneous open positions
    max_open_positions: int = 30
    # Stop-loss from entry price (recommended on buys)
    stop_loss_pct: float = 0.08
    # Maximum sector concentration (fraction of total portfolio)
    max_sector_concentration: float = 0.40
    # Profit-taking target — flag at this return
    profit_take_return: float = 0.25


@dataclass
class SignalConfig:
    # Number of calendar days used for momentum lookback
    momentum_lookback_days: int = 20
    # Volume spike: multiples of average volume to trigger signal
    volume_spike_threshold: float = 2.0
    # Rebalance: drift from target weight that triggers a rebalance signal
    rebalance_drift_threshold: float = 0.05
    # Oversold: price pct below 52W high to be considered oversold
    oversold_threshold: float = -0.15
    # Overbought: price pct above cost basis to be considered overbought
    overbought_threshold: float = 0.50
    # Minimum signal strength (0–1) worth surfacing
    min_signal_strength: float = 0.60


@dataclass
class AppConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)

    # Default stock watchlist (used when no input file is given)
    watchlist: list = field(default_factory=lambda: [
        "AMZN", "JNJ", "META", "NVDA", "PG", "RIVN", "SPY", "VBIL",
        "QQQ", "VTI", "MSFT", "AAPL", "GOOGL", "BRK.B", "V", "JPM",
    ])


# Singleton config instance
config = AppConfig()
