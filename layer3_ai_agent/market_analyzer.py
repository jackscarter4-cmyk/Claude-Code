"""
Layer 3 — AI stock analysis using Claude API.

For each candidate symbol:
1. Gathers context: current price, 52W range, volume, sector, recent news,
   cost basis (if held), beta, dividend info, EPS estimates, earnings date.
2. Asks Claude to provide a structured stock outlook.
3. Returns a StockAnalysis dataclass consumed by the trade executor.

Uses claude-opus-4-6 with adaptive thinking for maximum accuracy.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import anthropic

from config.settings import config
from layer1_data.database import Database

logger = logging.getLogger(__name__)


@dataclass
class StockAnalysis:
    symbol: str
    company_name: str
    outlook: str             # "BULLISH" | "BEARISH" | "NEUTRAL"
    confidence: str          # "HIGH" | "MEDIUM" | "LOW"
    time_horizon: str        # "1W" | "1M" | "3M"
    current_price: float
    target_price: float
    upside_pct: float
    reasoning: str
    key_risks: list = field(default_factory=list)
    suggested_action: str = "HOLD"  # "BUY" | "SELL" | "HOLD" | "AVOID"
    model: str = ""


SYSTEM_PROMPT = """You are an expert equity analyst and portfolio manager with deep experience
in fundamental and technical analysis.

Your job is to analyze individual stocks and provide a structured investment outlook.
You consider:
- Current price vs 52-week range and historical context
- Volume trends and institutional activity signals
- Sector and macro environment
- Recent news and sentiment
- Cost basis and unrealized gain/loss for existing positions
- Beta, dividend profile, and earnings trajectory
- Upcoming catalysts (earnings, product launches, regulatory events)

You are calibrated: your HIGH-confidence BULLISH calls are right more than 65% of the time.
You flag genuine risks and do not overpromise upside.

You output ONLY valid JSON — no markdown fences, no preamble. Schema:
{
  "outlook": "BULLISH" | "BEARISH" | "NEUTRAL",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "time_horizon": "1W" | "1M" | "3M",
  "target_price": <float>,
  "upside_pct": <float>,
  "reasoning": "<2-4 sentences>",
  "key_risks": ["<risk 1>", "<risk 2>"],
  "suggested_action": "BUY" | "SELL" | "HOLD" | "AVOID"
}

Definitions:
- BULLISH: expect the stock to outperform the market over the time horizon
- BEARISH: expect the stock to underperform or decline
- NEUTRAL: no strong directional view; hold existing position
- HIGH confidence: strong evidence across multiple data sources
- MEDIUM confidence: mixed signals or limited data
- LOW confidence: highly speculative; rely mainly on broad market trends
"""


def _build_analysis_prompt(
    symbol,
    company_name,
    current_price,
    high_52w,
    low_52w,
    avg_volume,
    current_volume,
    sector,
    cost_basis,
    beta,
    dividend_yield,
    dividend_rate,
    eps_forward,
    earnings_date,
    news_items,
    sentiment,
):
    """Build the full analysis prompt for a given stock."""

    company_str = company_name or symbol
    range_str = (
        f"${low_52w:.2f} - ${high_52w:.2f}"
        if (low_52w and high_52w)
        else "unknown"
    )

    vol_str = "unknown"
    if avg_volume and current_volume and avg_volume > 0:
        vol_str = f"{current_volume:,.0f} ({current_volume/avg_volume:.1f}x avg of {avg_volume:,.0f})"

    cost_str = "Not held"
    if cost_basis and cost_basis > 0:
        unrealized_pct = (current_price - cost_basis) / cost_basis * 100
        cost_str = f"${cost_basis:.2f} (unrealized {unrealized_pct:+.1f}%)"

    div_str = "None"
    if dividend_yield or dividend_rate:
        parts = []
        if dividend_rate:
            parts.append(f"${dividend_rate:.2f}/yr")
        if dividend_yield:
            parts.append(f"{dividend_yield*100:.2f}% yield")
        div_str = " | ".join(parts)

    news_text = ""
    if news_items:
        headlines = [f"- [{n['source']}] {n['headline']}" for n in news_items[:15]]
        news_text = "RECENT NEWS:\n" + "\n".join(headlines)

    sentiment_text = ""
    if sentiment:
        sentiment_text = (
            f"SENTIMENT: hype_score={sentiment.get('hype_score', 0):.2f} "
            f"mentions={sentiment.get('mention_count', 0)} "
            f"platform={sentiment.get('platform', '')}"
        )

    return f"""STOCK: {company_str} ({symbol})

CURRENT PRICE:      ${current_price:.2f}
52-WEEK RANGE:      {range_str}
VOLUME (TODAY):     {vol_str}
SECTOR:             {sector or 'unknown'}
BETA:               {beta if beta is not None else 'unknown'}
DIVIDEND:           {div_str}
FORWARD EPS:        {f'${eps_forward:.2f}' if eps_forward else 'unknown'}
NEXT EARNINGS:      {earnings_date or 'unknown'}
COST BASIS:         {cost_str}

