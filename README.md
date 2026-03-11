# Autonomous Prediction Market Trading Engine

A fully autonomous, 6-layer trading system for [Polymarket](https://polymarket.com) powered by Claude AI.

## Architecture

```
Layer 1  Data Ingestion      — WebSocket price feed, RSS news, Twitter stream, event calendar
Layer 2  Market Making       — Automated arbitrage on binary market spreads (pure math)
Layer 3  AI Trading Agent    — Claude claude-opus-4-6 directional probability analysis
Layer 4  Sentiment Detection — Hype/FUD detection from social media and news
Layer 5  Risk Management     — Hard portfolio limits, daily loss caps, anomaly detection
Layer 6  Self-Improvement    — Weekly performance review + Claude-generated prompt updates
```

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Polymarket account with USDC on Polygon
- Anthropic API key
- (Optional) Twitter Developer account for stream access

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure credentials

```bash
cp .env.example .env
# Edit .env with your API keys
export $(cat .env | xargs)
```

### 4. Run the engine

```bash
# All 6 layers
python main.py

# Data collection only (no trading — safe to start with)
python main.py --layers 1,4

# Market making only (Layer 2) + data + risk
python main.py --layers 1,2,4,5

# Full system
python main.py --layers 1,2,3,4,5,6
```

## Layer Details

### Layer 1 — Data Ingestion (Always Running)

- **Polymarket WebSocket**: Streams real-time bid/ask prices for every active market
- **RSS Feeds**: Reuters, NYT, BBC, Politico, CoinDesk, CoinTelegraph (configurable)
- **Twitter/X Stream**: Filtered stream for prediction-market keywords
- **Reddit**: Polls r/PredictionMarkets, r/politics, r/CryptoCurrency, etc.
- **Structured Events**: FOMC meeting dates, political event calendar
- All data stored in SQLite (`data/prediction_markets.db`) with CSV export option

### Layer 2 — Market Making (Low Risk)

Monitors every binary market for moments when:

```
best_ask(YES) + best_ask(NO) < $1.00
```

Since one side always resolves to $1.00, the spread is guaranteed profit.
Places simultaneous limit orders on both sides and rebalances every 30 seconds.

**Configuration** (`config/settings.py`):
- `max_combined_cost = 0.98` — only trade if combined cost ≤ $0.98
- `position_size_usdc = 10.0` — $10 per pair
- `max_mm_positions = 200` — maximum simultaneous pairs

### Layer 3 — AI Directional Agent (Claude API)

Runs every 10 minutes:
1. Pulls all active markets from DB
2. Filters for minimum volume + time-to-resolution
3. For each candidate: fetches news context, price history, sentiment signals
4. Calls `claude-opus-4-6` with adaptive thinking to estimate true probability
5. If `|AI estimate − market price| > 10%`, flags as opportunity
6. Executes trade after risk checks

Uses streaming with `thinking: {type: "adaptive"}` for deep reasoning on complex political/sports/crypto questions.

### Layer 4 — Sentiment Detection

Monitors stored news and social data for hype spikes around specific markets.
When detected, signals Layer 3 to apply a contrarian discount:

```
adjusted_prob = base_prob + hype_discount × (0.5 − base_prob)
```

This automates the "nothing ever happens" strategy — betting against overexcited crowds.

### Layer 5 — Risk Guardian

**Hard limits (cannot be overridden):**
- Max 80% of bankroll deployed at any time
- Max 5% of bankroll on any single market
- Daily loss limit: 10% of opening balance → auto-halt + alert
- Max 3 correlated positions (same underlying: Trump, Fed, BTC, etc.)
- Auto profit-take at 50% return

Sends alerts via Slack/Discord webhook when limits are hit.

### Layer 6 — Self-Improvement Loop

Runs weekly:
1. Computes performance statistics: win rate, PnL, calibration by category
2. Identifies worst-performing categories and systematic biases
3. Calls Claude to generate an updated system prompt for Layer 3
4. Saves updated prompt to `data/updated_system_prompt.txt`
5. Layer 3 loads this on next startup

## Configuration

All settings in `config/settings.py`. Key parameters:

| Setting | Default | Description |
|---------|---------|-------------|
| `min_edge_threshold` | 0.10 | Minimum AI vs market gap to trade |
| `min_market_volume` | $10,000 | Ignore thin markets |
| `max_deployed_fraction` | 0.80 | Max % bankroll in active positions |
| `daily_loss_limit_fraction` | 0.10 | Auto-halt threshold |
| `ai_agent_interval_seconds` | 600 | Layer 3 cycle frequency |
| `profit_take_return` | 0.50 | Auto-close at 50% gain |

## Data Schema

SQLite tables in `data/prediction_markets.db`:

- `markets` — All Polymarket markets with metadata
- `price_ticks` — Real-time price history
- `news_items` — All ingested news/tweets/posts
- `trade_log` — Every trade placed (all layers)
- `sentiment_signals` — Hype scores per market
- `ai_predictions` — Every Claude estimate with reasoning
- `performance_snapshots` — Weekly review snapshots

## Starting Capital

| Mode | Recommended Capital |
|------|-------------------|
| Layer 2 only (MM) | $500+ |
| Layer 3 only (AI) | $500+ |
| Full system | $1,000–$2,000 |

## Disclaimer

This system places real money into prediction markets. Past performance does not guarantee future returns. Start with small capital, monitor closely, and adjust risk parameters based on your risk tolerance.
