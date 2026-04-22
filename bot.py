# =============================================================
# bot.py — Main Entry Point (Multi-Market)
# =============================================================

import os
import sys
import time
import signal
import threading
import argparse
from dotenv import load_dotenv

# Load .env first
load_dotenv()

from config import (
    BASE_TRADE_SIZE_USDC, SEQUENCE_LENGTH,
    MARKETS, get_active_markets,
)
from logger import BotLogger, MarketLogger, BOLD, RESET, YELLOW, GREEN, CYAN
from polymarket_client import PolymarketClient
from binance_stream import BinanceStream
from strategy import Strategy
from copy_trader import CopyTrader
from time_utils import sync_with_binance, get_offset


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


# ------------------------------------------------------------------ #
# Per-market launcher                                                  #
# ------------------------------------------------------------------ #

def _launch_market(market_key: str, poly: PolymarketClient) -> tuple:
    """
    Initializes and starts a (Strategy, BinanceStream) pair for one market.
    Returns (strategy, stream) or (None, None) on fatal error.
    """
    cfg       = MARKETS[market_key]
    slug      = cfg["slug"]
    series_id = cfg["series_id"]
    ws_url    = cfg["binance_ws"]
    symbol    = cfg["binance_symbol"]

    # Per-market logger: auto-prefixes every log call with [MARKET_KEY]
    mlog = MarketLogger(logger, market_key)

    mlog.info(f"Fetching active tokens for {slug}...")
    token_ids = poly.fetch_market_tokens(series_id=series_id)
    if not token_ids:
        mlog.error(f"Market '{slug}' not found or without active tokens.")
        mlog.error(f"Check: https://polymarket.com/event/{slug}")
        return None, None

    logger.startup_info(token_ids, BASE_TRADE_SIZE_USDC, market_slug=slug)

    strategy = Strategy(poly_client=poly, logger=mlog, market_key=market_key)
    strategy.token_ids = token_ids

    stream = BinanceStream(
        on_sequence     = strategy.on_sequence_detected,
        on_candle_tick  = strategy.on_candle_tick,
        on_candle_close = strategy.on_candle_close,
        logger          = mlog,
        ws_url          = ws_url,
        symbol          = symbol,
    )
    stream.start()

    mlog.info(f"Active. Waiting for sequence of {SEQUENCE_LENGTH} candles...")
    return strategy, stream


# ------------------------------------------------------------------ #
# Main                                                                 #
# ------------------------------------------------------------------ #

