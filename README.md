# Kalshi ETH 15-Minute Predictor

**Prediction-only** bot for Kalshi’s Ethereum 15-minute up/down markets (`KXETH15M`).

It watches the currently open window, estimates the probability that the official settlement index finishes at or above the locked floor strike, and prints a clear **YES / NO / NEUTRAL** call with confidence and reasoning.

> This project **does not place orders**. There is no trading code.

---

## How the market works

Every 15 minutes Kalshi opens a binary contract:

- **YES** pays $1 if the 60-second simple average of CF Benchmarks **ETHUSD_RTI** at the end of the window is **≥** the 60-second average at the open (the `floor_strike`).
- Otherwise **NO** pays $1.

A new window opens 24/7. The bot always tracks the soonest-closing open market.

---

## Prediction model (why these odds)

The model is a short-horizon **log-normal random walk** (geometric Brownian motion with zero drift):

1. Continuously records the live ETH price.
2. Estimates **realized volatility** from the most recent ~2 minutes of log returns.
3. Computes  
   \( P(\text{finish} \ge \text{floor}) = \Phi(d_2) \)  
   where  
   \( d_2 = \dfrac{\ln(S/K) - \frac12\sigma^2 T}{\sigma\sqrt{T}} \).
4. If that probability (or \(1-p\)) exceeds the configured confidence threshold (default 55 %), it emits a directional call; otherwise it stays **NEUTRAL**.

**Why this is a reasonable baseline**

- Settlement is itself a 60-second average of the official index → using the same index (or a close proxy) removes most basis risk.
- Over a 15-minute horizon the dominant factor is current distance from the floor measured in units of remaining volatility.
- The model is transparent, has almost no free parameters, and can be inspected in `src/model.py`.

It is **not** a guaranteed edge. Kalshi markets are often efficient and fees on the trading side are high; this tool only produces probabilities and explanations.

---

## Quick start

```bash
git clone https://github.com/<you>/kalshi-eth15m-predictor.git
cd kalshi-eth15m-predictor

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Optional: copy and fill credentials for the official CF Benchmarks feed
cp .env.example .env
# edit .env if you have Kalshi API keys

python -m src.main
```

The bot runs forever, printing a fresh prediction every few seconds. Press `Ctrl+C` to stop.

### Optional better price feed

Without API keys the bot uses a public average of Binance + Coinbase ETH-USD.  
With Kalshi API keys it prefers the **exact** settlement index (`ETHUSD_RTI`) via Kalshi’s CF Benchmarks passthrough.

1. Log into Kalshi → Account & security → API Keys → Create Key.
2. Save the private key PEM and put the Key ID + path into `.env`:

```env
KALSHI_API_KEY_ID=your-key-id
KALSHI_PRIVATE_KEY_PATH=/absolute/path/to/kalshi.pem
KALSHI_ENV=prod
```

---

## Configuration

`config.yaml` (or environment variables):

| Key | Default | Meaning |
|-----|---------|---------|
| `poll_interval_seconds` | 5 | How often to refresh |
| `min_confidence` | 0.55 | Minimum P(side) to emit YES/NO instead of NEUTRAL |
| `model.vol_lookback_seconds` | 120 | Window for realized-vol estimate |
| `model.fallback_vol` | 0.0009 | Used when price history is short |
| `model.min_seconds_left` | 8 | Ignore windows about to expire |
| `model.max_seconds_left` | 870 | Ignore brand-new windows still opening |

---

## Output example

```
╭─ KXETH15M  ·  KXETH15M-26OCT190715 ─────────────────────────╮
│  Prediction     YES                                         │
│  Confidence     71.4%                                       │
│  P(YES)         71.4%                                       │
│  P(NO)          28.6%                                       │
│  ETH price      2428.35  (CF Benchmarks ETHUSD_RTI)          │
│  Floor strike   2423.47                                     │
│  Time left      312s                                        │
│  Market YES mid 58.0%                                       │
│  Model edge     +13.4%                                      │
╰─ Current price 2428.35 vs floor 2423.47 (+20.1 bps). … ─────╯
```

---

## Project layout

```
kalshi-eth15m-predictor/
├── README.md
├── requirements.txt
├── config.yaml
├── .env.example
├── src/
│   ├── __init__.py
│   ├── main.py          # entry point – run this
│   ├── kalshi_client.py # market data + optional CF Benchmarks
│   └── model.py         # probability model + Prediction dataclass
└── tests/
```

---

## Disclaimer

This is research / educational software. It does not place trades. Cryptocurrency prices and prediction-market outcomes are volatile. Past model performance is not indicative of future results. Use at your own risk.

---

## License

MIT
