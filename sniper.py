# =============================================================
# sniper.py — Whale Sniper Mode
# =============================================================
# Monitors Binance aggTrade stream for large counter-market
# volume accumulation in the last 30–5 seconds of each 5m candle.
# When a whale signal is detected, immediately enters Polymarket
# on the side the whale is pushing.
#
# Key design choices:
#   • Uses Binance COMBINED stream (@kline_5m + @aggTrade) per market
#     so a single WebSocket connection handles both timing and volume.
#   • aggTrade field "m" (isBuyerMaker):
#       True  → buyer is market maker → SELL trade (bearish pressure)
#       False → seller is market maker → BUY  trade (bullish pressure)
#   • Whale "identity" cannot be a real wallet address on a CEX.
#     We build a behavioral fingerprint (pseudo-ID) from the pattern:
#     market + side + avg seconds-before-close. Repeated appearances
#     of the same fingerprint track the same entity across candles.
#   • Completely independent of Strategy: no martingale, no profit target.
#     Each sniper trade stands alone and resolves with the candle.
# =============================================================

import json
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import websocket

from config import (
    MARKETS,
    SNIPER_WHALE_MIN_USDT,
    SNIPER_WINDOW_START_SECONDS,
    SNIPER_WINDOW_END_SECONDS,
    SNIPER_TRADE_SIZE_USDC,
    SNIPER_MAX_PRICE,
    SNIPER_DOMINANCE_RATIO,
    SNIPER_MIN_PRICE_IMPACT,
    MARKET_REFRESH_INTERVAL,
)
from logger import (
    BotLogger, MarketLogger,
    BOLD, RESET, YELLOW, GREEN, RED, CYAN, MAGENTA, DIM, WHITE,
    LINE, DLINE,
)
from time_utils import synced_time


# ------------------------------------------------------------------ #
# Data Structures                                                      #
# ------------------------------------------------------------------ #

@dataclass
class TradePoint:
    """Single aggTrade data point captured during the sniper window."""
    timestamp:   float
    price:       float
    quantity:    float
    volume_usdt: float
    is_sell:     bool   # True = SELL trade (isBuyerMaker=True)


@dataclass
class WhaleFingerprint:
    """
    Behavioral fingerprint of a suspected whale entity.

    Since Binance is a centralized exchange, wallet addresses are NOT
    exposed via public streams. We approximate the entity identity
    using a stable behavioral signature:
        (market, side, avg timing relative to candle close)

    The same entity detected on multiple candles converges to the same
    pseudo-ID if it consistently acts on the same market/side/timing.
    """
    pseudo_id:          str
    market:             str
    side:               str    # "SELL" or "BUY"
    occurrences:        int   = 0
    total_volume_usdt:  float = 0.0
    avg_timing_seconds: float = 0.0  # avg seconds-before-close at event
    last_seen:          float = field(default_factory=synced_time)

    def record(self, volume_usdt: float, seconds_before_close: float):
        self.occurrences       += 1
        self.total_volume_usdt += volume_usdt
        # Running average of timing
        n = self.occurrences
        self.avg_timing_seconds = (
            (self.avg_timing_seconds * (n - 1) + seconds_before_close) / n
        )
        self.last_seen = synced_time()


@dataclass
class SniperTrade:
    """Records a trade opened by the Sniper."""
    market_key:       str
    token_id:         str
    side:             str
    entry_price:      float
    size_usdc:        float
    shares:           float
    order_id:         str
    whale_id:         str
    whale_volume_usdt: float   # measured in USDT (Binance side)
    entry_time:       float = field(default_factory=synced_time)
    closed_early:     bool  = False
    exit_price:       float = 0.0

@dataclass
class SniperResult:
    """Outcome of a completed Sniper trade, settled on kline close."""
    market_key:       str
    side:             str      # "UP" or "DOWN" — what the sniper bought
    entry_price:      float
    size_usdc:        float    # Polymarket stake
    shares:           float
    whale_id:         str
    whale_vol_usdt:   float
    candle_direction: str      # final kline close direction
    won:              bool
    pnl_usdc:         float    # estimated P&L (settle 1.00 win / 0.00 loss)
    entry_time:       float


