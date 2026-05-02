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

# Ensure we use the local py-clob-client-v2-main if it exists
local_sdk_path = os.path.join(os.path.dirname(__file__), "py-clob-client-v2-main")
if os.path.exists(local_sdk_path):
    sys.path.insert(0, local_sdk_path)
    print(f"INFO: Using local SDK from: {local_sdk_path}")

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
    cfg       = MARKETS[market_key]
    slug      = cfg["slug"]
    series_id = cfg["series_id"]
    ws_url    = cfg["binance_ws"]
    symbol    = cfg["binance_symbol"]

    mlog = MarketLogger(logger, market_key)
    mlog.info(f"Fetching active tokens for {slug}...")
    token_ids = poly.fetch_market_tokens(series_id=series_id)
    if not token_ids:
        mlog.error(f"Market '{slug}' not found or without active tokens.")
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
    return strategy, stream

# ------------------------------------------------------------------ #
# Main                                                                 #
# ------------------------------------------------------------------ #

def main():
    parser = argparse.ArgumentParser(description="Polymarket Up/Down 5M bot")
    parser.add_argument("--copy-trade", type=str, help="Wallet address to monitor")
    parser.add_argument("--sniper", action="store_true", help="Enable Whale Sniper mode")
    parser.add_argument("--virtual", type=float, nargs='?', const=1000.0, help="Enable Virtual Mode")
    parser.add_argument("--rescue", action="store_true", help="Emergency mode: Redemptions and Cancellations")
    args = parser.parse_args()

    logger.header()
    active_markets = get_active_markets()
    
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ---- Time Sync ----
    sync_with_binance()

    # ---- Shared Polymarket Client ----
    logger.info("Connecting to Polymarket CLOB...")
    try:
        if args.virtual is not None:
            from virtual_client import VirtualPolymarketClient
            poly = VirtualPolymarketClient(logger=logger, initial_balance=args.virtual)
        else:
            poly = PolymarketClient(logger=logger)

        poly.check_allowance_and_approve()
        
        if args.rescue:
            logger.warn("🚨 [RESCUE MODE] Initializing emergency cleanup...")
            poly.reconstruct_queue_from_history()
            
            logger.info("Checking local queue for unclaimed winnings...")
            # We must iterate over a copy of the keys to avoid modification errors
            for cid in list(poly._redeem_queue.keys()):
                poly.redeem_shares(cid)
            
            poly.rescue_open_orders()
            logger.success("Rescue operations completed. You can now restart the bot in normal mode.")
            sys.exit(0)

        poly.start_background_cleanup()

    except Exception as e:
        logger.error(f"Failed to initialize Polymarket client: {e}")
        sys.exit(1)

    # Launch markets
    market_instances = []
    for market_key in active_markets:
        strategy, stream = _launch_market(market_key, poly)
        if strategy and stream:
            market_instances.append((market_key, strategy, stream))

    try:
        while not _shutdown_event.is_set():
            _shutdown_event.wait(timeout=1.0)
    finally:
        for mk, st, sm in market_instances: sm.stop()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    main()