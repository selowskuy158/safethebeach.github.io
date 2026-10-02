"""High-impact news blackout.

FundedNext's News Profit Rule: on funded accounts only 40% of profit counts for
trades opened or closed within 5 minutes before/after a listed high-impact event.
The bot avoids opening trades inside a (configurable, wider) window.

data/news.json is a list of events, e.g.
  [{"time": "2026-10-02T12:30:00Z", "currency": "USD", "impact": "high", "title": "Non-Farm Payrolls"}]
Refresh it weekly from the FundedNext news calendar (see README: "FundedNext MCP").
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def symbol_currencies(symbol: str) -> set[str]:
    """EURUSD -> {EUR, USD}; XAUUSD.r -> {XAU, USD}."""
    letters = "".join(ch for ch in symbol.upper() if ch.isalpha())
    return {letters[:3], letters[3:6]} if len(letters) >= 6 else {letters}


class NewsFilter:
    def __init__(self, path: str | Path | None, minutes_before: int = 10, minutes_after: int = 10,
                 impacts: tuple[str, ...] = ("high",)):
        self.before = pd.Timedelta(minutes=minutes_before)
        self.after = pd.Timedelta(minutes=minutes_after)
        self.events = self._load(path, impacts)

    @staticmethod
    def _load(path, impacts) -> list[tuple[pd.Timestamp, str, str]]:
        if not path or not Path(path).exists():
            log.warning("No news file at %s: news blackout disabled", path)
            return []
        events = []
        for e in json.loads(Path(path).read_text()):
            if str(e.get("impact", "high")).lower() not in impacts:
                continue
            ts = pd.Timestamp(e["time"])
            ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
            events.append((ts, str(e.get("currency", "")).upper(), e.get("title", "")))
        return events

    def blocked(self, symbol: str, now_utc: pd.Timestamp) -> str | None:
        """Title of the event blocking `symbol` at `now_utc`, else None."""
        if now_utc.tzinfo is None:
            now_utc = now_utc.tz_localize("UTC")
        currencies = symbol_currencies(symbol)
        for when, currency, title in self.events:
            if currency in currencies and when - self.before <= now_utc <= when + self.after:
                return f"{currency} {title} @ {when:%Y-%m-%d %H:%M}Z"
        return None
