# =============================================================
# config.py — Bot Configuration
# =============================================================

# --- Polymarket ---
CLOB_HOST       = "https://clob.polymarket.com"
GAMMA_API       = "https://gamma-api.polymarket.com"
CHAIN_ID        = 137          # Polygon mainnet
MARKET_SLUG     = "btc-updown-5m"
MARKET_SERIES_ID= "10684"      # Series ID 

# --- Binance ---
BINANCE_WS_URL  = "wss://stream.binance.com:9443/ws/btcusdt@kline_5m"
SYMBOL          = "BTCUSDT"
CANDLE_INTERVAL = "5m"

# --- Strategy ---
SEQUENCE_LENGTH          = 3    # No. of consecutive candles to detect a trend
ENTRY_PRICE_THRESHOLD    = 0.46  # Max opposite option price to enter
GALE_1_PRICE_THRESHOLD   = 0.48  # Max price for Gale 1 specifically
GALE_2_PLUS_PRICE_THRESHOLD = 0.55 # Max price for Gale 2+
ENTRY_WINDOW_SECONDS     = 120   # 1st order entry window (seconds)
MARTINGALE_WINDOW_SECONDS= 180   # Total window for martingale (seconds)
MARTINGALE_MULTIPLIER    = 2.2   # Each gale = multiplier x previous gale
MAX_GALES                = 4     # Max gales per sequence

# --- Sizing ---
BASE_TRADE_SIZE_USDC     = 5.0   # Base trade size ($5 minimum)

# --- Polling ---
PRICE_POLL_INTERVAL      = 2     # Seconds between price checks
MARKET_REFRESH_INTERVAL  = 60    # Tokens change every 5min — refresh often
