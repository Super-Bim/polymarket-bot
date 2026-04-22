# =============================================================
# config.py — Bot Configuration
# =============================================================

# --- Polymarket ---
CLOB_HOST       = "https://clob.polymarket.com"
GAMMA_API       = "https://gamma-api.polymarket.com"
CHAIN_ID        = 137          # Polygon mainnet

# =============================================================
# MULTI-MARKET CONFIGURATION
# =============================================================
# Define which markets the bot will operate on.
# Set ACTIVE_MARKETS to a list of market keys you want to trade.
# Use "ALL" to enable all markets simultaneously.
#
# Available markets (with confirmed active Polymarket series):
#   "BTC"   — series 10684  (btc-updown-5m)
#   "ETH"   — series 10683  (eth-updown-5m)
#   "XRP"   — series 10685  (xrp-updown-5m)
#   "SOL"   — series 10686  (sol-updown-5m)
#   "BNB"   — series 11326  (bnb-up-or-down-5m)   ← confirmed active 2026-04
#   "DOGE"  — series 11325  (doge-up-or-down-5m)  ← confirmed active 2026-04
#
# Examples:
#   ACTIVE_MARKETS = ["BTC"]               # Only BTC
#   ACTIVE_MARKETS = ["BTC", "ETH"]        # BTC and ETH
#   ACTIVE_MARKETS = "ALL"                 # All confirmed markets
# =============================================================
ACTIVE_MARKETS = "ALL"   # <-- EDIT THIS to choose your markets

# Market definitions: slug, series_id (confirmed via Polymarket API), Binance symbol & stream
MARKETS = {
    "BTC": {
        "slug":               "btc-updown-5m",
        "series_id":          "10684",          # confirmed active
        "binance_symbol":     "BTCUSDT",
        "binance_ws":         "wss://stream.binance.com:9443/ws/btcusdt@kline_5m",
        "sniper_min_usdt":    350_000,          # High liquidity requires heavy volume
        "sniper_dom_ratio":   0.70,             # 70% dominance
        "sniper_impact":      0.0001,           # 0.01% price impact
    },
    "ETH": {
        "slug":               "eth-updown-5m",
        "series_id":          "10683",          # confirmed active
        "binance_symbol":     "ETHUSDT",
        "binance_ws":         "wss://stream.binance.com:9443/ws/ethusdt@kline_5m",
        "sniper_min_usdt":    150_000,
        "sniper_dom_ratio":   0.75,
        "sniper_impact":      0.0001,
    },
    "XRP": {
        "slug":               "xrp-updown-5m",
        "series_id":          "10685",          # confirmed active
        "binance_symbol":     "XRPUSDT",
        "binance_ws":         "wss://stream.binance.com:9443/ws/xrpusdt@kline_5m",
        "sniper_min_usdt":    100_000,
        "sniper_dom_ratio":   0.75,
        "sniper_impact":      0.0002,           # Needs slightly more impact due to orderbook structure
    },
    "SOL": {
        "slug":               "sol-updown-5m",
        "series_id":          "10686",          # confirmed active
        "binance_symbol":     "SOLUSDT",
        "binance_ws":         "wss://stream.binance.com:9443/ws/solusdt@kline_5m",
        "sniper_min_usdt":    100_000,
        "sniper_dom_ratio":   0.75,
        "sniper_impact":      0.0002,
    },
    "BNB": {
        "slug":               "bnb-up-or-down-5m",
        "series_id":          "11326",          # confirmed active 2026-04
        "binance_symbol":     "BNBUSDT",
        "binance_ws":         "wss://stream.binance.com:9443/ws/bnbusdt@kline_5m",
        "sniper_min_usdt":    80_000,
        "sniper_dom_ratio":   0.75,
        "sniper_impact":      0.0002,
    },
    "DOGE": {
        "slug":               "doge-up-or-down-5m",
        "series_id":          "11325",          # confirmed active 2026-04
        "binance_symbol":     "DOGEUSDT",
        "binance_ws":         "wss://stream.binance.com:9443/ws/dogeusdt@kline_5m",
        "sniper_min_usdt":    60_000,           # DOGE needs less capital to manipulate
        "sniper_dom_ratio":   0.80,             # But more dominance (lots of retail noise)
        "sniper_impact":      0.0003,           # Larger tick required
    },
}

# --- Resolve active market list ---
def get_active_markets() -> list:
    if ACTIVE_MARKETS == "ALL":
        return list(MARKETS.keys())
    return [m.upper() for m in ACTIVE_MARKETS if m.upper() in MARKETS]


# --- Strategy ---
SEQUENCE_LENGTH          = 2       # No. of consecutive candles to detect a trend
ENTRY_PRICE_THRESHOLD    = 0.48    # Max opposite option price to enter
GALE_1_PRICE_THRESHOLD   = 0.6    # Max price for Gale 1 specifically
GALE_2_PLUS_PRICE_THRESHOLD = 0.6 # Max price for Gale 2+
ENTRY_WINDOW_SECONDS     = 120      # 1st order entry window (seconds)
MARTINGALE_WINDOW_SECONDS= 180     # Total window for martingale (seconds)
MARTINGALE_MULTIPLIER    = 2.5    # Each gale = multiplier x previous gale
MAX_GALES                = 20      # Max gales per sequence
PROFIT_TARGET_PERCENT    = 150    # Overall profit in % for early cash out (both strategies)

# --- Idle Mode (Post-Martingale) ---
# When enabled, the bot waits for the current strong trend to break 
# after a max-gale loss before starting new trades.
IDLE_AFTER_GALE_LIMIT    = True    # Wait for trend reset after a full martingale loss?

# --- Risk Management ---
STOP_LOSS_PERCENT        = 40    # Stop loss per position
INDECISION_EXIT_WINDOW_S = 5    # Seconds before close to exit if price is indecisive
INDECISION_PRICE_RANGE   = (0.47, 0.54) # Price range considered "indecisive"

# --- Sizing ---
BASE_TRADE_SIZE_USDC     = 1     # Base trade size ($1 minimum)

# --- Polling ---
PRICE_POLL_INTERVAL      = 1       # Seconds between price checks
MARKET_REFRESH_INTERVAL  = 60      # Tokens change every 5min — refresh often

# --- Copy Trade ---
COPY_TRADE_POLL_INTERVAL = 1.5     # Seconds between target wallet balance checks

# =============================================================
# SNIPER MODE CONFIGURATION
# =============================================================
# Activated via:  python bot.py --sniper
# Monitors Binance aggTrade stream for whale accumulation in the
# last 30–5 seconds of each candle and enters Polymarket
# immediately. Runs INDEPENDENTLY — no martingale, no profit target.
#
# SNIPER_WHALE_MIN_USDT: minimum cumulative executed volume (USDT)
#   on the counter-market side to classify as whale activity.
#   This is measured in USDT because Binance pairs are XYZ/USDT.
#     BTC  → 200_000 – 500_000
#     ETH  → 100_000 – 200_000
#     SOL/XRP/BNB/DOGE → 30_000 – 80_000
#   The values below are GLOBAL DEFAULTS; per-market overrides are in the MARKETS dict.
# =============================================================
SNIPER_WHALE_MIN_USDT        = 100_000   # Default min volume (USDT)
SNIPER_WINDOW_START_SECONDS  = 40
SNIPER_WINDOW_END_SECONDS    = 3
SNIPER_TRADE_SIZE_USDC       = 1.0
SNIPER_MAX_PRICE             = 0.40
SNIPER_DOMINANCE_RATIO       = 0.75      # Default dominance req.
SNIPER_MIN_PRICE_IMPACT      = 0.0001    # Default min price movement
