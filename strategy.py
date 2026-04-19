# =============================================================
# strategy.py — Strategy State Machine + Martingale
# =============================================================

import time
import threading
from enum import Enum
from typing import Optional, Dict
from time_utils import synced_time

from config import (
    ENTRY_PRICE_THRESHOLD,
    GALE_1_PRICE_THRESHOLD,
    GALE_2_PLUS_PRICE_THRESHOLD,
    ENTRY_WINDOW_SECONDS,
    MARTINGALE_WINDOW_SECONDS,
    MARTINGALE_MULTIPLIER,
    MAX_GALES,
    BASE_TRADE_SIZE_USDC,
    PRICE_POLL_INTERVAL,
    MARKET_REFRESH_INTERVAL,
    PROFIT_TARGET_PERCENT,
    MARKETS,
    STOP_LOSS_PERCENT,
    INDECISION_EXIT_WINDOW_S,
    INDECISION_PRICE_RANGE,
)


# ------------------------------------------------------------------ #
# States                                                               #
# ------------------------------------------------------------------ #

class State(Enum):
    IDLE             = "IDLE"              # Waiting for sequence
    SCANNING         = "SCANNING"          # Sequence detected, monitoring price
    IN_TRADE_1       = "IN_TRADE_1"        # 1st order open
    VERIFYING_RESULT = "VERIFYING_RESULT"  # Querying Polymarket oracle
    MARTINGALE_WAIT  = "MARTINGALE_WAIT"   # Waiting for gale opportunity
    IN_GALE          = "IN_GALE"           # Gale N open


# ------------------------------------------------------------------ #
# Trade Object                                                         #
# ------------------------------------------------------------------ #

class Trade:
    def __init__(
        self,
        token_id: str,
        side: str,
        entry_price: float,
        size_usdc: float,
        shares: float,
        order_id: str = "",
        condition_id: str = "",
    ):
        self.token_id    = token_id
        self.side        = side          # "UP" or "DOWN"
        self.entry_price = entry_price
        self.size_usdc   = size_usdc
        self.shares      = shares
        self.order_id    = order_id
        self.condition_id = condition_id
        self.entry_time  = synced_time()


# ------------------------------------------------------------------ #
# Main Strategy                                                        #
# ------------------------------------------------------------------ #

