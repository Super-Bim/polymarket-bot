# =============================================================
# bot.py — Main Entry Point
# =============================================================

import os
import sys
import time
import signal
import threading
from dotenv import load_dotenv

# Load .env first
load_dotenv()

from config import BASE_TRADE_SIZE_USDC, MARKET_SLUG, SEQUENCE_LENGTH
from logger import BotLogger, BOLD, RESET, YELLOW, GREEN
from polymarket_client import PolymarketClient
from binance_stream import BinanceStream
from strategy import Strategy


# ------------------------------------------------------------------ #
# Shutdown handler                                                     #
# ------------------------------------------------------------------ #

_shutdown_event = threading.Event()


def _handle_signal(sig, frame):
    print("\n")
    logger.warn("Interrupt received. Shutting down bot...")
    _shutdown_event.set()


# ------------------------------------------------------------------ #
# Initialization                                                       #
# ------------------------------------------------------------------ #

logger = BotLogger()


def main():
    logger.header()
    logger.info("Starting Polymarket BTC Up/Down 5M bot...")

    # Register signal handlers
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ---- Polymarket ----
    logger.info("Connecting to Polymarket CLOB...")
    try:
        poly = PolymarketClient(logger=logger)
        
        # 1. Verify and Approve USDC.e Allowance
        poly.check_allowance_and_approve()
        
        # 2. Get balances (No longer logging)
        poly.get_balances()
            
        # 3. Start background cleanup (Previous winnings)
        poly.start_background_cleanup()

    except ValueError as e:
        logger.error(str(e))
        logger.error("Configure the .env file before running. See .env.example")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to initialize Polymarket client: {e}")
        sys.exit(1)

    # ---- Fetch Initial Tokens (Immediate start) ----
    logger.info(f"Fetching active tokens for {MARKET_SLUG} market...")
    token_ids = poly.fetch_market_tokens()
    if not token_ids:
        logger.error(f"Market '{MARKET_SLUG}' not found or without active tokens.")
        logger.error("Check if market exists: https://polymarket.com/event/btc-updown-5m")
        sys.exit(1)

    logger.startup_info(token_ids, BASE_TRADE_SIZE_USDC)

    # ---- Strategy ----
    strategy = Strategy(poly_client=poly, logger=logger)
    strategy.token_ids = token_ids

    # ---- Binance Stream ----
    logger.info("Connecting to Binance stream BTCUSDT@kline_5m...")
    stream = BinanceStream(
        on_sequence    = strategy.on_sequence_detected,
        on_candle_tick = strategy.on_candle_tick,
        on_candle_close= strategy.on_candle_close,
        logger         = logger,
    )
    stream.start()

    logger.info(f"Bot active. Waiting for sequence of {SEQUENCE_LENGTH} candles...")
    logger.separator()
    print()

    # ---- Main Loop ----
    try:
        while not _shutdown_event.is_set():
            _shutdown_event.wait(timeout=1.0)
    finally:
        _shutdown_print(stream, strategy, poly)


def _shutdown_print(stream, strategy, poly):
    stream.stop()
    logger.separator()
    logger.info(f"Final state: {strategy.state.value}")

    # Show open positions (if any)
    if strategy.trade_1 and strategy.state.value in ("IN_TRADE_1", "MARTINGALE_WAIT", "IN_MARTINGALE"):
        t = strategy.trade_1
        logger.warn(
            f"Open position — {t.side}  "
            f"Entry price: {t.entry_price:.3f}  "
            f"Size: ${t.size_usdc:.2f}  "
            f"Shares: {t.shares:.2f}"
        )
        logger.warn("Check manually at polymarket.com/profile")

    if strategy.gales and strategy.state.value == "IN_GALE":
        t = strategy.gales[-1]
        logger.warn(
            f"Open Martingale — {t.side}  "
            f"Entry price: {t.entry_price:.3f}  "
            f"Size: ${t.size_usdc:.2f}"
        )

    logger.info("Bot stopped.")


if __name__ == "__main__":
    main()