def main():
    parser = argparse.ArgumentParser(description="Polymarket Up/Down 5M bot (multi-market)")
    parser.add_argument("--copy-trade", type=str, help="Wallet address to monitor and copy trades")
    parser.add_argument(
        "--sniper",
        action="store_true",
        help="Enable Whale Sniper mode — monitors Binance aggTrade for large counter-market "
             "volume near candle close and enters Polymarket immediately. "
             "Runs independently (no martingale / no profit target).",
    )
    parser.add_argument(
        "--virtual",
        type=float,
        nargs='?',
        const=1000.0,
        help="Enable Virtual Mode with an optional initial balance (default: 1000.0). "
             "Simulates trades without spending real USDC and generates a dashboard.",
    )
    args = parser.parse_args()

    # Mutual exclusion guard
    if args.copy_trade and args.sniper:
        print("[ERROR] --copy-trade and --sniper cannot be used together.")
        sys.exit(1)

    logger.header()
    active_markets = get_active_markets()
    mode = "COPY TRADE" if args.copy_trade else "NORMAL"
    logger.info(f"Starting bot... [Mode: {mode}] [Markets: {', '.join(active_markets)}]")

    # Register signal handlers
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ---- Time Sync (Binance) ----
    offset = sync_with_binance()
    if offset != 0.0:
        logger.info(f"Binance time offset: {offset:+.3f}s (Clock drift detected)")
    else:
        logger.info("Binance time sync complete.")

    # ---- Shared Polymarket Client ----
    logger.info("Connecting to Polymarket CLOB...")
    try:
        if args.virtual is not None:
            from virtual_client import VirtualPolymarketClient
            poly = VirtualPolymarketClient(logger=logger, initial_balance=args.virtual)
        else:
            poly = PolymarketClient(logger=logger)

        # 1. Verify and Approve USDC.e Allowance
        poly.check_allowance_and_approve()

        # 2. Get balances
        poly.get_balances()

        # 3. Start background cleanup (Previous winnings)
        poly.start_background_cleanup()

        # 4. Cancel orphaned open orders
        open_orders = poly.get_open_orders()
        if open_orders:
            logger.warn(f"Found {len(open_orders)} open orders from previous execution. Canceling them...")
            poly.cancel_all_orders()
            time.sleep(1)

    except ValueError as e:
        logger.error(str(e))
        logger.error("Configure the .env file before running. See .env.example")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to initialize Polymarket client: {e}")
        sys.exit(1)

    # ---- Copy Trade Mode ----
    if args.copy_trade:
        # Copy trade uses the first active market's tokens by default
        first_key  = active_markets[0]
        series_id  = MARKETS[first_key]["series_id"]
        slug       = MARKETS[first_key]["slug"]

        logger.info(f"COPY TRADE Mode active. Monitoring wallet: {args.copy_trade}")
        logger.info(f"Using market: {slug}")

        token_ids = poly.fetch_market_tokens(series_id=series_id)
        if not token_ids:
            logger.error(f"Market '{slug}' not found or without active tokens.")
            sys.exit(1)

        copy_trader = CopyTrader(target_wallet=args.copy_trade, poly_client=poly, logger=logger)
        copy_trader.token_ids = token_ids
        copy_trader.start()

        logger.separator()
        print()

        try:
            while not _shutdown_event.is_set():
                _shutdown_event.wait(timeout=1.0)
        finally:
            copy_trader.stop()
            logger.separator()
            if copy_trader.current_trade:
                t = copy_trader.current_trade
                logger.warn(
                    f"Open copy trade position — {t.side}  "
                    f"Entry price: {t.entry_price:.3f}  "
                    f"Size: ${t.size_usdc:.2f}"
                )
                logger.warn("Check manually at polymarket.com/profile")
            logger.info("Bot stopped.")
        return

    # ---- Sniper Mode ----
    if args.sniper:
        from sniper import Sniper
        logger.info(f"Starting SNIPER mode on markets: {', '.join(active_markets)}")

        sniper = Sniper(poly_client=poly, logger=logger, active_markets=active_markets)
        sniper.start()

        logger.separator()
        print()

        try:
            while not _shutdown_event.is_set():
                _shutdown_event.wait(timeout=1.0)
        finally:
            sniper.stop()
            logger.separator()
            logger.info("Sniper stopped.")
        return

    # ---- Normal Multi-Market Mode ----
    if not active_markets:
        logger.error("No active markets configured. Check ACTIVE_MARKETS in config.py.")
        sys.exit(1)

    market_instances: list[tuple[str, Strategy, BinanceStream]] = []

    for market_key in active_markets:
        strategy, stream = _launch_market(market_key, poly)
        if strategy and stream:
            market_instances.append((market_key, strategy, stream))
        else:
            logger.warn(f"[{market_key}] Skipping — failed to initialize.")

    if not market_instances:
        logger.error("All markets failed to initialize. Aborting.")
        sys.exit(1)

    logger.separator()
    print()

    # ---- Main Loop ----
    try:
        while not _shutdown_event.is_set():
            _shutdown_event.wait(timeout=1.0)
    finally:
        _shutdown_print(market_instances)


def _shutdown_print(market_instances: list):
    logger.separator()
    logger.info("Shutting down all market streams...")

    for market_key, strategy, stream in market_instances:
        stream.stop()
        logger.info(f"[{market_key}] Final state: {strategy.state.value}")

        # Show open trade_1 position
        if strategy.trade_1 and strategy.state.value in (
            "IN_TRADE_1", "MARTINGALE_WAIT", "IN_GALE"
        ):
            t = strategy.trade_1
            logger.warn(
                f"[{market_key}] Open position — {t.side}  "
                f"Entry price: {t.entry_price:.3f}  "
                f"Size: ${t.size_usdc:.2f}  "
                f"Shares: {t.shares:.2f}"
            )
            logger.warn("Check manually at polymarket.com/profile")

        # Show open gale position
        if strategy.gales and strategy.state.value == "IN_GALE":
            t = strategy.gales[-1]
            logger.warn(
                f"[{market_key}] Open Martingale — {t.side}  "
                f"Entry price: {t.entry_price:.3f}  "
                f"Size: ${t.size_usdc:.2f}"
            )

    logger.info("Bot stopped.")


if __name__ == "__main__":
    main()