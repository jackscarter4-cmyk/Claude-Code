"""
Central configuration for the Prediction Market Trading Engine.
All secrets are loaded from environment variables — never hardcode keys.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PolymarketConfig:
    api_key: str = field(default_factory=lambda: os.environ.get("POLYMARKET_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.environ.get("POLYMARKET_API_SECRET", ""))
    api_passphrase: str = field(default_factory=lambda: os.environ.get("POLYMARKET_API_PASSPHRASE", ""))
    wallet_private_key: str = field(default_factory=lambda: os.environ.get("POLYGON_WALLET_PRIVATE_KEY", ""))
    wallet_address: str = field(default_factory=lambda: os.environ.get("POLYGON_WALLET_ADDRESS", ""))
    # Polymarket endpoints
    rest_host: str = "https://clob.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    gamma_api: str = "https://gamma-api.polymarket.com"


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
    access_token_secret: str = field(default_factory=lambda: os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", ""))


@dataclass
class DatabaseConfig:
    path: str = field(default_factory=lambda: os.environ.get("DB_PATH", "data/prediction_markets.db"))
    csv_dir: str = field(default_factory=lambda: os.environ.get("CSV_DIR", "data/csv"))


@dataclass
class RiskConfig:
    # Maximum fraction of bankroll to deploy at any time
    max_deployed_fraction: float = 0.80
    # Maximum fraction of bankroll on any single market
    max_single_market_fraction: float = 0.05
    # Daily loss limit as fraction of starting daily bankroll
    daily_loss_limit_fraction: float = 0.10
    # Maximum number of simultaneous open positions
    max_open_positions: int = 50
    # Minimum edge required before placing a directional trade (Layer 3)
    min_edge_threshold: float = 0.10
    # Minimum volume (in USDC) for a market to be considered
    min_market_volume: float = 10_000.0
    # Minimum days until resolution to consider trading
    min_days_to_resolution: int = 1
    # Maximum number of correlated positions (same underlying)
    max_correlated_positions: int = 3
    # Profit-taking target (close position at this return)
    profit_take_return: float = 0.50


@dataclass
class MarketMakingConfig:
    # Maximum combined cost for an arbitrage pair to still be profitable
    max_combined_cost: float = 0.98   # Must be < $1.00
    # Position size per arb trade (in USDC)
    position_size_usdc: float = 10.0
    # Maximum number of simultaneous MM positions
    max_mm_positions: int = 200
    # How often to refresh limit orders (seconds)
    refresh_interval_seconds: int = 30


@dataclass
class SentimentConfig:
    # Spike threshold: mentions per hour that constitute "hype"
    hype_spike_threshold: int = 100
    # How much to discount probability estimate when hype detected
    hype_discount_factor: float = 0.15
    # Subreddits to monitor
    subreddits: list = field(default_factory=lambda: [
        "PredictionMarkets", "politics", "wallstreetbets",
        "CryptoCurrency", "sports", "worldnews"
    ])


@dataclass
class AlertConfig:
    # Email or webhook for critical alerts
    alert_webhook_url: str = field(
        default_factory=lambda: os.environ.get("ALERT_WEBHOOK_URL", "")
    )
    alert_email: str = field(
        default_factory=lambda: os.environ.get("ALERT_EMAIL", "")
    )


@dataclass
class AppConfig:
    polymarket: PolymarketConfig = field(default_factory=PolymarketConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    twitter: TwitterConfig = field(default_factory=TwitterConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    market_making: MarketMakingConfig = field(default_factory=MarketMakingConfig)
    sentiment: SentimentConfig = field(default_factory=SentimentConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)

    # Layer 3 AI agent loop interval (seconds)
    ai_agent_interval_seconds: int = 600   # 10 minutes
    # Layer 6 review interval (seconds)
    review_interval_seconds: int = 604_800  # 1 week

    # RSS news feeds
    rss_feeds: list = field(default_factory=lambda: [
        "https://feeds.reuters.com/reuters/topNews",
        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://www.politico.com/rss/politicopicks.xml",
        "https://feeds.npr.org/1001/rss.xml",
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ])


# Singleton config instance
config = AppConfig()
