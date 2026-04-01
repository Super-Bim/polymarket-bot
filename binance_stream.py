# =============================================================
# binance_stream.py — Binance WebSocket (BTC/USDT 5m candles)
# =============================================================

import json
import time
import threading
import websocket
from typing import Callable, List, Optional
from config import BINANCE_WS_URL, SEQUENCE_LENGTH


class Candle:
    """ candle 5 minutes BTC/USDT."""

    def __init__(self, k: dict):
        self.open_time  = k["t"]          # ms
        self.close_time = k["T"]          # ms
        self.open       = float(k["o"])
        self.high       = float(k["h"])
        self.low        = float(k["l"])
        self.close      = float(k["c"])
        self.volume     = float(k["v"])
        self.closed     = bool(k["x"])    # True when the candle is closed
        self.direction  = "UP" if self.close >= self.open else "DOWN"

    @property
    def remaining_seconds(self) -> float:
        """Seconds remaining until the candle closes."""
        return max(0.0, (self.close_time - time.time() * 1000) / 1000)

    def __repr__(self):
        return (
            f"Candle({self.direction} "
            f"O={self.open:.1f} C={self.close:.1f} "
            f"closed={self.closed})"
        )


class BinanceStream:
    """
    Connects to Binance WebSocket and processes 5-minute candles.

    Callbacks:
      on_sequence(direction, candles)  — N consecutive candles in the same direction
      on_candle_tick(candle)           — Tick of each forming candle
      on_candle_close(candle)          — Closed candle
    """

    def __init__(
        self,
        on_sequence: Callable,
        on_candle_tick: Callable,
        on_candle_close: Callable,
        logger=None,
    ):
        self.on_sequence    = on_sequence
        self.on_candle_tick = on_candle_tick
        self.on_candle_close = on_candle_close
        self.log            = logger

        self._closed_candles: List[Candle] = []
        self._current: Optional[Candle]    = None
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread]   = None
        self._running = False
        self._reconnect_delay = 3   # seconds

    # ------------------------------------------------------------------ #
    # Internal WebSocket Callbacks                                         #
    # ------------------------------------------------------------------ #

    def _on_message(self, ws, message: str):
        try:
            data = json.loads(message)
            if data.get("e") != "kline":
                return

            candle = Candle(data["k"])

            # Optional: Periodic Pulse (each 10 messages)
            # if getattr(self, '_msg_count', 0) % 20 == 0:
            #     pass 

            if candle.closed:
                self._closed_candles.append(candle)
                # Keep only the last SEQUENCE_LENGTH + 2 candles
                if len(self._closed_candles) > SEQUENCE_LENGTH + 2:
                    self._closed_candles.pop(0)

                self.on_candle_close(candle)
                self._check_sequence()
            else:
                self._current = candle
                self.on_candle_tick(candle)

        except Exception as e:
            if self.log:
                self.log.error(f"[Binance] Error processing message: {e}")

    def _on_error(self, ws, error):
        if self.log:
            self.log.error(f"[Binance] WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        if self._running:
            if self.log:
                self.log.warn(f"[Binance] Connection closed. Reconnecting in {self._reconnect_delay}s...")
            time.sleep(self._reconnect_delay)
            self._reconnect()

    def _on_open(self, ws):
        if self.log:
            self.log.success("[Binance] Connected to stream BTCUSDT@kline_5m")

    # ------------------------------------------------------------------ #
    # Sequence detection                                                   #
    # ------------------------------------------------------------------ #

    def _check_sequence(self):
        """Checks if the last N closed candles are in the same direction."""
        if len(self._closed_candles) < SEQUENCE_LENGTH:
            return

        last_n = self._closed_candles[-SEQUENCE_LENGTH:]
        directions = [c.direction for c in last_n]

        if len(set(directions)) == 1:
            self.on_sequence(directions[0], last_n)

    # ------------------------------------------------------------------ #
    # Stream control                                                       #
    # ------------------------------------------------------------------ #

    def _connect(self):
        self._ws = websocket.WebSocketApp(
            BINANCE_WS_URL,
            on_message  = self._on_message,
            on_error    = self._on_error,
            on_close    = self._on_close,
            on_open     = self._on_open,
        )
        self._ws.run_forever(ping_interval=30, ping_timeout=10)

    def _reconnect(self):
        if self._running:
            self._connect()

    def _preload_history(self):
        """Fetches the latest closed candles via REST API to avoid starting 'blind'."""
        try:
            import requests
            if self.log:
                self.log.info("[Binance] Downloading recent history to speed up entries...")
            
            # Get the previous 4 candles (limit=5, ignores the last one which is open)
            res = requests.get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=5", timeout=10)
            klines = res.json()
            
            # The last kline returned by the REST API is usually the CURRENT forming candle.
            # We iterate only through the closed ones (all except the last).
            for k in klines[:-1]:
                simulated_k = {
                    "t": k[0],
                    "T": k[6],
                    "o": k[1],
                    "h": k[2],
                    "l": k[3],
                    "c": k[4],
                    "v": k[5],
                    "x": True
                }
                c = Candle(simulated_k)
                self._closed_candles.append(c)
                if self.log:
                    # Logging to give visual feedback of the preloading
                    self.log.candle_close(c)
            
            # Check if sequence is already hit on boot
            self._check_sequence()
        except Exception as e:
            if self.log:
                self.log.warn(f"[Binance] Failed to preload history: {e}")

    def start(self):
        """Starts the background stream."""
        self._preload_history()
        self._running = True
        self._thread  = threading.Thread(target=self._connect, daemon=True)
        self._thread.start()

    def stop(self):
        """Stops the stream."""
        self._running = False
        if self._ws:
            self._ws.close()

    @property
    def current_candle(self) -> Optional[Candle]:
        return self._current

    @property
    def last_closed_candles(self) -> List[Candle]:
        return list(self._closed_candles)
