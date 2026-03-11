"""
Layer 5 — Alert system.

Sends critical alerts via:
  1. Webhook (Slack, Discord, custom HTTP endpoint)
  2. Email (SMTP)

Alerts are throttled to avoid spam (max 1 per type per hour).
"""

import asyncio
import logging
import smtplib
import time
from collections import defaultdict
from email.mime.text import MIMEText
from typing import Optional

import aiohttp

from config.settings import config

logger = logging.getLogger(__name__)


class AlertSystem:
    """Sends critical alerts to configured destinations."""

    def __init__(self):
        self._last_sent: dict[str, float] = defaultdict(float)
        self._throttle_seconds = 3600  # 1 hour between identical alerts

    def _should_send(self, message: str) -> bool:
        """Throttle: only send the same alert once per hour."""
        key = message[:80]  # use first 80 chars as key
        last = self._last_sent[key]
        if time.time() - last > self._throttle_seconds:
            self._last_sent[key] = time.time()
            return True
        return False

    async def send(self, message: str, level: str = "WARNING"):
        """Send an alert to all configured destinations."""
        if not self._should_send(message):
            return

        full_message = f"[{level}] Prediction Market Engine: {message}"
        logger.warning("ALERT: %s", full_message)

        tasks = []
        if config.alerts.alert_webhook_url:
            tasks.append(self._send_webhook(full_message))
        if config.alerts.alert_email:
            tasks.append(asyncio.to_thread(self._send_email, full_message))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_webhook(self, message: str):
        """POST to a Slack/Discord/custom webhook."""
        payload = {"text": message, "content": message}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    config.alerts.alert_webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status < 300:
                        logger.debug("Webhook alert sent")
                    else:
                        logger.warning("Webhook returned %d", resp.status)
        except Exception as exc:
            logger.error("Webhook send failed: %s", exc)

    def _send_email(self, message: str):
        """Send via SMTP (requires SMTP env vars — not shown in .env.example for brevity)."""
        smtp_host = config.alerts.alert_email
        if not smtp_host:
            return
        try:
            msg = MIMEText(message)
            msg["Subject"] = "Prediction Market Engine Alert"
            msg["From"] = "alerts@prediction-engine.local"
            msg["To"] = config.alerts.alert_email
            with smtplib.SMTP("localhost", 25) as server:
                server.send_message(msg)
        except Exception as exc:
            logger.debug("Email send failed (SMTP not configured): %s", exc)
