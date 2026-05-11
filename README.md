# 🤖 Polymaster Multi-Market Intelligent Bot

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Target-Polymarket%20CLOB%20V2-green.svg)](https://polymarket.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An advanced institutional-grade automated trading engine specialized for **Up/Down (5M) ** outcome markets. The system integrates real-time high-frequency Binance data, advanced technical analysis filtering, and enterprise reliability mechanisms to deliver execution edge across high-volume digital assets.

---

## 🔥 Key Features & Ecosystem

### 1. 🛡️ Multi-Factor Intelligent Filters (New)
Supercharge reversal detections with an enterprise math engine. The system acts as an integrated **Logical AND-Gate**, allowing execution ONLY when user-enabled technical indicators dynamically converge:
- **EMA (Exponential Moving Average)**: Validates structural long-term trends vs short-term momentum.
- **RSI (Relative Strength Index)**: Filters execution based on Overbought/Oversold thermal zones.
- **MACD (Moving Average Convergence Divergence)**: Ensures volume & histogram momentum aligns before firing.
- **Bollinger Bands**: Confirms volatility volatility/breakouts to refine entry density.
- **Fibonacci Retracement**: Uses historical lookback vectors to determine golden ratio breakout thresholds.
*Dynamic Logic: Indicators act exclusively as strict gatekeepers for cycle entries; Martingale recovery relies on pure price-action integrity.*

### 2. 🚀 Hybrid Architecture Modes
*   **Reversal Engine (Default)**: High-probability mean reversion scanning with recursive Martingale recovery logic.
*   **Whale Sniper Mode (`--sniper`)**: Latency-optimized accumulator monitoring Binance `aggTrade` pools for extreme volume concentration in terminal candle seconds.
*   **Synchronous Copy Trade (`--copy-trade`)**: Institutional-grade wallet propagation mirroring, executing matching exposures milliseconds after a targeted wallet signals.

### 3. 📦 Zero-Touch Automated Setup
Skip manual configuration hassles. The core module leverages a built-in **Setup Onboarding Assistant**:
- Detects missing credentials dynamically upon boot.
- Securely prompts for key ingestion directly via standard IO.
- Interactively writes optimized `.env` configuration structures and auto-derives corresponding layer-2 signatures without further intervention.

### 4. 📈 Premium Analytics Suite
The software continuously emits sub-second event telemetry to render professional, rich-client monitoring dashboard systems:
- **Real-time Equity Curve Visuals**: Visualize asset distribution and session alpha over time.
- **Deep Asset Decomposition**: Tracks and reports fine-grained metrics broken down by symbol (BTC, ETH, SOL, XRP, BNB, DOGE).
- **Auto-Sync Lifecycle**: Reflects instantaneous position closures, payout redemption, and martingale tree progression instantly.

---

## 🔧 Scalability & System Optimization

*   **Adaptive Resource Loading**: The system detects indicator utilization status. Enabling filters expands buffering to ~100 units instantly, preserving maximal throughput and bandwidth.
*   **Bidirectional Redundancy Exits**: Employs `Fill-And-Kill (FAK)` logic on liquidity extraction vectors, maximizing exit fulfillment density for critical `Stop Loss` and `Trailing TP` execution routes.
*   **Anti-Bypass Chronological Gating**: Rigid delta runtime calculations ensure strict enforcement of the 90s entry window, mathematically protecting trading logic against historical execution drifts during environment reboots.

---

## ⚙️ Deployment & Installation

### 1. System Requirements
Requires Python 3.10 or later. Deploy standard tooling packages via:
```bash
pip install -r requirements.txt
```

### 2. Zero-Config Boot (Recommended)
Simply execute the application. If credentials are not explicitly stored, the **Interactive Assistant** will securely structure the backend config automatically:
```bash
python bot.py
```

### 3. Manual Environment Specification (`.env`)
Alternatively, pre-populate the system manifest as follows:
```env
PRIVATE_KEY="0x..."
SIGNATURE_TYPE=0
```
*Note: Cryptographic L2 API structures are instantiated and propagated securely by the core handler on initial validation.*

---

## 🎮 Operation Instructions

| Objective | Protocol Interface |
| :--- | :--- |
| **Risk-Free Sandbox** | `python bot.py --virtual 1000` |
| **Production Execution** | `python bot.py` |
| **Liquidity Accumulator (Whale)** | `python bot.py --sniper` |
| **Mirror Distribution** | `python bot.py --copy-trade 0xWALLET` |
| **System Emergency Rescue** | `python bot.py --rescue` |

### Emergency Rescue Vectors (`--rescue`)
Failsafe routines injected to cleanse account surface exposure:
1. **Force Collateral Salvage**: Retrospectively evaluates account history to harvest lat