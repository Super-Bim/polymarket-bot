# Polymaster BTC Up/Down 5M Bot 🎯

An advanced Python bot engineered to automate trading in the 5-minute Bitcoin (UP/DOWN) market on the **Polymarket** platform. Operating securely via Smart Contracts on the Polygon network, this bot interfaces seamlessly with Polymarket's Central Limit Order Book (CLOB).

It supports two distinct execution modes: **Binance Oracle Strategy** (Pattern matching) and **Copy Trade** (On-chain Wallet Monitoring).

---

## 🚀 Key Features

- **Dual Execution Modes**: 
  - `NORMAL`: Intercepts Binance WebSockets in real-time. Capable of analyzing short-term market momentum and automatically placing counter-trend Martingale sequences.
  - `COPY TRADE`: Stealthily monitors a specific target wallet directly via Polygon Web3 RPC. It securely replicates the target's exact market movements (entries and exits) with zero API rate-limit delays, using your personal risk sizes.
- **Global Take Profit (Early Cash Out)**: Never leave money on the table. Both modes support an aggressive `PROFIT_TARGET_PERCENT` guard. When the configuration target is reached mid-candle, the bot autonomously sweeps the Orderbook, selling your micro-shares at Market value and securing instant profit before the candle closes.
- **Automated Profit Claim (Redeem)**: No more holding unredeemed tickets. An asynchronous worker runs in the background continuously scanning your completed positions. Once a market expires favorably, it automatically clears the CTF Smart Contracts and transfers your winnings back as raw `USDC.e`.
- **Zero Configuration Exchange Allowances**: Booting for the first time? The bot flawlessly scans and injects the `ERC20` (USDC) and `ERC1155` (CTF) Approval Contracts to the Polymarket Exchange on its own. 
- **Autonomous Martingale Ecosystem**: (Normal Mode only). Reacts to losses sequentially and responsibly, increasing bet sizes linearly to safely recoup capital within a safe margin of `MAX_GALES` limits.

---

## ⚙️ How it Works

### 1. Configuration (`config.py`)
No complex setups. Out of the box, `config.py` provides highly readable and simple options you can tweak:
- `BASE_TRADE_SIZE_USDC`: The base value you are comfortable investing per entry (minimum $1.0).
- `PROFIT_TARGET_PERCENT`: Target percentage to lock-in profits aggressively (e.g. `30.0` for 30%). Set to `0.0` to disable the feature and Hold-to-Maturity.
- *(Normal Mode)* Threshold Limits, sequence lengths, and Martingale multipliers for your risk hunger.

### 2. Authentication (`.env`)
The bot operates via your decentralized **Wallet** 
1. Inside `.env`, paste your `PRIVATE_KEY`
> **⚠️ WARNING:** Keep the `.env` local and secure. Never commit it to GitHub.

---

## ▶️ Usage Instructions

Install the necessary dependencies first:
```bash
pip install -r requirements.txt
```

### Starting the Binance Oracle Mode (Normal)
Monitors Binance's `BTCUSDT@kline_5m` stream, waiting for consecutive color sequence formations before placing a Limit Order reversal:
```bash
python bot.py
```

### Starting the Copy Trade Mode
Monitors a top trader's address directly on the Blockchain. Whenever the target wallet buys or sells *BTC Up/Down* shares, your bot copies the action using your personal `BASE_TRADE_SIZE_USDC`.
```bash
python bot.py --copy-trade 0xTARGET_WALLET_ADDRESS_HERE
```

---

## 🛠️ Auto-Management Capabilities
- **Clock Drift Mitigation**: Ensures perfect milisecond-alignment by keeping your OS time firmly linked to Binance and Polymarket server epochs.
- **Micro-Slippage Safety**: Rejects unpredictable token surges via strictly controlled FOK (Fill-Or-Kill) transactions. If the pool becomes volatile mid-execution, your capital is preserved.
- **Background Keepalive**: Prevents timeout disconnection gracefully via an active Web3 Heartbeat algorithm.

*Maintain your workstation/vps running uninterrupted for optimal WebSockets latency. High profitability and safe trading!* 💸
