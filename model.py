"""
Probability model for Kalshi ETH 15-minute up/down markets.

Settlement rule (KXETH15M):
  YES if 60-second simple average of CF Benchmarks ETHUSD_RTI at close
  >= 60-second simple average at open (the floor_strike).

We approximate P(YES) with a log-normal random-walk model conditioned on
current price, time remaining, and recent realized volatility.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional


@dataclass
class Prediction:
    ticker: str
    floor_strike: float
    current_price: float
    price_source: str
    seconds_left: float
    p_yes: float
    p_no: float
    side: str          # "YES" | "NO" | "NEUTRAL"
    confidence: float  # max(p_yes, p_no)
    reasoning: str
    market_yes_mid: Optional[float] = None
    edge_vs_market: Optional[float] = None


class Eth15mModel:
    def __init__(
        self,
        vol_lookback_seconds: int = 120,
        fallback_vol: float = 0.0009,
        min_confidence: float = 0.55,
    ):
        self.vol_lookback = vol_lookback_seconds
        self.fallback_vol = fallback_vol
        self.min_confidence = min_confidence
        self._prices: Deque[float] = deque(maxlen=300)
        self._times: Deque[float] = deque(maxlen=300)

    def update_price(self, price: float) -> None:
        now = time.time()
        self._prices.append(price)
        self._times.append(now)

    def realized_vol(self) -> float:
        """Per-sqrt-second volatility from recent log returns."""
        if len(self._prices) < 8:
            return self.fallback_vol
        cutoff = time.time() - self.vol_lookback
        rets = []
        for i in range(1, len(self._prices)):
            if self._times[i] < cutoff:
                continue
            dt = self._times[i] - self._times[i - 1]
            if dt <= 0:
                continue
            ret = math.log(self._prices[i] / self._prices[i - 1])
            rets.append(ret / math.sqrt(dt))
        if len(rets) < 5:
            return self.fallback_vol
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
        return max(math.sqrt(var), 1e-8)

    def p_finish_above(
        self, current: float, floor: float, seconds_left: float
    ) -> float:
        """
        Approximate P(S_T >= floor) under geometric Brownian motion
        with zero drift (short horizon) and constant vol.
        """
        if seconds_left <= 1.0:
            return 1.0 if current >= floor else 0.0
        if current <= 0 or floor <= 0:
            return 0.5

        vol = self.realized_vol()
        sigma = vol * math.sqrt(seconds_left)
        if sigma < 1e-10:
            return 1.0 if current >= floor else 0.0

        # Digital call under risk-neutral measure with r=0, mu=0
        # P = N(d2) where d2 = [ln(S/K) - 0.5 sigma^2] / sigma
        d2 = (math.log(current / floor) - 0.5 * sigma * sigma) / sigma
        return 0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0)))

    def predict(
        self,
        ticker: str,
        floor_strike: float,
        current_price: float,
        price_source: str,
        seconds_left: float,
        market_yes_bid: Optional[float] = None,
        market_yes_ask: Optional[float] = None,
    ) -> Prediction:
        p_yes = self.p_finish_above(current_price, floor_strike, seconds_left)
        p_yes = max(0.0, min(1.0, p_yes))
        p_no = 1.0 - p_yes

        if p_yes >= self.min_confidence:
            side = "YES"
            conf = p_yes
        elif p_no >= self.min_confidence:
            side = "NO"
            conf = p_no
        else:
            side = "NEUTRAL"
            conf = max(p_yes, p_no)

        # Distance from floor in volatility units
        vol = self.realized_vol()
        sigma_t = vol * math.sqrt(max(seconds_left, 1.0))
        if sigma_t > 0:
            z = math.log(current_price / floor_strike) / sigma_t
        else:
            z = 0.0

        dist_bps = (current_price / floor_strike - 1.0) * 10_000

        reasoning_parts = [
            f"Current price {current_price:.2f} vs floor {floor_strike:.2f} "
            f"({dist_bps:+.1f} bps).",
            f"Time left: {seconds_left:.0f}s.",
            f"Realized vol (per √s): {vol:.6f} → σ√T ≈ {sigma_t*100:.2f}%.",
            f"Model P(YES) = {p_yes:.1%} (z-score ≈ {z:+.2f}).",
        ]

        market_mid = None
        edge = None
        if market_yes_bid is not None and market_yes_ask is not None:
            market_mid = (market_yes_bid + market_yes_ask) / 2.0
            edge = p_yes - market_mid
            reasoning_parts.append(
                f"Market YES mid ≈ {market_mid:.1%} → model edge {edge:+.1%}."
            )
        elif market_yes_ask is not None:
            market_mid = market_yes_ask
            edge = p_yes - market_mid
            reasoning_parts.append(
                f"Market YES ask ≈ {market_mid:.1%} → model edge {edge:+.1%}."
            )

        if side == "YES":
            reasoning_parts.append(
                "Prediction: YES — price is sufficiently above the floor "
                "relative to remaining uncertainty."
            )
        elif side == "NO":
            reasoning_parts.append(
                "Prediction: NO — price is sufficiently below the floor "
                "relative to remaining uncertainty."
            )
        else:
            reasoning_parts.append(
                "Prediction: NEUTRAL — within the uncertainty band; "
                "no high-confidence directional call."
            )

        reasoning_parts.append(f"Price source: {price_source}.")

        return Prediction(
            ticker=ticker,
            floor_strike=floor_strike,
            current_price=current_price,
            price_source=price_source,
            seconds_left=seconds_left,
            p_yes=p_yes,
            p_no=p_no,
            side=side,
            confidence=conf,
            reasoning=" ".join(reasoning_parts),
            market_yes_mid=market_mid,
            edge_vs_market=edge,
        )
