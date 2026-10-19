"""
Lightweight Kalshi client focused on public market data + optional CF Benchmarks.
No trading endpoints.
"""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

PROD_BASE = "https://external-api.kalshi.com/trade-api/v2"
DEMO_BASE = "https://external-api.demo.kalshi.co/trade-api/v2"


class KalshiClient:
    def __init__(
        self,
        api_key_id: Optional[str] = None,
        private_key_pem: Optional[bytes] = None,
        env: str = "prod",
    ):
        self.api_key_id = api_key_id
        self.private_key = None
        self.base = DEMO_BASE if env == "demo" else PROD_BASE
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

        if private_key_pem and api_key_id:
            try:
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.backends import default_backend

                self.private_key = serialization.load_pem_private_key(
                    private_key_pem, password=None, backend=default_backend()
                )
                log.info("Kalshi authenticated client ready (CF Benchmarks available)")
            except Exception as e:
                log.warning("Failed to load private key – public mode only: %s", e)

    @property
    def authenticated(self) -> bool:
        return self.private_key is not None and bool(self.api_key_id)

    def _sign(self, timestamp: str, method: str, path: str) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, ed25519

        path = path.split("?")[0]
        msg = f"{timestamp}{method}{path}".encode()
        if isinstance(self.private_key, ed25519.Ed25519PrivateKey):
            sig = self.private_key.sign(msg)
        else:
            sig = self.private_key.sign(
                msg,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH,
                ),
                hashes.SHA256(),
            )
        return base64.b64encode(sig).decode()

    def _headers(self, method: str, path: str) -> dict:
        if not self.authenticated:
            return {}
        # path for signing must include /trade-api/v2 prefix
        sign_path = path if path.startswith("/trade-api") else f"/trade-api/v2{path}"
        ts = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": self._sign(ts, method, sign_path),
        }

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        url = self.base + path
        headers = self._headers("GET", path)
        r = self.session.get(url, headers=headers, params=params, timeout=12)
        r.raise_for_status()
        return r.json()

    # ---------- Public market helpers ----------

    def get_open_eth15m(self) -> Optional[dict]:
        """Return the soonest-closing open KXETH15M market, or None."""
        data = self.get(
            "/markets",
            {
                "series_ticker": "KXETH15M",
                "status": "open",
                "limit": 10,
            },
        )
        markets = data.get("markets") or []
        if not markets:
            return None
        markets.sort(key=lambda m: m.get("close_time") or "")
        return markets[0]

    def get_market(self, ticker: str) -> dict:
        return self.get(f"/markets/{ticker}")

    # ---------- Price feeds ----------

    def get_eth_rti(self) -> Optional[float]:
        """
        Official CF Benchmarks ETHUSD_RTI via Kalshi passthrough (auth required).
        Returns None if unauthenticated or request fails.
        """
        if not self.authenticated:
            return None
        try:
            data = self.get("/cfbenchmarks/values", {"id": "ETHUSD_RTI"})
            # Flexible parsing – Kalshi envelope can vary slightly
            if "data" in data:
                val = data["data"]
                if isinstance(val, list) and val:
                    item = val[0]
                    return float(item.get("value") or item.get("v") or item)
                if isinstance(val, dict):
                    return float(val.get("value") or val.get("v"))
            if "value" in data:
                return float(data["value"])
        except Exception as e:
            log.debug("CF Benchmarks fetch failed: %s", e)
        return None

    @staticmethod
    def get_public_eth_price() -> Optional[float]:
        """Fallback: mid of Binance + Coinbase ETH-USD (public, no key)."""
        prices = []
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "ETHUSDT"},
                timeout=5,
            )
            r.raise_for_status()
            prices.append(float(r.json()["price"]))
        except Exception:
            pass
        try:
            r = requests.get(
                "https://api.coinbase.com/v2/prices/ETH-USD/spot",
                timeout=5,
            )
            r.raise_for_status()
            prices.append(float(r.json()["data"]["amount"]))
        except Exception:
            pass
        if not prices:
            return None
        return sum(prices) / len(prices)

    def current_eth_price(self) -> tuple[Optional[float], str]:
        """Prefer official RTI; fall back to public exchange average."""
        rti = self.get_eth_rti()
        if rti is not None:
            return rti, "CF Benchmarks ETHUSD_RTI"
        pub = self.get_public_eth_price()
        if pub is not None:
            return pub, "public exchange mid (Binance+Coinbase)"
        return None, "unavailable"


def seconds_until(iso_ts: str) -> float:
    """Seconds remaining until an ISO-8601 close_time."""
    if iso_ts.endswith("Z"):
        iso_ts = iso_ts[:-1] + "+00:00"
    close = datetime.fromisoformat(iso_ts)
    if close.tzinfo is None:
        close = close.replace(tzinfo=timezone.utc)
    return (close - datetime.now(timezone.utc)).total_seconds()
