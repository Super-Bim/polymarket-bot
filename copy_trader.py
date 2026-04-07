# =============================================================
# copy_trader.py — Copy Trade Logic via RPC Polling
# =============================================================

import time
import threading
from typing import Dict, Optional

from config import (
    BASE_TRADE_SIZE_USDC,
    COPY_TRADE_POLL_INTERVAL,
    PROFIT_TARGET_PERCENT,
    MARKET_REFRESH_INTERVAL,
)
from strategy import Trade
from time_utils import synced_time


class CopyTrader:
    def __init__(self, target_wallet: str, poly_client, logger):
        self.target_wallet = target_wallet
        self.poly = poly_client
        self.log = logger
        self._shutdown_event = threading.Event()
        
        self.token_ids: Dict[str, str] = {}
        self._last_token_refresh: float = 0
        
        self.current_trade: Optional[Trade] = None
        self._heartbeat_id = ""

    def start(self):
        self.log.info(f"Starting Copy Trade target: {self.target_wallet}...")
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()
        
    def stop(self):
        self._shutdown_event.set()
        if hasattr(self, 't'):
            self.t.join(timeout=2)
            
    def _refresh_tokens_if_needed(self):
        now = synced_time()
        if now - self._last_token_refresh > MARKET_REFRESH_INTERVAL:
            new_ids = self.poly.fetch_market_tokens()
            if new_ids:
                self.token_ids = new_ids
                self._last_token_refresh = now
                self.log.info(
                    f"[CopyTrade] Market polled. Tokens: "
                    f"UP {new_ids.get('UP', 'N/A')[:6]}... | "
                    f"DOWN {new_ids.get('DOWN', 'N/A')[:6]}..."
                )
            else:
                self.log.warn("[CopyTrade] Failed to get active tokens.")

    def _run(self):
        w3 = self.poly._get_w3()
        if not w3:
            self.log.error("[CopyTrade] Fatal Error: Cannot connect to web3 RPC.")
            return

        ctf_address = w3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
        abi = [{"constant":True,"inputs":[{"name":"account","type":"address"},{"name":"id","type":"uint256"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]
        contract = w3.eth.contract(address=ctf_address, abi=abi)
        
        try:
            target_addr = w3.to_checksum_address(self.target_wallet)
        except Exception:
            self.log.error("[CopyTrade] Error: Specified target wallet is not a valid Ethereum/Polygon address.")
            return

        while not self._shutdown_event.is_set():
            try:
                self._heartbeat_id = self.poly.send_heartbeat(self._heartbeat_id)
                self._refresh_tokens_if_needed()
                
                up_id = self.token_ids.get("UP")
                down_id = self.token_ids.get("DOWN")

                if not up_id or not down_id:
                    self._shutdown_event.wait(timeout=2.0)
                    continue

                # Query balance on-chain
                bal_up = contract.functions.balanceOf(target_addr, int(up_id)).call()
                bal_down = contract.functions.balanceOf(target_addr, int(down_id)).call()

                self._process_state(bal_up > 0, bal_down > 0, up_id, down_id)

            except Exception as e:
                self.log.error(f"[CopyTrade] Polling loop error: {e}")
            
            self._shutdown_event.wait(timeout=COPY_TRADE_POLL_INTERVAL)

    def _process_state(self, has_up: bool, has_down: bool, up_id: str, down_id: str):
        # 1. If we are flat and target bought something
        if not self.current_trade:
            if has_up and not has_down:
                self._buy("UP", up_id)
            elif has_down and not has_up:
                self._buy("DOWN", down_id)
            return

        # 2. If we're already in a trade, monitor Cash Out OR target exiting
        if self.current_trade:
            # Check if market has changed (new token ids).
            # If current tokens differ from the ones we bought, round has resolved.
            is_old_market = (self.current_trade.token_id not in [up_id, down_id])
            if is_old_market:
                self.log.info(f"[CopyTrade] Market resolved while holding order. Delegating to cleanup worker.")
                self.current_trade = None
                return

            # Check if target sold (exited position)
            target_sold = False
            if self.current_trade.side == "UP" and not has_up: target_sold = True
            elif self.current_trade.side == "DOWN" and not has_down: target_sold = True

            if target_sold:
                self.log.info("[CopyTrade] Target seems to have CLOSED position. Replicating exit!")
                self._sell("COPY_EXIT")
                return

            # Check Cash Out (Take Profit)
            if PROFIT_TARGET_PERCENT > 0:
                current_price = self.poly.get_bid_price(self.current_trade.token_id)
                if current_price > 0.01:
                    current_value = current_price * self.current_trade.shares
                    profit_pct = ((current_value / self.current_trade.size_usdc) - 1.0) * 100
                    
                    if profit_pct >= PROFIT_TARGET_PERCENT:
                        self.log.info(f"[CopyTrade] Target profit reached! (+{profit_pct:.2f}%). Executing early Cash Out!")
                        self._sell("CASH_OUT")
                        return

    def _buy(self, side: str, token_id: str):
        self.log.info(f"🎯 [CopyTrade] DETECTED TARGET ENTRY IN {side}! Executing Buy.")
        price = self.poly.get_ask_price(token_id)
        if price <= 0.0 or price > 0.95:
            self.log.warn(f"[CopyTrade] Invalid price at the moment ({price}). Skipping copy.")
            return

        resp = self.poly.buy(token_id, price, BASE_TRADE_SIZE_USDC)
        if resp.get("success") or resp.get("status") in ("live", "matched", "delayed"):
            shares = round(BASE_TRADE_SIZE_USDC / price, 4)
            self.current_trade = Trade(
                token_id=token_id,
                side=side,
                entry_price=price,
                size_usdc=BASE_TRADE_SIZE_USDC,
                shares=shares,
                order_id=resp.get("orderID", ""),
                condition_id=self.token_ids.get("condition_id", ""),
            )
            self.log.info(f"✅ [CopyTrade] ENTRY COPIED! Bought: {side} | Price: {price:.3f} | Size: ${BASE_TRADE_SIZE_USDC}")
        else:
            self.log.error(f"❌ [CopyTrade] Failed to copy entry order: {resp.get('errorMsg', resp)}")

    def _sell(self, reason: str):
        if not self.current_trade: return
        if price > 0.0:
            exact_shares = self.poly.get_exact_token_balance(trade.token_id)
            if exact_shares <= 0.0:
                self.log.warn(f"⚠ [CopyTrade] Not enough on-chain balance to sell. Aborting.")
                self.current_trade = None
                return

            self.log.info(f"🔄 [CopyTrade] Executing Sell via {reason} at {price:.3f} (Qty: {exact_shares:.4f} shares)...")
            resp = self.poly.sell(trade.token_id, price, exact_shares)
            if resp.get("success") or resp.get("status") in ("live", "matched"):
                self.log.info(f"✅ [CopyTrade] Position CLOSED successfully ({reason}). Waiting for next target entry.")
                self.current_trade = None
            else:
                self.log.error(f"❌ [CopyTrade] Early Sell failed: {resp.get('errorMsg', resp)}")
                # Do not clear trade here. Let polling loop retry!
        else:
             self.log.warn(f"⚠ [CopyTrade] Failed to get sell quote for Cash Out. Retrying next cycle.")
