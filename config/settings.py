"""
Central configuration for the Stock Trading Engine.
All secrets are loaded from environment variables — never hardcode keys.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AlpacaConfig:
    api_key: str = field(default_factory=lambda: os.environ.get("ALPACA_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.environ.get("ALPACA_API_SECRET", ""))
    base_url: str = field(
        default_factory=lambda: os.environ.get(
            "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
        )
    )
    # Alpaca market data WebSocket (IEX feed)
    ws_url: str = "wss://stream.data.alpaca.markets/v2/iex"


@dataclass
class YahooFinanceConfig:
    # Base URL for Yahoo Finance chart API (no auth required)
    chart_url: str = "https://query1.finance.yahoo.com/v8/finance/chart"
    # Polling interval (seconds) when WebSocket is unavailable
    poll_interval_seconds: int = 30


@dataclass
class AnthropicConfig:
    api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    model: str = "claude-opus-4-6"
    max_tokens: int = 8192


@dataclass
class TwitterConfig:
    bearer_token: str = field(default_factory=lambda: os.environ.get("TWITTER_BEARER_TOKEN", ""))
    api_key: str = field(default_factory=lambda: os.environ.get("TWITTER_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.environ.get("TWITTER_API_SECRET", ""))
    access_token: str = field(default_factory=lambda: os.environ.get("TWITTER_ACCESS_TOKEN", ""))
    access_token_secret: str = field(
        default_factory=lambda: os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")
    )


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
    # Stop-loss from entry price (Layer 3 AI trades)
    stop_loss_pct: float = 0.08
    # Maximum sector concentration (fraction of total portfolio)
    max_sector_concentration: float = 0.40
    # Profit-taking target — close/alert at this return
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
    # Minimum signal strength (0–1) required to attempt a trade
    min_signal_strength: float = 0.60
    # Scan interval during market hours (seconds)
    scan_interval_seconds: int = 300  # 5 minutes


@dataclass
class SentimentConfig:
    # Spike threshold: mentions per hour that constitute "hype"
    hype_spike_threshold: int = 100
    # How much to discount probability estimate when hype detected
    hype_discount_factor: float = 0.15
    # Subreddits to monitor
    subreddits: list = field(default_factory=lambda: [
        "stocks", "investing", "wallstreetbets",
        "StockMarket", "options", "securityanalysis",
        "personalfinance", "dividends",
    ])


@dataclass
class AlertConfig:
    alert_webhook_url: str = field(
        default_factory=lambda: os.environ.get("ALERT_WEBHOOK_URL", "")
    )
    alert_email: str = field(
        default_factory=lambda: os.environ.get("ALERT_EMAIL", "")
    )


@dataclass
class AppConfig:
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    yahoo: YahooFinanceConfig = field(default_factory=YahooFinanceConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    twitter: TwitterConfig = field(default_factory=TwitterConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    sentiment: SentimentConfig = field(default_factory=SentimentConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)

    # Layer 3 AI agent loop interval (seconds) — 15 minutes
    ai_agent_interval_seconds: int = 900
    # Layer 6 review interval (seconds) — 1 week
    review_interval_seconds: int = 604_800

    # Default stock watchlist
    watchlist: list = field(default_factory=lambda: [
        "AMZN", "JNJ", "META", "NVDA", "PG", "RIVN", "SPY", "VBIL",
        "QQQ", "VTI", "MSFT", "AAPL", "GOOGL", "BRK.B", "V", "JPM",
    ])

    # RSS news feeds focused on financial and market news
    rss_feeds: list = field(default_factory=lambda: [
        "https://feeds.reuters.com/reuters/topNews",
        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "https://feeds.npr.org/1001/rss.xml",
        "https://www.marketwatch.com/rss/topstories",
        "https://finance.yahoo.com/news/rssindex",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    ])


# Singleton config instance
config = AppConfig()
