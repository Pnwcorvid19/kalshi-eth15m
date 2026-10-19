#!/usr/bin/env python3
"""
Kalshi ETH 15m predictor with web dashboard + Telegram toggle.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import threading
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify

import yaml
from dotenv import load_dotenv
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_client import KalshiClient, seconds_until
from src.model import Eth15mModel

app = Flask(__name__)

# Shared state
latest_prediction = {"text": "Waiting for first prediction...", "raw": None}
telegram_enabled = True
last_update_id = 0
running = True

def send_telegram(message: str):
    if not telegram_enabled:
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram send failed: {e}")

def check_telegram_commands():
    global last_update_id, telegram_enabled, running
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        resp = requests.get(url, params={"offset": last_update_id + 1, "timeout": 1}, timeout=5)
        data = resp.json()
        if not data.get("ok"):
            return
        for update in data.get("result", []):
            last_update_id = update["update_id"]
            msg = update.get("message", {})
            text = msg.get("text", "")
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if chat_id != os.getenv("TELEGRAM_CHAT_ID"):
                continue
            if text == "/telegram_on":
                telegram_enabled = True
                send_telegram("Telegram notifications turned ON")
            elif text == "/telegram_off":
                send_telegram("Telegram notifications turned OFF")
                telegram_enabled = False
            elif text == "/status":
                status = "ON" if telegram_enabled else "OFF"
                send_telegram(f"Bot running. Telegram notifications: {status}")
    except Exception as e:
        print(f"Telegram check failed: {e}")

def prediction_loop():
    global latest_prediction, running
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    cfg = {}
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}

    poll = int(os.getenv("POLL_INTERVAL_SECONDS") or cfg.get("poll_interval_seconds", 5))
    min_conf = float(os.getenv("MIN_CONFIDENCE") or cfg.get("min_confidence", 0.55))
    model_cfg = cfg.get("model") or {}

    key_id = os.getenv("KALSHI_API_KEY_ID") or ""
    key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH") or ""
    env = os.getenv("KALSHI_ENV", "prod")
    pem = Path(key_path).read_bytes() if key_path and Path(key_path).exists() else None
    client = KalshiClient(api_key_id=key_id or None, private_key_pem=pem, env=env)
    model = Eth15mModel(
        vol_lookback_seconds=int(model_cfg.get("vol_lookback_seconds", 120)),
        fallback_vol=float(model_cfg.get("fallback_vol", 0.0009)),
        min_confidence=min_conf,
    )

    min_left = float(model_cfg.get("min_seconds_left", 8))
    max_left = float(model_cfg.get("max_seconds_left", 870))
    last_ticker = None

    send_telegram("Bot started. Commands: /telegram_on /telegram_off /status")

    while running:
        try:
            check_telegram_commands()
            market = client.get_open_eth15m()
            if not market:
                time.sleep(poll)
                continue

            ticker = market["ticker"]
            floor = float(market.get("floor_strike") or 0)
            if floor <= 0:
                time.sleep(poll)
                continue

            seconds_left = seconds_until(market["close_time"])
            if seconds_left < min_left or seconds_left > max_left:
                time.sleep(poll)
                continue

            price, source = client.current_eth_price()
            if price is None:
                time.sleep(poll)
                continue

            model.update_price(price)
            yes_bid = float(market.get("yes_bid_dollars") or 0) or None
            yes_ask = float(market.get("yes_ask_dollars") or 0) or None

            pred = model.predict(
                ticker=ticker,
                floor_strike=floor,
                current_price=price,
                price_source=source,
                seconds_left=seconds_left,
                market_yes_bid=yes_bid,
                market_yes_ask=yes_ask,
            )

            text = (
                f"Ticker: {pred.ticker}\n"
                f"Prediction: {pred.side}\n"
                f"Confidence: {pred.confidence:.1%}\n"
                f"P(YES): {pred.p_yes:.1%}\n"
                f"Price: {pred.current_price:.2f}\n"
                f"Floor: {pred.floor_strike:.2f}\n"
                f"Time left: {pred.seconds_left:.0f}s"
            )
            latest_prediction = {"text": text, "raw": pred}
            send_telegram(text)

        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(max(poll, 5))
        time.sleep(poll)

@app.route("/")
def index():
    tg_status = "ON" if telegram_enabled else "OFF"
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>ETH 15m Predictor</title>
        <meta http-equiv="refresh" content="10">
        <style>
            body { font-family: Arial, sans-serif; background: #111; color: #eee; padding: 20px; }
            pre { background: #222; padding: 15px; border-radius: 8px; white-space: pre-wrap; }
            .status { margin: 10px 0; }
            a { color: #4ea; }
        </style>
    </head>
    <body>
        <h1>KXETH15M Predictor</h1>
        <div class="status">Telegram notifications: <b>{{ tg_status }}</b></div>
        <pre>{{ prediction }}</pre>
        <p><a href="/telegram/on">Turn Telegram ON</a> | <a href="/telegram/off">Turn Telegram OFF</a></p>
        <p>Page auto-refreshes every 10 seconds.</p>
    </body>
    </html>
    """
    return render_template_string(html, prediction=latest_prediction["text"], tg_status=tg_status)

@app.route("/telegram/<state>")
def toggle_telegram(state):
    global telegram_enabled
    if state == "on":
        telegram_enabled = True
        send_telegram("Telegram notifications turned ON via web")
    elif state == "off":
        send_telegram("Telegram notifications turned OFF via web")
        telegram_enabled = False
    return jsonify({"telegram_enabled": telegram_enabled})

if __name__ == "__main__":
    # Start prediction loop in background
    t = threading.Thread(target=prediction_loop, daemon=True)
    t.start()
    # Start web server (Render sets PORT)
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)