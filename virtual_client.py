# =============================================================
# virtual_client.py — Virtual Polymarket Client for Simulation
# =============================================================

import time
import json
import os
from typing import Dict, Optional
from polymarket_client import PolymarketClient
from stats_manager import StatsManager

class VirtualPolymarketClient(PolymarketClient):
    """
    Mocks the PolymarketClient to allow risk-free simulations.
    Tracks a virtual balance and records trade history via StatsManager.
    """

    def __init__(self, logger=None, initial_balance: float = 1000.0):
        self.log = logger
        self.balance = initial_balance
        self.active_trades = {} # token_id -> shares
        
        # Stats Manager for the dashboard
        self.stats = StatsManager(initial_balance=initial_balance, mode="VIRTUAL")
        
        # Real methods we still need (Gamma API for tokens and prices is mostly public/GET)
        import requests
        self._session = requests.Session()
        from config import CLOB_HOST, GAMMA_API
        self.host = CLOB_HOST
        self.gamma_api = GAMMA_API
        self._price_cache = {}
        
        if self.log:
            self.log.info(f"Initialized Virtual Mode with ${initial_balance:.2f} balance.")

    def check_is_winner(self, token_id: str, timeout_seconds: int = 15) -> bool:
        """Simulates winning check (always returns True in simulation context)."""
        return True

    def check_allowance_and_approve(self, amount_usdc: float = 1000000.0):
        if self.log: self.log.info("Virtual Mode: Skipping allowance checks.")

    def get_balances(self) -> Dict[str, float]:
        return {"available": self.balance, "redeemable": 0.0}

    def start_background_cleanup(self):
        pass # No cleanup needed in virtual mode

    def buy(self, token_id: str, price: float, size_usdc: float, is_martingale: bool = False, market: str = "") -> dict:
        if size_usdc > self.balance:
            return {"success": False, "errorMsg": "not enough balance"}
        
        shares = size_usdc / price
        self.balance -= size_usdc
        self.active_trades[token_id] = self.active_trades.get(token_id, 0) + shares
        
        # Update stats
        self.stats.update_balance(self.balance)
        self.stats.record_event("MARTINGALE" if is_martingale else "BUY", {
            "market": market,
            "token_id": token_id,
            "price": price,
            "size_usdc": size_usdc,
            "shares": round(shares, 4)
        })
        
        return {
            "success": True,
            "orderID": f"v-{int(time.time()*1000)}",
            "status": "matched"
        }

    def sell(self, token_id: str, price: float, shares: float, market: str = "") -> dict:
        if token_id not in self.active_trades or self.active_trades[token_id] < shares:
            return {"success": False, "errorMsg": "not enough shares"}
        
        received = shares * price
        self.balance += received
        self.active_trades[token_id] -= shares
        
        # Update stats
        self.stats.update_balance(self.balance)
        self.stats.record_event("SELL", {
            "market": market,
            "token_id": token_id,
            "price": price,
            "received_usdc": round(received, 2),
            "shares": round(shares, 4)
        })
        
        return {
            "success": True,
            "status": "matched"
        }

    def register_win_for_settlement(self, trade_record, total_spent: float = 0, early_exit_price: float = 0, market: str = ""):
        if early_exit_price > 0:
            return # Already handled by sell()
            
        shares = getattr(trade_record, 'shares', 0)
        token_id = getattr(trade_record, 'token_id', None)
        
        payout = shares * 1.0
        self.balance += payout
        if token_id in self.active_trades:
            self.active_trades[token_id] -= shares
            
        # Update stats
        self.stats.update_balance(self.balance)
        self.stats.record_event("SETTLEMENT", {
            "market": market,
            "payout": round(payout, 2),
            "shares": round(shares, 4)
        })

    def update_max_gale(self, gale_count: int, total_spent: float):
        self.stats.update_max_gale(gale_count)

    def send_heartbeat(self, heartbeat_id: str = "") -> str:
        return "v-heartbeat"

    def get_open_orders(self) -> list:
        return []

    def cancel_all_orders(self):
        return {"success": True}
