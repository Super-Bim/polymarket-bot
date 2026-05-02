# =============================================================
# logger.py — Clean and organized terminal output
# =============================================================

import sys
from datetime import datetime
from time_utils import synced_time

# ANSI color codes
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[92m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
BLUE    = "\033[94m"
CYAN    = "\033[96m"
MAGENTA = "\033[95m"
WHITE   = "\033[97m"

# Box chars
LINE    = "-" * 60
DLINE   = "=" * 60


def _ts() -> str:
    return datetime.fromtimestamp(synced_time()).strftime("%H:%M:%S")


def _tag(label: str, color: str) -> str:
    return f"{color}{BOLD}[{label}]{RESET}"


def _mkt_tag(market_key: str) -> str:
    """Returns a colored [MKT] tag, or empty string if no market_key given."""
    if not market_key:
        return ""
    return f"{CYAN}{BOLD}[{market_key}]{RESET}"


class BotLogger:
    """Compact and visual terminal logger."""

    def header(self):
        print(f"\n{CYAN}{BOLD}{DLINE}{RESET}")
        print(f"{CYAN}{BOLD}  >  POLYMASTER-BOT UP/DOWN 5M BOT{RESET}")
        print(f"{CYAN}{BOLD}{DLINE}{RESET}\n")

    def info(self, msg: str):
        print(f"{DIM}{_ts()}{RESET} (i)  {msg}")
    def warn(self, msg: str):
        print(f"{DIM}{_ts()}{RESET} (!)  {YELLOW}{msg}{RESET}")
    def error(self, msg: str):
        print(f"{DIM}{_ts()}{RESET} (x)  {RED}{msg}{RESET}")
    def success(self, msg: str):
        print(f"{DIM}{_ts()}{RESET} (v)  {GREEN}{msg}{RESET}")

    def debug(self, msg: str):
        pass

    def separator(self):
        print(f"{DIM}{LINE}{RESET}")

    # -- Market Events --

    def candle_close(self, candle, market_key: str = ""):
        symbol = "UP" if candle.direction == "UP" else "DN"
        color  = GREEN if candle.direction == "UP" else RED
        mkt    = f" {_mkt_tag(market_key)}" if market_key else ""
        print(
            f"{DIM}{_ts()}{RESET}"
            f"{mkt} "
            f"{color}{BOLD}{symbol} CANDLE{RESET} "
            f"O:{candle.open:.1f}  C:{candle.close:.1f}  "
            f"Dir:{color}{candle.direction}{RESET}"
        )

    def sequence_detected(self, direction: str, candles, market_ticker: str = "", market_key: str = ""):
        from config import SEQUENCE_LENGTH
        color    = GREEN if direction == "UP" else RED
        arrow    = (">" if direction == "UP" else "<") * SEQUENCE_LENGTH
        opposite = "DOWN" if direction == "UP" else "UP"

        mkt_tag     = f"  {_mkt_tag(market_key)}" if market_key else ""
        market_info = f"  Market: {BOLD}{market_ticker}{RESET}" if market_ticker else ""

        print()
        print(f"{DIM}{LINE}{RESET}")
        print(
            f"{_ts()} {color}{BOLD}SEQUENCE DETECTED  {arrow}{RESET}{mkt_tag}  "
            f"Trend: {color}{BOLD}{direction}{RESET}  "
            f"Target: {CYAN}{BOLD}{opposite}{RESET}{market_info}"
        )
        print(f"{DIM}{LINE}{RESET}")

    def price_check(self, side: str, price: float, elapsed: float, window: float,
                    is_martingale: bool = False, bid: float = 0, market_key: str = ""):
        label   = "MARTINGALE" if is_martingale else "ENTRY     "
        color   = MAGENTA if is_martingale else CYAN
        ok      = price <= 0.40
        pmark   = f"{GREEN}v BELOW LIMIT{RESET}" if ok else f"{YELLOW}above limit{RESET}"
        bar     = _progress_bar(elapsed, window)
        mkt     = f"{_mkt_tag(market_key)} " if market_key else ""
        bid_str = f" (Bid: {bid:.3f})" if bid > 0 else ""
        print(
            f"{DIM}{_ts()}{RESET} {mkt}{color}[{label}]{RESET} "
            f"Price {side}: {BOLD}{price:.3f}{RESET}{bid_str}  {pmark}  "
            f"Time: {bar} {elapsed:.0f}s/{window:.0f}s"
        )

    def order_placed(self, trade, is_martingale: bool = False, market_key: str = ""):
        label = "MARTINGALE" if is_martingale else "1st ORDER"
        color = MAGENTA if is_martingale else GREEN
        mkt   = f" {_mkt_tag(market_key)}" if market_key else ""
        profit_info = ""
        if trade.entry_price > 0:
            roi_pct = ((1.0 / trade.entry_price) - 1.0) * 100
            profit_info = f"  Target ROI: {BOLD}{roi_pct:.0f}%{RESET}"

        print()
        print(f"{DIM}{LINE}{RESET}")
        print(f"{_ts()}  {color}{BOLD}> ORDER PLACED -- {label}{mkt}{RESET}")
        print(
            f"          Side    : {BOLD}{trade.side}{RESET}\n"
            f"          Price   : {BOLD}{trade.entry_price:.3f}{RESET}\n"
            f"          Size    : {BOLD}${trade.size_usdc:.2f} USDC{RESET}  "
            f"({trade.shares:.2f} shares){profit_info}"
        )
        if trade.order_id:
            print(f"          Order ID: {DIM}{trade.order_id[:20]}...{RESET}")
        print(f"{DIM}{LINE}{RESET}\n")

    def order_failed(self, resp: dict, market_key: str = ""):
        mkt = f"{_mkt_tag(market_key)} " if market_key else ""
        self.error(f"{mkt}Order rejected: {resp.get('errorMsg', resp)}")

    def win_signal(self, trade, candle, market_key: str = ""):
        color = GREEN
        mkt   = f" {_mkt_tag(market_key)}" if market_key else ""
        print()
        print(f"{color}{BOLD}{DLINE}{RESET}")
        print(
            f"{_ts()}  {color}{BOLD}v CANDLE CLOSED IN FAVOR{mkt}  "
            f"({candle.direction}){RESET}"
        )
        print(f"{color}{BOLD}{DLINE}{RESET}\n")

    def loss_signal(self, msg: str, market_key: str = ""):
        mkt = f"{_mkt_tag(market_key)} " if market_key else ""
        print()
        print(f"{RED}{BOLD}  x {mkt}{msg}{RESET}\n")

    def timeout(self, msg: str, market_key: str = ""):
        mkt = f"{_mkt_tag(market_key)} " if market_key else ""
        print(f"{DIM}{_ts()}{RESET} [T] {mkt}{msg}{RESET}")

    def state_change(self, new_state: str, market_key: str = ""):
        mkt = f"{_mkt_tag(market_key)} " if market_key else ""
        print(f"{DIM}{_ts()}  {mkt}State -> {BOLD}{new_state}{RESET}")

    def startup_info(self, token_ids: dict, trade_size: float, market_slug: str = ""):
        slug = market_slug or "updown-5m"
        self.separator()
        print(
            f"  Market    : {slug}\n"
            f"  UP token  : {DIM}{token_ids.get('UP', 'N/A')[:20]}...{RESET}\n"
            f"  DOWN token: {DIM}{token_ids.get('DOWN', 'N/A')[:20]}...{RESET}\n"
            f"  Size      : ${trade_size:.2f} USDC per entry"
        )
        self.separator()
        print()


# =============================================================
# MarketLogger — per-market wrapper around BotLogger
# =============================================================

class MarketLogger:
    """
    Thin wrapper around BotLogger that automatically injects the market
    key prefix into every log method. Instantiate one per active market.

    Usage:
        mlog = MarketLogger(logger, "BTC")
        mlog.info("hello")       → "20:00:00 ℹ  [BTC] hello"
        mlog.order_placed(trade) → shows [BTC] in the ORDER PLACED block
    """

    def __init__(self, base_logger: BotLogger, market_key: str):
        self._log = base_logger
        self.market_key = market_key.upper()

    # ------------------------------------------------------------------ #
    # Simple text messages — prepend [MKT] to the message string          #
    # ------------------------------------------------------------------ #

    def info(self, msg: str):
        self._log.info(f"[{self.market_key}] {msg}")

    def warn(self, msg: str):
        self._log.warn(f"[{self.market_key}] {msg}")

    def error(self, msg: str):
        self._log.error(f"[{self.market_key}] {msg}")

    def success(self, msg: str):
        self._log.success(f"[{self.market_key}] {msg}")

    def debug(self, msg: str):
        self._log.debug(f"[{self.market_key}] {msg}")

    # ------------------------------------------------------------------ #
    # Specialized formatted methods — inject market_key into BotLogger    #
    # ------------------------------------------------------------------ #

    def candle_close(self, candle):
        self._log.candle_close(candle, market_key=self.market_key)

    def price_check(self, *args, **kwargs):
        kwargs.setdefault("market_key", self.market_key)
        self._log.price_check(*args, **kwargs)

    def order_placed(self, *args, **kwargs):
        kwargs.setdefault("market_key", self.market_key)
        self._log.order_placed(*args, **kwargs)

    def order_failed(self, resp: dict):
        self._log.order_failed(resp, market_key=self.market_key)

    def win_signal(self, *args, **kwargs):
        kwargs.setdefault("market_key", self.market_key)
        self._log.win_signal(*args, **kwargs)

    def loss_signal(self, msg: str):
        self._log.loss_signal(msg, market_key=self.market_key)

    def timeout(self, msg: str):
        self._log.timeout(msg, market_key=self.market_key)

    def state_change(self, new_state: str):
        self._log.state_change(new_state, market_key=self.market_key)

    def sequence_detected(self, *args, **kwargs):
        kwargs.setdefault("market_key", self.market_key)
        self._log.sequence_detected(*args, **kwargs)

    # ------------------------------------------------------------------ #
    # Global passthrough (no per-market prefix needed)                    #
    # ------------------------------------------------------------------ #

    def header(self):
        self._log.header()

    def separator(self):
        self._log.separator()

    def startup_info(self, *args, **kwargs):
        self._log.startup_info(*args, **kwargs)


def _progress_bar(elapsed: float, total: float, width: int = 10) -> str:
    ratio  = min(elapsed / total, 1.0)
    filled = int(ratio * width)
    bar    = "#" * filled + "-" * (width - filled)
    return f"[{bar}]"
