import time
import requests

# Global offset (Binance time - Local time)
_offset = 0.0

def sync_with_binance():
    """
    Fetches the Binance server time and calculates the offset 
    against the local system clock.
    """
    global _offset
    try:
        # Fetch server time in milliseconds
        resp = requests.get("https://api.binance.com/api/v3/time", timeout=5)
        resp.raise_for_status()
        server_ms = resp.json()["serverTime"]
        server_sec = server_ms / 1000.0
        
        local_sec = time.time()
        _offset = server_sec - local_sec
        
        return _offset
    except Exception:
        # Fallback if request fails
        _offset = 0.0
        return 0.0

def synced_time():
    """Returns the current time (float) synchronized with Binance."""
    return time.time() + _offset

def get_offset():
    return _offset
