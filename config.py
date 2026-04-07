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
SEQUENCE_LENGTH          = 3       # No. of consecutive candles to detect a trend
ENTRY_PRICE_THRESHOLD    = 0.48    # Max opposite option price to enter
GALE_1_PRICE_THRESHOLD   = 0.54    # Max price for Gale 1 specifically
GALE_2_PLUS_PRICE_THRESHOLD = 0.61 # Max price for Gale 2+
ENTRY_WINDOW_SECONDS     = 90      # 1st order entry window (seconds)
MARTINGALE_WINDOW_SECONDS= 180     # Total window for martingale (seconds)
MARTINGALE_MULTIPLIER    = 2.75    # Each gale = multiplier x previous gale
MAX_GALES                = 10      # Max gales per sequence
PROFIT_TARGET_PERCENT    = 0    # Overall profit in % for early cash out (both strategies)

# --- Sizing ---
BASE_TRADE_SIZE_USDC     = 1.0     # Base trade size ($1 minimum)

# --- Polling ---
PRICE_POLL_INTERVAL      = 1       # Seconds between price checks
MARKET_REFRESH_INTERVAL  = 60      # Tokens change every 5min — refresh often

# --- Copy Trade ---
COPY_TRADE_POLL_INTERVAL = 1.5     # Seconds between target wallet balance checks

