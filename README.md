# Polymarket Multi-Market Trading Bot 🎯

An automated trading bot for **Polymarket Up/Down (5M)** markets. The bot uses real-time data from Binance to execute high-probability trades across multiple assets (BTC, ETH, SOL, XRP, BNB, DOGE).

---

## 🚀 Main Strategies

### 1. Reversal Sequence (Standard Mode)
Monitors price candles. When it detects a sequence (e.g., 2 consecutive UP candles), it places a reversal trade (DOWN) on Polymarket.
- **Martingale**: If a trade loses, it can automatically double-down (Gale) on the next opportunity to recover.
- **Early Cash Out**: If the price hits your profit target mid-candle, it sells early to lock in gains.

### 2. Whale Sniper Mode
Activated with `--sniper`. Monitors large "Whale" trades on Binance in the final seconds of a candle.
- Detects where big money is pushing.
- Enters Polymarket immediately to follow the whale's direction.
- **Independent**: Does not use Martingale; each trade is a single "sniper" shot.

---

## 🛡️ Risk Management (Built-in)

- **Position Stop Loss**: Each trade has its own stop loss. If the position drops significantly, the bot exits to protect your balance.
- **Indecision Exit**: In the final 10 seconds, if the price is near 0.50 (undecided), the bot sells early to avoid the "coin flip" risk of the close.
- **Auto-Redeem**: Automatically claims your winning tickets and returns USDC.e to your wallet.
- **Balance Guard**: Stops immediately if your balance is insufficient.

---

## ⚙️ Setup

### 1. Requirements
```bash
pip install -r requirements.txt
```

### 2. Configuration (`.env`)
Create a `.env` file with your credentials:
```env
PRIVATE_KEY=0x...
SIGNATURE_TYPE=0
```

### 3. Market Choice (`config.py`)
Edit `config.py` to choose your markets:
```python
ACTIVE_MARKETS = ["BTC", "ETH", "SOL"]  # Or "ALL"
```

---

## ▶️ How to Run

### Standard Mode (Reversal + Martingale)
```bash
python bot.py
```

### Sniper Mode (Whale Tracking)
```bash
python bot.py --sniper
```

### Copy Trade Mode (Follow a Wallet)
```bash
python bot.py --copy-trade 0xWALLET_ADDRESS
```

---

## 🛠️ Maintenance
- The bot handles **USDC.e and CTF approvals** automatically on first run.
- It syncs your clock with Binance to ensure perfect entry timing.

*Trade responsibly. High volatility markets involve risk!* 📈
