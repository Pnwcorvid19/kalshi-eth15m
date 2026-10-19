#!/usr/bin/env python3
"""
Kalshi ETH 15-minute market predictor (prediction only – no trading).

Continuously watches the open KXETH15M window, estimates P(YES) using a
realized-volatility log-normal model against the official floor strike,
and prints a clear prediction + reasoning.

Usage:
    python -m src.main
    # or
    python src/main.py
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

# Allow running as script or module
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
