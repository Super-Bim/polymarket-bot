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
LINE    = "─" * 60
DLINE   = "═" * 60


def _ts() -> str:
    return datetime.fromtimestamp(synced_time()).strftime("%H:%M:%S")


def _tag(label: str, color: str) -> str:
    return f"{color}{BOLD}[{label}]{RESET}"


class BotLogger:
    """Compact and visual terminal logger."""

    def header(self):
        print(f"\n{CYAN}{BOLD}{DLINE}{RESET}")
        print(f"{CYAN}{BOLD}  ▶  POLYMASTER-BOT BTC UP/DOWN 5M BOT{RESET}")
        print(f"{CYAN}{BOLD}{DLINE}{RESET}\n")

    def info(self, msg: str):
        print(f"{DIM}{_ts()}{RESET} {BLUE}ℹ{RESET}  {msg}")

    def warn(self, msg: str):
        print(f"{DIM}{_ts()}{RESET} {YELLOW}⚠{RESET}  {YELLOW}{msg}{RESET}")

    def error(self, msg: str):
        print(f"{DIM}{_ts()}{RESET} {RED}✖{RESET}  {RED}{msg}{RESET}")

    def success(self, msg: str):
        print(f"{DIM}{_ts()}{RESET} {GREEN}✔{RESET}  {GREEN}{msg}{RESET}")

    def separator(self):
        print(f"{DIM}{LINE}{RESET}")

    # -- Market Events --

    def candle_close(self, candle):
        symbol = "▲" if candle.direction == "UP" else "▼"
        color  = GREEN if candle.direction == "UP" else RED
        print(
            f"{DIM}{_ts()}{RESET} "
            f"{color}{BOLD}{symbol} CANDLE{RESET} "
            f"O:{candle.open:.1f}  C:{candle.close:.1f}  "
            f"Dir:{color}{candle.direction}{RESET}"
        )

    def sequence_detected(self, direction: str, candles, market_ticker: str = ""):
        from config import SEQUENCE_LENGTH
        color = GREEN if direction == "UP" else RED
        arrow = ("▲" if direction == "UP" else "▼") * SEQUENCE_LENGTH
        opposite = "DOWN" if direction == "UP" else "UP"
        
        market_info = f"  Market: {BOLD}{market_ticker}{RESET}" if market_ticker else ""
        
        print()
        print(f"{DIM}{LINE}{RESET}")
        print(
            f"{_ts()} {color}{BOLD}SEQUENCE DETECTED  {arrow}{RESET}  "
            f"Trend: {color}{BOLD}{direction}{RESET}  "
            f"Target: {CYAN}{BOLD}{opposite}{RESET}{market_info}"
        )
        print(f"{DIM}{LINE}{RESET}")


    def price_check(self, side: str, price: float, elapsed: float, window: float, is_martingale: bool = False, bid: float = 0):
        label   = "MARTINGALE" if is_martingale else "ENTRY     "
        color   = MAGENTA if is_martingale else CYAN
        ok      = price <= 0.40
        pmark   = f"{GREEN}✔ BELOW LIMIT{RESET}" if ok else f"{YELLOW}above limit{RESET}"
        bar     = _progress_bar(elapsed, window)
        
        bid_str = f" (Bid: {bid:.3f})" if bid > 0 else ""
        print(
            f"{DIM}{_ts()}{RESET} {color}[{label}]{RESET} "
            f"Price {side}: {BOLD}{price:.3f}{RESET}{bid_str}  {pmark}  "
            f"Time: {bar} {elapsed:.0f}s/{window:.0f}s"
        )

    def order_placed(self, trade, is_martingale: bool = False):
        label = "MARTINGALE" if is_martingale else "1st ORDER"
        color = MAGENTA if is_martingale else GREEN
        profit_info = ""
        if trade.entry_price > 0:
            roi_pct = ((1.0 / trade.entry_price) - 1.0) * 100
            profit_info = f"  Target ROI: {BOLD}{roi_pct:.0f}%{RESET}"
            
        print()
        print(f"{DIM}{LINE}{RESET}")
        print(
            f"{_ts()}  {color}{BOLD}▶ ORDER PLACED — {label}{RESET}"
        )
        print(
            f"          Side    : {BOLD}{trade.side}{RESET}\n"
            f"          Price   : {BOLD}{trade.entry_price:.3f}{RESET}\n"
            f"          Size    : {BOLD}${trade.size_usdc:.2f} USDC{RESET}  "
            f"({trade.shares:.2f} shares){profit_info}"
        )
        if trade.order_id:
            print(f"          Order ID: {DIM}{trade.order_id[:20]}...{RESET}")
        print(f"{DIM}{LINE}{RESET}\n")

    def order_failed(self, resp: dict):
        self.error(f"Order rejected: {resp.get('errorMsg', resp)}")

    def win_signal(self, trade, candle):
        color = GREEN
        print()
        print(f"{color}{BOLD}{DLINE}{RESET}")
        print(
            f"{_ts()}  {color}{BOLD}✔ CANDLE CLOSED IN FAVOR  "
            f"({candle.direction}){RESET}"
        )
        print(f"{color}{BOLD}{DLINE}{RESET}\n")

    def loss_signal(self, msg: str):
        print()
        print(f"{RED}{BOLD}  ✖ {msg}{RESET}\n")

    def timeout(self, msg: str):
        print(f"{DIM}{_ts()}{RESET} {YELLOW}⏱ {msg}{RESET}")

    def state_change(self, new_state: str):
        print(f"{DIM}{_ts()}  State → {BOLD}{new_state}{RESET}")

    def startup_info(self, token_ids: dict, trade_size: float):
        self.separator()
        print(
            f"  Market    : btc-updown-5m\n"
            f"  UP token  : {DIM}{token_ids.get('UP', 'N/A')[:20]}...{RESET}\n"
            f"  DOWN token: {DIM}{token_ids.get('DOWN', 'N/A')[:20]}...{RESET}\n"
            f"  Size      : ${trade_size:.2f} USDC per entry"
        )
        self.separator()
        print()


def _progress_bar(elapsed: float, total: float, width: int = 10) -> str:
    ratio = min(elapsed / total, 1.0)
    filled = int(ratio * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}]"
