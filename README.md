# polymarket-bot
A bot focused on automating binary options (UP/DOWN options) on the **Polymarket** platform. The bot uses the Binance Oracle via WebSocket in real time for millimeter-precise decision-making and executes orders directly in the Polymarket Clob (Order Book) using Smart Contracts.
## 🚀 Main Features

- **Real-Time Binance Oracle:** Connects directly to Binance WebSockets to extract directional signals in the exact millisecond the 5-minute (5m) candle closes.
- **Pattern Detection:** Reads the market flow and identifies reversal triggers based on the premise of force exhaustion (e.g. 3 consecutive candles closing in the same color generates a trigger for the opposite color).
- **Hold-to-Maturity Execution:** After a validated entry with price limits (instant Limit Order), the robot responsibly waits for the candle's expiration maturity to define the natural outcome without panicking over intra-candle fluctuations.
- **Autonomous Martingale Ecosystem:** If the candle resolves in a loss, the bot automatically feeds back, entering the next cycle window with a recovery injection (2x Multiplier) to secure base profit — limited to a safe ceiling of rounds (Gales) defined in the settings.
- **Anti-Ghost Resilience:** Internal sweep routines block the dreaded Polymarket Orderbook "Fakes" (stuck orders, lack of fluidity, L2 freezes). It requires cross-confirmation on the Blockchain.
- **Constant Order Sweeping:** Dispatches asynchronous sweeps on your proxy-wallet to cancel orphan operations from the past, automatically returning inactive margin back to your bankroll.

---

## 🛠 Prerequisites and Installation

1. **Python 3.10+**: The robot requires native language running on your server/machine. ([Download Python](https://www.python.org/downloads/))
2. **Dependency Installation**: Open the robot's folder in the terminal and install the required libraries contained in the `requirements.txt` file:
   ```bash
   pip install -r requirements.txt
   ```

---

## ⚙ Environment Configuration (.env)

The robot connects to your Polymarket Proxy Wallet confidentially. **DO NOT share the `.env` file with anyone.**

To configure:
1. Make a copy of the `.env.example` file and rename it to `.env`.
   - This can be done via terminal by running: `copy .env.example .env` (Windows) or `cp .env.example .env` (Linux/Mac).
2. Open the generated `.env` file and fill it with your credentials. The tags and comments inside the file will guide the correct filling of your `PRIVATE_KEY`, the address of your proxy wallet `FUNDER_ADDRESS`, the signature type `SIGNATURE_TYPE`, and the base trade size `TRADE_SIZE_USDC`.

*Tip:* All limits, initial bet sizes, rates, and Martingale targets can be easily customized inside the `config.py` file if you want to experiment with other approaches.

---

## ▶ How to Start

With the environment ready and your `.env` well configured, just run the orchestrator engine in the terminal:

```bash
python bot.py
```

The bot will instantly download the old Binance candles to avoid waiting too long, and present a clean and colorful log interface pointing the steps of candle monitoring, opening and freely reporting transactions.

---

## 📈 Profit Claim (Cash Out / Redeem)

**Attention:** Due to the L2 Gnosis Safe infrastructure and the Gasless necessity of the Safe itself (Proxy Wallet), the bot executes an aggressive commercial tactic and **validates profits**, however **Redeeming Matured Positions** must be periodically done manually directly in the "Portfolio / Redeem Positions" tab on your official Polymarket website dashboard so that the Balance returns safely as available USDC. The bot never burns your tickets without your manual order on the exchange.

---
*Keep your terminal free from power suspension so that the WebSocket oracle does not suffer from latency! Good profits!* 💸