# ------------------------------------------------------------------ #
# Whale Tracker — cross-candle entity fingerprinting                  #
# ------------------------------------------------------------------ #

class WhaleTracker:
    """
    Maintains behavioral fingerprints for suspected whale entities
    across all markets and candles.

    Matching heuristic:
        A new event matches an existing fingerprint when:
          • same market AND same side
          • timing delta < 8 seconds from fingerprint average
        If no match found, a new fingerprint is created.
    """

    _TIMING_MATCH_WINDOW_S = 8.0   # seconds tolerance for fingerprint match

    def __init__(self):
        self._fingerprints: Dict[str, WhaleFingerprint] = {}
        self._lock          = threading.Lock()
        self._counter       = 0

    def record_event(
        self,
        market: str,
        side: str,
        volume_usdt: float,
        seconds_before_close: float,
    ) -> str:
        """
        Records a whale event.
        Returns the pseudo-ID of the matched or newly created entity.
        """
        with self._lock:
            best: Optional[WhaleFingerprint] = None
            for fp in self._fingerprints.values():
                if fp.market == market and fp.side == side:
                    delta = abs(fp.avg_timing_seconds - seconds_before_close)
                    if delta <= self._TIMING_MATCH_WINDOW_S:
                        best = fp
                        break

            if best:
                best.record(volume_usdt, seconds_before_close)
                return best.pseudo_id

            # New entity
            self._counter += 1
            pseudo_id = f"WHALE-{market}-{side[0]}{self._counter:04d}"
            fp = WhaleFingerprint(pseudo_id=pseudo_id, market=market, side=side)
            fp.record(volume_usdt, seconds_before_close)
            self._fingerprints[pseudo_id] = fp
            return pseudo_id

    def get_summary(self) -> str:
        """Returns a formatted summary of all tracked whale fingerprints."""
        with self._lock:
            if not self._fingerprints:
                return "  No whale entities tracked in this session."
            lines = [f"  {BOLD}Tracked Whale Entities:{RESET}"]
            sorted_fps = sorted(
                self._fingerprints.values(),
                key=lambda x: x.total_volume_usdt,
                reverse=True,
            )
            for fp in sorted_fps:
                lines.append(
                    f"    {CYAN}{fp.pseudo_id}{RESET}  "
                    f"Market: {fp.market}  Side: {fp.side}  "
                    f"Seen: {fp.occurrences}×  "
                    f"Total Vol: {GREEN}${fp.total_volume_usdt:,.0f} USDT{RESET}  "
                    f"Avg Timing: {fp.avg_timing_seconds:.1f}s before close"
                )
            return "\n".join(lines)


# ------------------------------------------------------------------ #
# SniperMarket — per-market stream handler                            #
# ------------------------------------------------------------------ #

