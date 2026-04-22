# =============================================================
# stats_manager.py — Session Statistics & Dashboard Generator
# =============================================================

import time
import json
import os
from typing import Dict, List
from time_utils import synced_time
from virtual_dashboard import generate_dashboard

class StatsManager:
    """
    Manages session statistics, tracking balance over time, 
    calculating PnL, Drawdown, and generating the HTML dashboard.
    """

    def __init__(self, initial_balance: float, mode: str = "VIRTUAL"):
        self.mode = mode.upper()
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.peak_balance = initial_balance
        self.max_drawdown = 0.0
        self.max_gale_reached = 0
        self.start_time = synced_time()
        self.history: List[Dict] = []
        
        # Load previous history if exists for this mode (optional, for now we start fresh per session)
        self.stats_file = f"{self.mode.lower()}_stats.json"
        
        if os.path.exists(self.stats_file):
            try:
                # Optionally load previous session stats? 
                # For now, we prefer fresh session starts as per bot logic.
                pass
            except Exception: pass

    def update_balance(self, new_balance: float):
        self.balance = new_balance
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        
        current_drawdown = self.peak_balance - self.balance
        if current_drawdown > self.max_drawdown:
            self.max_drawdown = current_drawdown
        
        self.save()

    def record_event(self, event_type: str, data: Dict):
        """Records a trade or milestone in the history."""
        event = {
            "type": event_type.upper(),
            "timestamp": synced_time(),
            "balance_after": round(self.balance, 2),
            **data
        }
        self.history.append(event)
        self.save()

    def update_max_gale(self, gale_count: int):
        if gale_count > self.max_gale_reached:
            self.max_gale_reached = gale_count
            self.save()

    def save(self):
        """Calculates current stats and triggers dashboard generation."""
        elapsed_hours = (synced_time() - self.start_time) / 3600
        pnl = self.balance - self.initial_balance
        avg_pnl_hour = pnl / elapsed_hours if elapsed_hours > 0 else 0
        
        stats = {
            "mode": self.mode,
            "current_balance": round(self.balance, 2),
            "initial_balance": self.initial_balance,
            "pnl": round(pnl, 2),
            "pnl_percent": round((pnl / self.initial_balance) * 100, 2) if self.initial_balance > 0 else 0,
            "elapsed_hours": round(elapsed_hours, 2),
            "avg_pnl_hour": round(avg_pnl_hour, 2),
            "max_gale": self.max_gale_reached,
            "capital_required": round(self.max_drawdown, 2),
            "history": self.history[-100:] # Last 100 events
        }
        
        try:
            with open(self.stats_file, "w") as f:
                json.dump(stats, f, indent=4)
            
            # Generate the dashboard HTML
            generate_dashboard(stats)
        except Exception:
            pass
