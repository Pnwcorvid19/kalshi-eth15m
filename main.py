#!/usr/bin/env python3
"""
Kalshi ETH 15-minute market predictor (prediction only – no trading).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_client import KalshiClient, seconds_until
from src.model import Eth15mModel

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    RICH = True
except ImportError:
    RICH = False

def send_telegram(message: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram credentials missing")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print("Telegram message sent")
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

log = logging.getLogger("predictor")

def load_config() -> dict:
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            return yaml.safe_load(f) or {}
    return {}

def load_client() -> KalshiClient:
    load_dotenv()
    key_id = os.getenv("KALSHI_API_KEY_ID") or ""
    key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH") or ""
    env = os.getenv("KALSHI_ENV", "prod")

    pem = None
    if key_path and Path(key_path).exists():
        pem = Path(key_path).read_bytes()
    elif key_id:
        log.warning("KALSHI_API_KEY_ID set but private key file missing – public mode")

    return KalshiClient(api_key_id=key_id or None, private_key_pem=pem, env=env)

def render_rich(pred, market: dict, console: "Console") -> None:
    side_color = {
        "YES": "green",
        "NO": "red",
        "NEUTRAL": "yellow",
    }[pred.side]

    title = Text(f"KXETH15M  ·  {pred.ticker}", style="bold")
    body = Table.grid(padding=(0, 2))
    body.add_column(style="cyan", justify="right")
    body.add_column()

    body.add_row("Prediction", Text(pred.side, style=f"bold {side_color}"))
    body.add_row("Confidence", f"{pred.confidence:.1%}")
    body.add_row("P(YES)", f"{pred.p_yes:.1%}")
    body.add_row("P(NO)", f"{pred.p_no:.1%}")
    body.add_row("ETH price", f"{pred.current_price:.2f}  ({pred.price_source})")
    body.add_row("Floor strike", f"{pred.floor_strike:.2f}")
    body.add_row("Time left", f"{pred.seconds_left:.0f}s")
    if pred.market_yes_mid is not None:
        body.add_row("Market YES mid", f"{pred.market_yes_mid:.1%}")
    if pred.edge_vs_market is not None:
        edge_style = "green" if pred.edge_vs_market > 0.02 else "red" if pred.edge_vs_market < -0.02 else "white"
        body.add_row("Model edge", Text(f"{pred.edge_vs_market:+.1%}", style=edge_style))

    panel = Panel(
        body,
        title=title,
        subtitle=pred.reasoning[:220] + ("…" if len(pred.reasoning) > 220 else ""),
        border_style=side_color,
    )
    console.print(panel)

def render_plain(pred) -> None:
    print("=" * 60)
    print(f"TICKER     : {pred.ticker}")
    print(f"PREDICTION : {pred.side}   (confidence {pred.confidence:.1%})")
    print(f"P(YES)     : {pred.p_yes:.1%}")
    print(f"P(NO)      : {pred.p_no:.1%}")
    print(f"PRICE      : {pred.current_price:.2f}  [{pred.price_source}]")
    print(f"FLOOR      : {pred.floor_strike:.2f}")
    print(f"TIME LEFT  : {pred.seconds_left:.0f}s")
    if pred.market_yes_mid is not None:
        print(f"MKT YES    : {pred.market_yes_mid:.1%}")
    if pred.edge_vs_market is not None:
        print(f"EDGE       : {pred.edge_vs_market:+.1%}")
    print(f"REASON     : {pred.reasoning}")
    print("=" * 60)

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = load_config()
    poll = int(os.getenv("POLL_INTERVAL_SECONDS") or cfg.get("poll_interval_seconds", 5))
    min_conf = float(os.getenv("MIN_CONFIDENCE") or cfg.get("min_confidence", 0.55))
    model_cfg = cfg.get("model") or {}

    client = load_client()
    model = Eth15mModel(
        vol_lookback_seconds=int(model_cfg.get("vol_lookback_seconds", 120)),
        fallback_vol=float(model_cfg.get("fallback_vol", 0.0009)),
        min_confidence=min_conf,
    )

    min_left = float(model_cfg.get("min_seconds_left", 8))
    max_left = float(model_cfg.get("max_seconds_left", 870))

    console = Console() if RICH else None
    log.info(
        "ETH 15m predictor started | auth=%s | min_confidence=%.0f%%",
        client.authenticated,
        min_conf * 100,
    )

    last_ticker = None

    while True:
        try:
            market = client.get_open_eth15m()
            if not market:
                log.info("No open KXETH15M market right now")
                time.sleep(poll)
                continue

            ticker = market["ticker"]
            floor = float(market.get("floor_strike") or 0)
            if floor <= 0:
                log.warning("Missing floor_strike on %s", ticker)
                time.sleep(poll)
                continue

            seconds_left = seconds_until(market["close_time"])
            if seconds_left < min_left or seconds_left > max_left:
                time.sleep(poll)
                continue

            if ticker != last_ticker:
                last_ticker = ticker
                log.info("New window: %s  floor=%.2f  closes in %.0fs", ticker, floor, seconds_left)

            price, source = client.current_eth_price()
            if price is None:
                log.warning("Could not obtain ETH price")
                time.sleep(poll)
                continue

            model.update_price(price)

            yes_bid = _safe_float(market.get("yes_bid_dollars"))
            yes_ask = _safe_float(market.get("yes_ask_dollars"))

            pred = model.predict(
                ticker=ticker,
                floor_strike=floor,
                current_price=price,
                price_source=source,
                seconds_left=seconds_left,
                market_yes_bid=yes_bid,
                market_yes_ask=yes_ask,
            )

            if RICH and console:
                render_rich(pred, market, console)
            else:
                render_plain(pred)

            msg = (
                f"KXETH15M {pred.ticker}\n"
                f"Prediction: {pred.side} ({pred.confidence:.1%})\n"
                f"P(YES): {pred.p_yes:.1%} | Price: {pred.current_price:.2f}\n"
                f"Floor: {pred.floor_strike:.2f} | Time left: {pred.seconds_left:.0f}s"
            )
            send_telegram(msg)

        except KeyboardInterrupt:
            log.info("Stopped by user")
            break
        except Exception as e:
            log.exception("Loop error: %s", e)
            time.sleep(max(poll, 5))

        time.sleep(poll)

def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

if __name__ == "__main__":
    main()