class SniperMarket:
    """
    Opens a single Binance COMBINED stream per market:
        {symbol}@kline_5m  → tracks candle timing and direction
        {symbol}@aggTrade  → detects large counter-market trades

    Whale detection logic:
        • If the running candle is trending UP and counter-SELL volume
          (sum of SELL aggTrades) exceeds SNIPER_WHALE_MIN_USDT inside
          the sniper window → whale is pushing DOWN → buy DOWN on Polymarket.
        • Symmetric for DOWN candles and BUY whale.

    One entry per candle is enforced; the flag resets on each new kline.
    """

    def __init__(
        self,
        market_key:    str,
        poly_client,
        logger:        MarketLogger,
        token_ids:     dict,
        whale_tracker: WhaleTracker,
    ):
        self.market_key    = market_key
        self.poly          = poly_client
        self.log           = logger
        self.token_ids     = token_ids
        self.whale_tracker = whale_tracker

        cfg            = MARKETS[market_key]
        self.symbol    = cfg["binance_symbol"]
        self._series_id = cfg["series_id"]

        # Asset-specific thresholds
        self._min_usdt   = cfg.get("sniper_min_usdt", SNIPER_WHALE_MIN_USDT)
        self._dom_ratio  = cfg.get("sniper_dom_ratio", SNIPER_DOMINANCE_RATIO)
        self._min_impact = cfg.get("sniper_impact", SNIPER_MIN_PRICE_IMPACT)

        # Book tracking
        self._best_bid_usdt = 0.0
        self._best_ask_usdt = 0.0

        # Binance combined stream URL
        sym = self.symbol.lower()
        self._ws_url = (
            f"wss://stream.binance.com:9443/stream?streams="
            f"{sym}@kline_5m/{sym}@aggTrade/{sym}@bookTicker"
        )

        # Candle state (updated by kline messages)
        self._candle_close_ms:  Optional[float] = None
        self._candle_direction: Optional[str]   = None   # "UP" or "DOWN"

        # Sniper window state
        self._window_active  = False
        self._buy_vol:  float = 0.0   # cumulative BUY-side volume in window
        self._sell_vol: float = 0.0   # cumulative SELL-side volume in window
        self._trade_placed    = False  # one entry per candle

        self._lock    = threading.RLock()
        self._running = False
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread]   = None
        self._last_token_refresh: float = 0.0

        # Result tracking
        self._pending_trade: Optional[SniperTrade] = None
        self._results:       List[SniperResult]    = []

        # Balance / force tracking — tracks whether whale volume is dominant
        # and whether it is actually moving price within the sniper window
        self._window_open_price:    Optional[float] = None  # kline close px when window opened
        self._candle_current_price: Optional[float] = None  # latest kline close px (real-time)
        self._last_rejection_log:   float = 0.0             # throttle noisy rejection logs

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._connect, daemon=True)
        self._thread.start()
        self.log.info(f"Sniper stream started — monitoring {self.symbol}")

    def stop(self):
        self._running = False
        if self._ws:
            self._ws.close()

    def update_token_ids(self, token_ids: dict):
        with self._lock:
            self.token_ids = token_ids

    # ------------------------------------------------------------------ #
    # WebSocket                                                            #
    # ------------------------------------------------------------------ #

    def _connect(self):
        self._ws = websocket.WebSocketApp(
            self._ws_url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open,
        )
        self._ws.run_forever(ping_interval=30, ping_timeout=10)

    def _on_open(self, ws):
        self.log.success(f"Connected to Binance combined stream ({self.symbol})")

    def _on_error(self, ws, error):
        self.log.error(f"WebSocket error: {error}")

    def _on_close(self, ws, code, msg):
        if self._running:
            self.log.warn(f"WS closed. Reconnecting in 3s…")
            time.sleep(3)
            self._connect()

    def _on_message(self, ws, raw: str):
        try:
            data    = json.loads(raw)
            stream  = data.get("stream", "")
            payload = data.get("data", {})

            if "@kline" in stream:
                self._handle_kline(payload)
            elif "@aggTrade" in stream:
                self._handle_trade(payload)
            elif "@bookTicker" in stream:
                self._handle_book_ticker(payload)
        except Exception as e:
            self.log.error(f"Message error: {e}")

    # ------------------------------------------------------------------ #
    # BookTicker handler — tracks real-time opposite wall sizes           #
    # ------------------------------------------------------------------ #

    def _handle_book_ticker(self, payload: dict):
        with self._lock:
            try:
                # b=best bid px, B=best bid qty, a=best ask px, A=best ask qty
                self._best_bid_usdt = float(payload.get("b", 0)) * float(payload.get("B", 0))
                self._best_ask_usdt = float(payload.get("a", 0)) * float(payload.get("A", 0))
            except Exception:
                pass


    # ------------------------------------------------------------------ #
    # Kline handler — tracks candle timing and direction                  #
    # ------------------------------------------------------------------ #

    def _handle_kline(self, data: dict):
        k = data.get("k", {})
        if not k:
            return

        close_ms  = float(k["T"])
        open_px   = float(k["o"])
        close_px  = float(k["c"])
        direction = "UP" if close_px >= open_px else "DOWN"
        is_closed = bool(k["x"])

        with self._lock:
            # Detect new candle — reset state
            if self._candle_close_ms and close_ms != self._candle_close_ms:
                self._reset_window()

            self._candle_close_ms  = close_ms
            self._candle_direction = direction

            now_ms      = synced_time() * 1000
            remaining_s = (close_ms - now_ms) / 1000

            in_window = (
                SNIPER_WINDOW_END_SECONDS
                < remaining_s
                <= SNIPER_WINDOW_START_SECONDS
            )

            # Always update the live price so _evaluate_whale_signal can measure impact
            self._candle_current_price = close_px

            if in_window and not self._window_active:
                # Window just opened — record reference price
                self._window_active     = True
                self._buy_vol           = 0.0
                self._sell_vol          = 0.0
                self._window_open_price = close_px
                self.log.info(
                    f"🎯 Sniper window OPEN  "
                    f"[{remaining_s:.0f}s left | candle: {direction} | ref px: {close_px:.4f}]"
                )

            elif not in_window and self._window_active:
                # Window closed (either by timing or candle close)
                self._window_active = False

            if is_closed:
                # Settle any open sniper trade BEFORE resetting the window
                if self._pending_trade:
                    self._settle_trade(direction)
                self._reset_window()

    # ------------------------------------------------------------------ #
    # aggTrade handler — accumulates whale volume in sniper window        #
    # ------------------------------------------------------------------ #

    def _handle_trade(self, data: dict):
        with self._lock:
            if not self._window_active or self._trade_placed:
                return

            price    = float(data["p"])
            qty      = float(data["q"])
            is_sell  = bool(data["m"])   # isBuyerMaker=True → SELL trade
            vol_usdt = price * qty   # Binance pairs are XYZ/USDT

            if is_sell:
                self._sell_vol += vol_usdt
            else:
                self._buy_vol += vol_usdt

            self._evaluate_whale_signal()

    # ------------------------------------------------------------------ #
    # Whale detection                                                      #
    # ------------------------------------------------------------------ #

    def _evaluate_whale_signal(self):
        """
        3-layer whale validation:

          Layer 1 — Absolute threshold:
            counter-market volume must reach SNIPER_WHALE_MIN_USDT.

          Layer 2 — Volume dominance:
            counter_vol / total_vol >= SNIPER_DOMINANCE_RATIO.
            Filters out scenarios where heavy opposing flow absorbs the whale,
            meaning the net effect would be negligible.

          Layer 3 — Price impact:
            price must have moved >= SNIPER_MIN_PRICE_IMPACT % in the whale's
            direction since the window opened, confirming the volume is actually
            translating into market movement and not being silently absorbed.
        """
        if not self._candle_direction or not self._candle_close_ms:
            return

        remaining_s = (self._candle_close_ms - synced_time() * 1000) / 1000

        # --- Layer 1: Which side? Does it meet absolute threshold? ---
        if self._candle_direction == "UP" and self._sell_vol >= self._min_usdt:
            counter_vol = self._sell_vol
            same_vol    = self._buy_vol
            whale_side  = "SELL"
            target_side = "DOWN"
        elif self._candle_direction == "DOWN" and self._buy_vol >= self._min_usdt:
            counter_vol = self._buy_vol
            same_vol    = self._sell_vol
            whale_side  = "BUY"
            target_side = "UP"
        else:
            return  # Threshold not yet reached

        total_vol = counter_vol + same_vol

        # --- Layer 2: Volume dominance ---
        dominance = counter_vol / total_vol if total_vol > 0 else 0.0
        if dominance < self._dom_ratio:
            now = synced_time()
            if now - self._last_rejection_log >= 2.0:
                self.log.info(
                    f"⚠️  [{self.market_key}] Whale vol ${counter_vol:,.0f} USDT "
                    f"but dominance {dominance:.0%} < {self._dom_ratio:.0%} "
                    f"(opposing absorption ${same_vol:,.0f}) — waiting"
                )
                self._last_rejection_log = now
            return  # Opposing flow is absorbing too much pressure

        # --- Layer 2.5: Orderbook Imbalance Override ---
        # Did the whale volume just obliterate the opposite defense wall?
        override_impact = False
        defense_usdt = self._best_bid_usdt if whale_side == "SELL" else self._best_ask_usdt
        
        # If the whale dumped 50% more free capital than the limit orders at the top of the barrier,
        # we assume the wall broke even if the price haven't ticked over the impact mark yet.
        if defense_usdt > 0 and (counter_vol >= defense_usdt * 1.5):
            override_impact = True

        # --- Layer 3: Price impact ---
        impact = 0.0
        if self._candle_current_price and self._window_open_price and self._window_open_price > 0:
            if whale_side == "SELL":
                # Selling should push price DOWN from window open
                impact = (self._window_open_price - self._candle_current_price) / self._window_open_price
            else:
                # Buying should push price UP from window open
                impact = (self._candle_current_price - self._window_open_price) / self._window_open_price

        if impact < self._min_impact and not override_impact:
            now = synced_time()
            if now - self._last_rejection_log >= 2.0:
                wall_info = f"| Wall left: ${defense_usdt:,.0f} " if defense_usdt > 0 else ""
                self.log.info(
                    f"⚠️  [{self.market_key}] Vol ${counter_vol:,.0f} USDT "
                    f"({dominance:.0%} dominant) but impact {impact:.4%} "
                    f"< {self._min_impact:.4%} {wall_info}— waiting"
                )
                self._last_rejection_log = now
            return  # Volume not yet translating to actual price movement

        # All 3 layers passed — this is a genuine dominant whale event
        whale_vol = counter_vol

        # Record fingerprint and get pseudo-ID
        whale_id = self.whale_tracker.record_event(
            market=self.market_key,
            side=whale_side,
            volume_usdt=whale_vol,
            seconds_before_close=remaining_s,
        )

        print()
        print(f"  {CYAN}{BOLD}{DLINE}{RESET}")
        print(
            f"  🐋 {YELLOW}{BOLD}WHALE SIGNAL CONFIRMED{RESET}  "
            f"[{self.market_key}]"
        )
        print(
            f"     Entity     : {CYAN}{whale_id}{RESET}\n"
            f"     Side       : {BOLD}{whale_side}{RESET}  "
            f"(counter to candle {self._candle_direction})\n"
            f"     Volume     : {GREEN}${whale_vol:,.0f} USDT{RESET}  "
            f"({dominance:.0%} of window vol)\n"
            f"     Absorbed   : ${same_vol:,.0f} USDT "
            f"({1-dominance:.0%} opposing)\n"
            f"     Px Impact  : {impact:.4%} in whale direction\n"
            f"     Time left  : {remaining_s:.1f}s before close\n"
            f"     Target     : Buying {BOLD}{target_side}{RESET} on Polymarket"
        )
        print(f"  {CYAN}{BOLD}{DLINE}{RESET}\n")

        self._execute_entry(target_side, whale_id, whale_vol)

    # ------------------------------------------------------------------ #
    # Polymarket entry                                                     #
    # ------------------------------------------------------------------ #

    def _execute_entry(self, target_side: str, whale_id: str, whale_vol: float):
        """Places a Polymarket buy order following the whale direction."""
        # Mark candle as processed immediately — prevents duplicate signal
        # firing from subsequent aggTrades even if entry is skipped.
        self._trade_placed = True

        token_id = self.token_ids.get(target_side)
        if not token_id:
            self.log.warn(f"No token ID for {target_side} — refreshing tokens…")
            self._refresh_tokens()
            token_id = self.token_ids.get(target_side)
            if not token_id:
                self.log.error(f"Still no token for {target_side}. Sniper entry aborted.")
                return

        # --- Grid Limit Execution ---
        mid = self.poly.get_midpoint(token_id)
        limit_price = round(mid + 0.01, 3)
        if limit_price < 0.01: limit_price = 0.01
        
        success = False
        resp = {}
        target_price = limit_price
        
        for attempt in range(4): # 4 attempts traversing the book upwards
            if target_price > SNIPER_MAX_PRICE:
                self.log.warn(
                    f"Grid target {target_price:.3f} out of sniper range "
                    f"(max: {SNIPER_MAX_PRICE}) — aborting grid execution."
                )
                break
                
            self.log.info(f"🚀 Sniper Grid Limit → {target_side} target @ {target_price:.3f} | Size: ${SNIPER_TRADE_SIZE_USDC:.2f} (Attempt {attempt+1})")
            
            resp = self.poly.buy_exact(token_id, target_price, SNIPER_TRADE_SIZE_USDC)
            if resp.get("success") or resp.get("status") in ("live", "matched", "delayed"):
                success = True
                break
            
            target_price = round(target_price + 0.01, 3)
            time.sleep(0.3)

        if success:
            shares = round(SNIPER_TRADE_SIZE_USDC / target_price, 4)
            self._trade_placed  = True  # Prevent double-entry this candle
            self._pending_trade = SniperTrade(
                market_key        = self.market_key,
                token_id          = token_id,
                side              = target_side,
                entry_price       = target_price,
                size_usdc         = SNIPER_TRADE_SIZE_USDC,
                shares            = shares,
                order_id          = resp.get("orderID", ""),
                whale_id          = whale_id,
                whale_volume_usdt = whale_vol,
            )
            self.log.success(
                f"✅ SNIPER TRADE PLACED (Grid Limit)  "
                f"Side: {target_side}  Price: {target_price:.4f}  "
                f"Shares: {shares:.4f}  Whale: {whale_id}  "
            )
            
            # Initiate Trailing Take-Profit tracking in background
            t = threading.Thread(target=self._monitor_ttp, args=(self._pending_trade,), daemon=True)
            t.start()
        else:
            self.log.error(
                f"❌ Sniper grid execution failed: {resp.get('errorMsg', 'No liquidity')}"
            )

    # ------------------------------------------------------------------ #
    # Trailing Take-Profit (Phase 2)                                       #
    # ------------------------------------------------------------------ #

    def _monitor_ttp(self, trade: SniperTrade):
        """Monitors a successful entry for Trailing Take Profit (TTP)"""
        peak_roi        = 0.0
        locked_stop_roi = 0.0
        active_ttp      = False
        
        self.log.info(f"🎯 [TTP] Monitoring started for {trade.side} @ {trade.entry_price:.3f}")
        
        while self._pending_trade == trade and self._window_active:
            time.sleep(1.0)
            
            # Since we BOUHGT the outcome, to exit we need to SELL it. 
            # We look at get_bid_price to see what the market will pay us.
            current_bid = self.poly.get_bid_price(trade.token_id)
            if current_bid <= 0.0: continue
            
            current_roi = (current_bid - trade.entry_price) / trade.entry_price
            
            if current_roi > peak_roi:
                peak_roi = current_roi
                
            # Trailing target triggers at +30% ROI
            if current_roi >= 0.30 and not active_ttp:
                active_ttp = True
                locked_stop_roi = 0.15 # Lock in at least 15% profit
                self.log.success(f"🔒 [TTP] Lock-in reached! Peak ROI +{peak_roi:.1%}. Secured Stop at +{locked_stop_roi:.1%}")
                
            # If active, trail the stop 15% behind the peak
            if active_ttp:
                new_stop = peak_roi - 0.15
                if new_stop > locked_stop_roi:
                    locked_stop_roi = new_stop
                    
                # Did we break the stop?
                if current_roi <= locked_stop_roi:
                    self.log.warn(f"📉 [TTP] Pullback detected! Stop broken at {locked_stop_roi:.1%}. Triggering LIMIT SELL at {current_bid:.3f}")
                    rest = self.poly.sell_exact(trade.token_id, current_bid, trade.shares)
                    
                    if rest.get("success") or rest.get("status") in ("live", "matched", "delayed"):
                        self.log.success(f"📈 [TTP] DUMP SUCCESSFUL. Locked profit at {current_bid:.3f}. Trade preserved early.")
                        trade.closed_early = True
                        trade.exit_price = current_bid
                    else:
                        self.log.error(f"❌ [TTP] Dump failed: {rest.get('errorMsg')}")
                    return

    # ------------------------------------------------------------------ #
    # Utilities                                                            #
    # ------------------------------------------------------------------ #

    def _settle_trade(self, candle_direction: str):
        """
        Called when kline closes with the final candle direction.
        Determines win/loss, calculates estimated P&L, logs result,
        and appends to the session result list.
        """
        t = self._pending_trade
        self._pending_trade = None

        won = (candle_direction == t.side)
        pnl = (1.0 - t.entry_price) * t.shares if won else -t.size_usdc
        roi = (pnl / t.size_usdc) * 100

        result = SniperResult(
            market_key       = t.market_key,
            side             = t.side,
            entry_price      = t.entry_price,
            size_usdc        = t.size_usdc,
            shares           = t.shares,
            whale_id         = t.whale_id,
            whale_vol_usdt   = t.whale_volume_usdt,
            candle_direction = candle_direction,
            won              = won,
            pnl_usdc         = pnl,
            entry_time       = t.entry_time,
        )
        self._results.append(result)

        outcome_color = GREEN if won else RED
        outcome_label = "WIN  ✅" if won else "LOSS ❌"
        settle_px     = 1.00 if won else 0.00
        pnl_str       = f"+${pnl:.4f}" if won else f"-${abs(pnl):.4f}"
        match_str     = "← MATCH ✅" if won else "← AGAINST ❌"
        arrow         = "▲" if candle_direction == "UP" else "▼"

        print()
        print(f"  {outcome_color}{BOLD}{DLINE}{RESET}")
        print(
            f"  🎯 {BOLD}SNIPER RESULT [{self.market_key}]{RESET}  "
            f"{outcome_color}{BOLD}{outcome_label}{RESET}"
        )
        print(
            f"     Side      : {BOLD}{t.side}{RESET}\n"
            f"     Entry     : {BOLD}{t.entry_price:.4f}{RESET}  →  Settled: {settle_px:.2f}\n"
            f"     Size      : ${t.size_usdc:.2f} USDC  |  Shares: {t.shares:.4f}\n"
            f"     Candle    : {arrow} {candle_direction}  {match_str}\n"
            f"     P&L       : {outcome_color}{BOLD}{pnl_str} USDC  ({roi:+.1f}%){RESET}\n"
            f"     Whale     : {t.whale_id}  |  Vol: ${t.whale_volume_usdt:,.0f} USDT"
        )
        print(f"  {outcome_color}{BOLD}{DLINE}{RESET}\n")

        # Fire background settlement + monitoring sync (same flow as Strategy wins)
        if won:
            self.poly.register_win_for_settlement(t, t.size_usdc)

    def get_results(self) -> List[SniperResult]:
        """Returns a copy of all trade results accumulated this session."""
        return list(self._results)

    def _reset_window(self):
        self._window_active      = False
        self._buy_vol            = 0.0
        self._sell_vol           = 0.0
        self._trade_placed       = False
        self._window_open_price  = None
        self._last_rejection_log = 0.0

    def _refresh_tokens(self):
        new_ids = self.poly.fetch_market_tokens(series_id=self._series_id)
        if new_ids:
            with self._lock:
                self.token_ids = new_ids
                self._last_token_refresh = synced_time()
            self.log.info(
                f"Tokens refreshed — "
                f"UP: {new_ids.get('UP', 'N/A')[:12]}…  "
                f"DOWN: {new_ids.get('DOWN', 'N/A')[:12]}…"
            )
        else:
            self.log.warn("Token refresh failed.")