{news_text}

{sentiment_text}

Analyze {symbol} and provide your investment outlook as JSON. Be specific about
the target price and reasoning. Highlight the 2-3 most important risks."""


class StockAnalyzer:
    """
    Uses Claude API to analyze individual stocks and produce structured outlooks.
    """

    def __init__(self, db: Database):
        self.db = db
        self.client = anthropic.AsyncAnthropic(api_key=config.anthropic.api_key)
        self.model = config.anthropic.model

    async def _get_claude_analysis(self, prompt: str) -> Optional[dict]:
        """Call Claude API and parse the JSON analysis response."""
        try:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=config.anthropic.max_tokens,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                response = await stream.get_final_message()

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
            return json.loads(raw.strip())

        except json.JSONDecodeError as exc:
            logger.warning("Claude returned invalid JSON for stock analysis: %s", exc)
            return None
        except anthropic.APIError as exc:
            logger.error("Anthropic API error during stock analysis: %s", exc)
            return None
        except Exception as exc:
            logger.error("Unexpected error in _get_claude_analysis: %s", exc)
            return None

    async def analyze_stock(
        self,
        symbol: str,
        current_price: float,
        market_info: Optional[dict] = None,
        position_info: Optional[dict] = None,
    ) -> Optional[StockAnalysis]:
        """
        Full analysis pipeline for a single stock.

        Args:
            symbol:        Equity ticker symbol.
            current_price: Latest trade price.
            market_info:   Dict from YahooFinanceClient.get_quote_summary().
            position_info: Dict from BrokerClient.get_positions() for this symbol.
        """
        if market_info is None:
            market_info = {}
        if position_info is None:
            position_info = {}

        company_name = market_info.get("name")
        high_52w = market_info.get("fifty_two_week_high")
        low_52w = market_info.get("fifty_two_week_low")
        avg_volume = market_info.get("avg_volume")
        current_volume = market_info.get("current_volume")
        sector = market_info.get("sector")
        beta = market_info.get("beta")
        dividend_yield = market_info.get("dividend_yield")
        dividend_rate = market_info.get("dividend_rate")
        eps_forward = market_info.get("eps_forward")
        earnings_date = market_info.get("earnings_date")

        cost_basis = position_info.get("avg_entry_price")

        # Gather recent news relevant to the stock
        news = self.db.get_recent_news(hours=48, limit=100)
        symbol_lower = symbol.lower()
        company_words = set((company_name or "").lower().split()) if company_name else set()
        relevant_news = [
            n for n in news
            if symbol_lower in n.get("headline", "").lower()
            or any(w in n.get("headline", "").lower() for w in company_words if len(w) > 4)
        ][:15]

        sentiment = self.db.get_latest_sentiment(symbol)

        prompt = _build_analysis_prompt(
            symbol=symbol,
            company_name=company_name,
            current_price=current_price,
            high_52w=high_52w,
            low_52w=low_52w,
            avg_volume=avg_volume,
            current_volume=current_volume,
            sector=sector,
            cost_basis=cost_basis,
            beta=beta,
            dividend_yield=dividend_yield,
            dividend_rate=dividend_rate,
            eps_forward=eps_forward,
            earnings_date=earnings_date,
            news_items=relevant_news,
            sentiment=sentiment,
        )

        result = await self._get_claude_analysis(prompt)
        if result is None:
            return None

        outlook = result.get("outlook", "NEUTRAL")
        confidence = result.get("confidence", "LOW")
        time_horizon = result.get("time_horizon", "1M")
        target_price = float(result.get("target_price", current_price))
        upside_pct = float(result.get("upside_pct", 0.0))
        reasoning = result.get("reasoning", "")
        key_risks = result.get("key_risks", [])
        suggested_action = result.get("suggested_action", "HOLD")

        # Recalculate upside_pct from target if not provided
        if upside_pct == 0.0 and current_price > 0:
            upside_pct = (target_price - current_price) / current_price * 100

        # Persist to database
        self.db.log_stock_analysis({
            "symbol": symbol,
            "company_name": company_name or symbol,
            "ai_outlook": outlook,
            "confidence": confidence,
            "price_at_analysis": current_price,
            "target_price": target_price,
            "upside_pct": round(upside_pct, 2),
            "reasoning": reasoning,
            "key_risks": key_risks,
            "model": self.model,
        })

        logger.info(
            "Stock analysis: %s | outlook=%s confidence=%s target=$%.2f upside=%.1f%% action=%s",
            symbol, outlook, confidence, target_price, upside_pct, suggested_action,
        )

        return StockAnalysis(
            symbol=symbol,
            company_name=company_name or symbol,
            outlook=outlook,
            confidence=confidence,
            time_horizon=time_horizon,
            current_price=current_price,
            target_price=target_price,
            upside_pct=upside_pct,
            reasoning=reasoning,
            key_risks=key_risks,
            suggested_action=suggested_action,
            model=self.model,
        )
