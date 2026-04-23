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

### 3. Copy Trade Mode
Activated with `--copy-trade [WALLET]`. Follows the moves of a target wallet in real-time.

---

## 📊 Live Dashboard & Monitoring

The bot generates a premium HTML dashboard (`live_dashboard.html` or `virtual_dashboard.html`) that allows you to monitor your performance in real-time:

- **Auto-Refresh**: The page updates automatically every 15 seconds.
- **Detailed Tracking**: The trade history table shows the specific asset (e.g., `BTC UP`) and distinguishes between normal entries and Martingale (Gale) recovery trades.
- **Visual Charts**: Live equity curve showing your balance evolution.

---

## ⚙️ Setup & Installation

### 1. Install Dependencies
Ensure you have Python 3.10+ installed. Run the following command to install required libraries:
```bash
pip install -r requirements.txt
```

### 2. Configuration (`.env`)
Create a `.env` file in the root folder with your credentials:
```env
PRIVATE_KEY=0xYOUR_PRIVATE_KEY
SIGNATURE_TYPE=0 
```
*Note: The bot will automatically generate and save your Polymarket API keys to this file on the first run.*

### 3. Market Choice (`config.py`)
You can choose which assets to trade by editing the `ACTIVE_MARKETS` list:
```python
ACTIVE_MARKETS = ["BTC", "ETH", "SOL"]  # Or "ALL" to trade everything
```

---

## ▶️ Usage

### Running in Virtual Mode (Simulation)
Test your settings with virtual money:
```bash
python bot.py --virtual 1000
```

### Running in Real Mode (Real Money)
```bash
python bot.py
```

### Running the Sniper
```bash
python bot.py --sniper
```

### Running Copy Trade
```bash
python bot.py --copy-trade 0x123...
```

---

## 🛡️ Risk Management
- **Automatic Approvals**: Handles USDC.e and CTF contract approvals on first use.
- **Clock Sync**: Automatically synchronizes with Binance server time for precise entries.
- **Stop Loss & Indecision Exit**: Built-in mechanisms to exit risky positions before the candle close.

*Trade responsibly. High volatility markets involve risk!* 📈