class Strategy:
    """
    State machine for the reversal strategy.

    Flow:
      IDLE → (sequence of identical candles) → SCANNING
      SCANNING → (price <= 0.40 in 1st 120s) → IN_TRADE_1
      SCANNING → (120s without entry) → IDLE
      IN_TRADE_1 → (profit target hit) → IDLE
      MARTINGALE_WAIT → (same condition in window) → IN_GALE
      MARTINGALE_WAIT → (window expires) → IDLE
      IN_GALE → (profit target hit) → IDLE
      IN_GALE → (candle closes) → IDLE
    """

    def __init__(self, poly_client, logger, market_key: str = "BTC"):
        self.poly       = poly_client
        self.log        = logger   # expected to be a MarketLogger (auto-prefixes all calls)
        self._lock      = threading.RLock()
        self.market_key = market_key.upper()
        market_cfg      = MARKETS.get(self.market_key, {})
        self._series_id = market_cfg.get("series_id", "")

        # State
        self._state: State = State.IDLE

        # Sequence context
        self.sequence_direction: Optional[str] = None
        self.sequence_time: Optional[float]    = None
        self.target_side: Optional[str]        = None   # side we will buy (opposite)
        self.target_token_id: Optional[str]    = None

        # Trades
        self.trade_1: Optional[Trade]     = None   # initial entry
        self.gales: list                  = []     # list of gales [Trade, ...]
        self.gale_count: int              = 0      # how many gales executed

        # Martingale timing
        self.martingale_start: Optional[float] = None

        # Token IDs refreshed periodically
        self.token_ids: Dict[str, str] = {}
        self._last_token_refresh: float = 0
        self._heartbeat_id: str = ""

        # Price polling
        self._last_price_check: float = 0
        self._prefetch_triggered: bool = False

    # ---------------------------------------------------------------- #
    # Public state property                                              #
    # ---------------------------------------------------------------- #

    @property
    def state(self) -> State:
        return self._state

    def _set_state(self, new_state: State):
        if self._state != new_state:
            self._state = new_state
            self.log.state_change(new_state.value)

    # ---------------------------------------------------------------- #
    # BinanceStream Callbacks                                            #
    # ---------------------------------------------------------------- #
    def on_sequence_detected(self, direction: str, candles):
        """
        Called when N consecutive candles in same direction are detected.
        Only starts new scan if IDLE.
        """
        # --- MOVED OUTSIDE LOCK: Network-bound call ---
        self._refresh_tokens_if_needed()

        with self._lock:
            if self._state != State.IDLE:
                return

            opposite = "DOWN" if direction == "UP" else "UP"
            token_id  = self.token_ids.get(opposite)

            if not token_id:
                self.log.warn(f"Token ID for {opposite} not found. Ignoring.")
                return

            self.sequence_direction = direction
            self.sequence_time      = synced_time()
            self.target_side        = opposite
            self.target_token_id    = token_id
            self.trade_1            = None

            self._set_state(State.SCANNING)
            self.log.sequence_detected(direction, candles, self.token_ids.get("market_ticker", ""))

            # --- IMMEDATE ACTION: To reduce 2s delay ---
            # Instead of waiting for next tick, attempt entry NOW
            self._try_first_entry(0.0)

    def on_candle_tick(self, candle):
        """
        Called on each tick of the current candle.
        Controls entry windows and monitors positions.
        """
        with self._lock:
            now = synced_time()

            # Price checks rate-limit
            if now - self._last_price_check < PRICE_POLL_INTERVAL:
                return
            self._last_price_check = now

            # Heartbeat to keep session alive
            self._heartbeat_id = self.poly.send_heartbeat(self._heartbeat_id)

            # --- TOKEN PRE-FETCH (15s before close) ---
            # To avoid 1s latency on Gamma API call during close
            rem = candle.remaining_seconds
            if rem < 15 and not self._prefetch_triggered:
                self._prefetch_triggered = True
                threading.Thread(target=self._refresh_tokens_if_needed, kwargs={"force": True}, daemon=True).start()

            if self._state == State.SCANNING:
                elapsed = now - (candle.open_time / 1000.0)
                if elapsed > ENTRY_WINDOW_SECONDS:
                    self._set_state(State.IDLE)
                    self.log.timeout(f"Entry window ({ENTRY_WINDOW_SECONDS}s) expired without condition")
                    return
                self._try_first_entry(elapsed)

            elif self._state in (State.IN_TRADE_1, State.IN_GALE):
                self._check_position_stop_loss(candle.remaining_seconds)

            elif self._state == State.MARTINGALE_WAIT:
                elapsed = now - self.martingale_start
                if elapsed > MARTINGALE_WINDOW_SECONDS:
                    self._set_state(State.IDLE)
                    self.log.timeout("Gale window (240s) expired")
                    self._reset_context()
                    return
                self._try_gale(elapsed)
                
            elif self._state == State.VERIFYING_RESULT:
                # Just waiting for _verify_and_resolve thread to finish
                pass

    def on_candle_close(self, candle):
        """
        Called when a 5m candle closes.
        Starts final verification via Polymarket Gamma API.
        """
        with self._lock:
            self.log.candle_close(candle)

            if self._state == State.SCANNING:
                self.log.timeout("Candle closed without entry. Cancelling scan.")
                self._set_state(State.IDLE)
                self._reset_context()

            if self._state in (State.IN_TRADE_1, State.IN_GALE):
                is_trade_1 = self._state == State.IN_TRADE_1
                trade = self.trade_1 if is_trade_1 else (self.gales[-1] if self.gales else None)
                
                if trade:
                    # Determining WIN/LOSS instantly based on Binance chart
                    is_outcome_win = (candle.direction == self.target_side)
                    
                    if is_outcome_win:
                        self.log.win_signal(trade, candle)
                        
                        # Win Calculation
                        win_amt = trade.shares * 1.0
                        
                        self.poly.register_win_for_settlement(trade, self.total_spent)
                        self._set_state(State.IDLE)
                        self._reset_context()
                    else:
                        # Loss logic
                        if is_trade_1:
                            self.log.loss_signal(f"Initial trade lost. Starting Gale 1...")
                            self.martingale_start = synced_time()
                            self._set_state(State.MARTINGALE_WAIT)
                        else:
                            if self.gale_count >= MAX_GALES:
                                self.log.loss_signal(
                                    f"Gale {self.gale_count} reached MAX LIMIT ({MAX_GALES}). "
                                    "Ending sequence assuming loss."
                                )
                                self._set_state(State.IDLE)
                                self._reset_context()
                            else:
                                self.log.loss_signal(
                                    f"Gale {self.gale_count} lost. "
                                    f"Waiting for Gale {self.gale_count + 1} opportunity..."
                                )
                                self.martingale_start = synced_time()
                                self._set_state(State.MARTINGALE_WAIT)

            # If we transitioned to WAIT, try Gale immediately
            if self._state == State.MARTINGALE_WAIT:
                self._try_gale(0.0)

        # --- MOVED OUTSIDE LOCK: Network-bound call ---
        # ALWAYS update tokens on closing — each 5min cycle has new IDs
        self._refresh_tokens_if_needed(force=True)
        # Reset pre-fetch for next candle
        self._prefetch_triggered = False



    # ---------------------------------------------------------------- #
    # Entry Logic                                                        #
    # ---------------------------------------------------------------- #

    def _try_first_entry(self, elapsed: float):
        price = self.poly.get_ask_price(self.target_token_id)
        bid   = self.poly.get_bid_price(self.target_token_id)
        self.log.price_check(self.target_side, price, elapsed, ENTRY_WINDOW_SECONDS, bid=bid)

        if price <= ENTRY_PRICE_THRESHOLD and price > 0.02:
            success = self._place_buy(
                token_id      = self.target_token_id,
                price         = price,
                size_usdc     = BASE_TRADE_SIZE_USDC,
                is_gale       = False,
            )
            if success:
                self._set_state(State.IN_TRADE_1)

    def _try_gale(self, elapsed: float):
        target_token = self.token_ids.get(self.target_side)
        if not target_token:
            return # Awaits token load if API is delayed
            
        price = self.poly.get_ask_price(target_token)
        bid   = self.poly.get_bid_price(target_token)
        gale_num = self.gale_count + 1
        self.log.price_check(
            self.target_side, price, elapsed,
            MARTINGALE_WINDOW_SECONDS, is_martingale=True, 
            bid=bid
        )

        is_market_gale = self.gale_count >= 1  # Gale 2 onwards
        threshold = GALE_1_PRICE_THRESHOLD if self.gale_count == 0 else GALE_2_PLUS_PRICE_THRESHOLD

        # Gale entry logic
        if price <= threshold and price > 0.02:
            prev_size = self.gales[-1].size_usdc if self.gales else self.trade_1.size_usdc
            size      = self._calc_gale_size(prev_size)
            success   = self._place_buy(
                token_id  = target_token,
                price     = price,
                size_usdc = size,
                is_gale   = True,
                gale_num  = gale_num,
            )
            if success:
                self.gale_count += 1
                self._set_state(State.IN_GALE)

    # ---------------------------------------------------------------- #

    # ---------------------------------------------------------------- #
    # Order Execution                                                    #
    # ---------------------------------------------------------------- #

    def _place_buy(
        self,
        token_id: str,
        price: float,
        size_usdc: float,
        is_gale: bool,
        gale_num: int = 1,
    ) -> bool:
        resp = self.poly.buy(token_id, price, size_usdc)

        status    = resp.get("status", "")
        error_msg = str(resp.get("errorMsg", "")).lower()

        if resp.get("success") or status in ("live", "matched", "delayed"):
            shares = round(size_usdc / price, 4)
            trade  = Trade(
                token_id     = token_id,
                side         = self.target_side,
                entry_price  = price,
                size_usdc    = size_usdc,
                shares       = shares,
                order_id     = resp.get("orderID", ""),
                condition_id = self.token_ids.get("condition_id", ""),
            )
            if is_gale:
                self.gales.append(trade)
                self.total_spent += size_usdc
                self.log.order_placed(trade, is_martingale=True)
            else:
                self.trade_1 = trade
                self.total_spent = size_usdc
                self.log.order_placed(trade, is_martingale=False)
            return True

        # --- Insufficient balance: stop retrying immediately ---
        if "not enough balance" in error_msg or "balance is not enough" in error_msg:
            self.log.warn(
                f"Insufficient balance to place ${size_usdc:.2f} order. "
                "Another market may have consumed the funds. Returning to IDLE."
            )
            self._set_state(State.IDLE)
            self._reset_context()
            return False

        self.log.order_failed(resp)
        return False

    def _execute_cash_out(self, trade, price):
        exact_shares = self.poly.get_exact_token_balance(trade.token_id)
        if exact_shares <= 0.0:
            self.log.warn(f"⚠ No on-chain balance available for global Cash Out.")
            self._set_state(State.IDLE)
            self._reset_context()
            return
            
        self.log.info(f"🔄 Executing Sell via CASH_OUT at {price:.3f} (Qty: {exact_shares:.4f} shares)...")
        resp = self.poly.sell(trade.token_id, price, exact_shares)
        if resp.get("success") or resp.get("status") in ("live", "matched"):
            self.log.info(f"✅ Cash Out CLOSED successfully. Returning to IDLE mode.")
            self._set_state(State.IDLE)
            self._reset_context()
        else:
            self.log.error(f"❌ Early Cash Out failed: {resp.get('errorMsg', resp)}")

    def _check_position_stop_loss(self, remaining_seconds: float):
        """Monitors open position for profit target, stop loss, or indecision."""
        is_trade_1 = self._state == State.IN_TRADE_1
        trade = self.trade_1 if is_trade_1 else (self.gales[-1] if self.gales else None)
        if not trade: return

        current_bid = self.poly.get_bid_price(trade.token_id)
        if current_bid <= 0.01: return

        current_value = current_bid * trade.shares
        pnl_pct = ((current_value / trade.size_usdc) - 1.0) * 100

        # --- Take Profit (Trade 1 only) ---
        if is_trade_1 and PROFIT_TARGET_PERCENT > 0 and pnl_pct >= PROFIT_TARGET_PERCENT:
            self.log.success(f"💰 Take Profit hit! (+{pnl_pct:.1f}%). Early Cash Out...")
            self._execute_cash_out(trade, current_bid)
            return

        # --- Indecision Exit (Last N seconds) ---
        if remaining_seconds <= INDECISION_EXIT_WINDOW_S:
            min_p, max_p = INDECISION_PRICE_RANGE
            if min_p <= current_bid <= max_p:
                self.log.warn(f"⚖ Market indecision detected ({current_bid:.3f}) in last {INDECISION_EXIT_WINDOW_S}s. Exiting early to preserve stake.")
                self._execute_cash_out(trade, current_bid)
                return

        # --- Stop Loss ---
        if STOP_LOSS_PERCENT > 0 and pnl_pct <= -STOP_LOSS_PERCENT:
            self.log.warn(f"🛑 Position Stop Loss hit! ({pnl_pct:.1f}%). Selling...")
            resp = self.poly.sell(trade.token_id, current_bid, trade.shares)
            if resp.get("success") or resp.get("status") in ("live", "matched"):
                self.log.success(f"✅ Stop Loss executed at {current_bid:.3f}.")
                # Proceed to the next level (Gale) as a normal but controlled loss
                if is_trade_1:
                    self.log.info("Proceeding to Martingale Gale 1...")
                    self.martingale_start = synced_time()
                    self._set_state(State.MARTINGALE_WAIT)
                elif self.gale_count < MAX_GALES:
                    self.log.info(f"Proceeding to Martingale Gale {self.gale_count+1}...")
                    self.martingale_start = synced_time()
                    self._set_state(State.MARTINGALE_WAIT)
                else:
                    self._set_state(State.IDLE)
                    self._reset_context()
            else:
                self.log.error(f"❌ Stop Loss Sell failed: {resp.get('errorMsg')}")


    # ---------------------------------------------------------------- #
    # Utilities                                                          #
    # ---------------------------------------------------------------- #

    def _calc_gale_size(self, prev_size: float) -> float:
        """
        Each gale = multiplier x previous gale size.
        Ex: entry=$5 → gale1=$10.5 → gale2=$22.05
        """
        return round(prev_size * MARTINGALE_MULTIPLIER, 2)

    def _refresh_tokens_if_needed(self, force: bool = False):
        now = synced_time()
        if force or (now - self._last_token_refresh > MARKET_REFRESH_INTERVAL):
            new_ids = self.poly.fetch_market_tokens(series_id=self._series_id)
            if new_ids:
                with self._lock:
                    self.token_ids = new_ids
                    self._last_token_refresh = now
                self.log.info(
                    f"Tokens refreshed — "
                    f"UP: {new_ids.get('UP', 'N/A')[:12]}...  "
                    f"DOWN: {new_ids.get('DOWN', 'N/A')[:12]}..."
                )
            else:
                self.log.warn("Could not refresh token IDs")

    def _reset_context(self):
        self.sequence_direction = None
        self.sequence_time      = None
        self.target_side        = None
        self.target_token_id    = None
        self.trade_1            = None
        self.gales              = []
        self.gale_count         = 0
        self.martingale_start   = None
        self.total_spent        = 0.0