# ------------------------------------------------------------------ #
# Sniper — orchestrator (all active markets)                          #
# ------------------------------------------------------------------ #

class Sniper:
    """
    Launches one SniperMarket handler per active market.
    All handlers share a single WhaleTracker for cross-market
    fingerprint correlation.

    Token IDs are refreshed every MARKET_REFRESH_INTERVAL seconds
    in a background thread so the sniper always holds current tokens.
    """

    def __init__(self, poly_client, logger: BotLogger, active_markets: list):
        self.poly            = poly_client
        self.log             = logger
        self._active_markets = active_markets
        self._handlers:      Dict[str, SniperMarket] = {}
        self._whale_tracker  = WhaleTracker()
        self._running        = False
        self._refresh_thread: Optional[threading.Thread] = None

    def start(self):
        print(f"\n{CYAN}{BOLD}{DLINE}{RESET}")
        print(f"  🎯 {BOLD}SNIPER MODE — Whale Detection Active{RESET}")
        print(f"  Markets  : {', '.join(self._active_markets)}")
        print(f"  Window   : last {SNIPER_WINDOW_START_SECONDS}s → {SNIPER_WINDOW_END_SECONDS}s of each candle")
        print(f"  Threshold: ${SNIPER_WHALE_MIN_USDT:,.0f} USDT counter-market volume")
        print(f"  Entry    : ${SNIPER_TRADE_SIZE_USDC:.2f} USDC (Polymarket)  |  Max price: {SNIPER_MAX_PRICE:.2f}")
        print(f"{CYAN}{BOLD}{DLINE}{RESET}\n")

        self._running = True

        for market_key in self._active_markets:
            cfg       = MARKETS[market_key]
            token_ids = self.poly.fetch_market_tokens(series_id=cfg["series_id"])
            if not token_ids:
                self.log.warn(f"[SNIPER:{market_key}] Could not load tokens — skipping market.")
                continue

            mlog    = MarketLogger(self.log, f"SNP:{market_key}")
            handler = SniperMarket(
                market_key=market_key,
                poly_client=self.poly,
                logger=mlog,
                token_ids=token_ids,
                whale_tracker=self._whale_tracker,
            )
            handler.start()
            self._handlers[market_key] = handler

        if not self._handlers:
            self.log.error("[SNIPER] No markets initialized. Aborting.")
            return

        self.log.info(
            f"[SNIPER] Active on {len(self._handlers)} market(s): "
            f"{', '.join(self._handlers.keys())}"
        )

        # Background token refresh loop
        self._refresh_thread = threading.Thread(
            target=self._token_refresh_loop, daemon=True
        )
        self._refresh_thread.start()

    def stop(self):
        self._running = False
        for handler in self._handlers.values():
            handler.stop()

        # Collect all trade results from every market handler
        all_results: List[SniperResult] = []
        for handler in self._handlers.values():
            all_results.extend(handler.get_results())

        print(f"\n{CYAN}{BOLD}{DLINE}{RESET}")
        print(f"  🎯 {BOLD}SNIPER SESSION SUMMARY{RESET}")
        print(f"{CYAN}{BOLD}{DLINE}{RESET}")

        if all_results:
            wins      = [r for r in all_results if r.won]
            losses    = [r for r in all_results if not r.won]
            total_pnl = sum(r.pnl_usdc for r in all_results)
            win_rate  = len(wins) / len(all_results) * 100
            pc        = GREEN if total_pnl >= 0 else RED
            ps        = "+" if total_pnl >= 0 else ""

            print(
                f"\n  Trades: {BOLD}{len(all_results)}{RESET}  |  "
                f"{GREEN}Wins: {len(wins)}{RESET}  |  "
                f"{RED}Losses: {len(losses)}{RESET}  |  "
                f"Win Rate: {BOLD}{win_rate:.1f}%{RESET}"
            )
            print(f"  Total P&L : {pc}{BOLD}{ps}${total_pnl:.4f} USDC{RESET}\n")

            # Trade-by-trade table
            hdr = f"  {'#':<3} {'Mkt':<6} {'Side':<5} {'Entry':>7} {'Candle':<7} {'Result':<10} {'P&L':>12}"
            print(hdr)
            print(f"  {'─' * 54}")
            for i, r in enumerate(all_results, 1):
                outcome = f"{GREEN}WIN ✅{RESET}" if r.won else f"{RED}LOSS ❌{RESET}"
                pnl_s   = f"+${r.pnl_usdc:.4f}" if r.won else f"-${abs(r.pnl_usdc):.4f}"
                print(
                    f"  {i:<3} {r.market_key:<6} {r.side:<5} "
                    f"{r.entry_price:>7.4f} {r.candle_direction:<7} "
                    f"{outcome}  {pnl_s:>12}"
                )
            print()
        else:
            print("\n  No trades executed in this session.\n")

        print(self._whale_tracker.get_summary())
        print(f"\n{CYAN}{BOLD}{DLINE}{RESET}\n")

    def _token_refresh_loop(self):
        """Periodically refreshes Polymarket token IDs for all sniper markets."""
        while self._running:
            time.sleep(MARKET_REFRESH_INTERVAL)
            for market_key, handler in self._handlers.items():
                cfg     = MARKETS[market_key]
                new_ids = self.poly.fetch_market_tokens(series_id=cfg["series_id"])
                if new_ids:
                    handler.update_token_ids(new_ids)
