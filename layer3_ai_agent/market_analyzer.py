"""
Layer 3 — AI market analysis using Claude API.

For each candidate market:
1. Gathers context: market description, resolution criteria, recent news,
   historical prices, and sentiment signals.
2. Asks Claude to estimate the true probability of the YES outcome.
3. Compares Claude's estimate to the market price.
4. Flags markets where |estimate - price| > threshold as trade opportunities.

Uses claude-opus-4-6 with adaptive thinking for maximum accuracy.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import anthropic

from config.settings import config
from layer1_data.database import Database

logger = logging.getLogger(__name__)


@dataclass
class MarketAnalysis:
    market_id: str
    question: str
    ai_probability: float       # Claude's estimate (0–1)
    market_price: float         # Current market mid-price (0–1)
    edge: float                 # ai_probability - market_price
    confidence: str             # "HIGH" / "MEDIUM" / "LOW"
    reasoning: str              # Claude's full reasoning
    recommended_side: str       # "YES" or "NO"
    recommended_size_usdc: float


SYSTEM_PROMPT = """You are an expert probability forecaster and prediction market analyst.
Your job is to estimate the true probability of events resolving YES on Polymarket.

You approach each market with:
- Rigorous base-rate reasoning
- Careful reading of resolution criteria
- Awareness of common biases (recency, availability, narrative)
- Calibration: you are not overconfident; your 70% estimates are right ~70% of the time

You output ONLY valid JSON — no markdown fences, no preamble. Schema:
{
  "probability": <float 0.0–1.0>,
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "reasoning": "<2–4 sentences explaining your estimate>",
  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"]
}

Definitions:
- HIGH confidence: you have strong evidence and the resolution criteria are clear
- MEDIUM confidence: some uncertainty in data or interpretation
- LOW confidence: highly speculative, thin information, ambiguous criteria
"""


def _build_analysis_prompt(
    market: dict,
    news_items: list[dict],
    recent_prices: list[dict],
    sentiment: Optional[dict],
) -> str:
    """Construct the full analysis prompt for a given market."""

    # Summarise recent news
    news_text = ""
    if news_items:
        headlines = [f"- [{n['source']}] {n['headline']}" for n in news_items[:15]]
        news_text = "RECENT RELEVANT NEWS:\n" + "\n".join(headlines)

    # Summarise price history
    price_text = ""
    if recent_prices:
        prices = [float(p["price"]) for p in recent_prices[:20]]
        price_text = (
            f"PRICE HISTORY (last {len(prices)} ticks): "
            f"min={min(prices):.3f} max={max(prices):.3f} "
            f"current={prices[0]:.3f}"
        )

    # Sentiment
    sentiment_text = ""
    if sentiment:
        sentiment_text = (
            f"SENTIMENT SIGNAL: hype_score={sentiment.get('hype_score', 0):.2f} "
            f"mentions={sentiment.get('mention_count', 0)} "
            f"platform={sentiment.get('platform', '')}"
        )

    end_date = market.get("end_date", "unknown")

    return f"""MARKET QUESTION:
{market.get('question', '')}

DESCRIPTION / RESOLUTION CRITERIA:
{market.get('description', 'No description available.')[:1000]}

RESOLUTION DATE: {end_date}
CURRENT MARKET PRICE (YES): {market.get('_current_price', 'unknown')}
MARKET VOLUME: ${float(market.get('volume', 0) or 0):,.0f}
CATEGORY: {market.get('category', 'unknown')}

{news_text}

{price_text}

{sentiment_text}

Based on all available information, estimate the probability that this market resolves YES.
Respond with JSON only."""


class MarketAnalyzer:
    """
    Uses Claude API to estimate probabilities for prediction market questions.
    """

    def __init__(self, db: Database):
        self.db = db
        self.client = anthropic.AsyncAnthropic(api_key=config.anthropic.api_key)
        self.model = config.anthropic.model

    async def _get_claude_estimate(self, prompt: str) -> Optional[dict]:
        """Call Claude API and parse the JSON probability estimate."""
        try:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=config.anthropic.max_tokens,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                response = await stream.get_final_message()

            # Extract text from response
            text_block = next(
                (b for b in response.content if b.type == "text"), None
            )
            if not text_block:
                return None

            raw = text_block.text.strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)

        except json.JSONDecodeError as exc:
            logger.warning("Claude returned invalid JSON: %s", exc)
            return None
        except anthropic.APIError as exc:
            logger.error("Anthropic API error: %s", exc)
            return None
        except Exception as exc:
            logger.error("Unexpected error in _get_claude_estimate: %s", exc)
            return None

    async def analyze_market(
        self, market: dict, current_price: float
    ) -> Optional[MarketAnalysis]:
        """
        Full analysis pipeline for a single market:
        1. Gather context
        2. Call Claude
        3. Return structured MarketAnalysis
        """
        market_id = market.get("id", "")

        # Gather context
        news = self.db.get_recent_news(hours=48, limit=50)
        # Filter news to items mentioning the market question keywords
        question_words = set(market.get("question", "").lower().split())
        relevant_news = [
            n for n in news
            if any(w in n.get("headline", "").lower() for w in question_words if len(w) > 4)
        ][:10]

        prices = self.db.get_recent_prices(market_id, limit=50)
        sentiment = self.db.get_latest_sentiment(market_id)

        market["_current_price"] = f"{current_price:.4f}"

        prompt = _build_analysis_prompt(market, relevant_news, prices, sentiment)

        result = await self._get_claude_estimate(prompt)
        if result is None:
            return None

        ai_prob = float(result.get("probability", 0.5))
        confidence = result.get("confidence", "MEDIUM")
        reasoning = result.get("reasoning", "")

        edge = ai_prob - current_price
        # Decide which side to trade
        recommended_side = "YES" if edge > 0 else "NO"
        # If trading NO, the effective edge on the NO token is -edge
        effective_edge = abs(edge)

        # Size proportional to edge and confidence
        base_size = config.market_making.position_size_usdc * 2  # $20 base
        confidence_mult = {"HIGH": 2.0, "MEDIUM": 1.0, "LOW": 0.5}.get(confidence, 1.0)
        size = round(base_size * confidence_mult * (effective_edge / 0.15), 2)
        size = max(5.0, min(size, config.risk.max_single_market_fraction * 1000))

        # Log prediction
        self.db.log_prediction(
            market_id=market_id,
            question=market.get("question", ""),
            ai_prob=ai_prob,
            market_price=current_price,
            reasoning=reasoning,
            model=self.model,
        )

        return MarketAnalysis(
            market_id=market_id,
            question=market.get("question", ""),
            ai_probability=ai_prob,
            market_price=current_price,
            edge=edge,
            confidence=confidence,
            reasoning=reasoning,
            recommended_side=recommended_side,
            recommended_size_usdc=size,
        )
