# =============================================================
# polymarket_client.py — CLOB + Gamma API Wrapper
# =============================================================

import os
import time
import socket
import requests
from typing import Optional, Dict

# ------------------------------------------------------------------
# DNS Resilience — Cached Cloudflare IPs for Polymarket endpoints.
# Acts as a fallback when system DNS fails to resolve Polymarket
# domains (e.g. restrictive ISPs, corporate firewalls, VPN configs).
# These are Polymarket's own public Cloudflare CDN addresses.
# ------------------------------------------------------------------
_POLYMARKET_IPS = {
    "gamma-api.polymarket.com": "172.64.153.51",
    "clob.polymarket.com":      "172.64.153.51",
    "data-api.polymarket.com":  "172.64.153.51",
    "strapi-matic.poly.market": "172.64.153.51",
    "api.polymarket.com":       "172.64.153.51",
}

_orig_getaddrinfo = socket.getaddrinfo

def _patched_getaddrinfo(host, port, *args, **kwargs):
    if host in _POLYMARKET_IPS:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (_POLYMARKET_IPS[host], port))]
    return _orig_getaddrinfo(host, port, *args, **kwargs)

socket.getaddrinfo = _patched_getaddrinfo
# ------------------------------------------------------------------

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, OrderArgs, MarketOrderArgs, OrderType
from py_clob_client_v2.order_builder.constants import BUY, SELL

from eth_account import Account
from dotenv import set_key
from config import CLOB_HOST, GAMMA_API, CHAIN_ID
from stats_manager import StatsManager


class PolymarketClient:
    """
    Interface with Polymarket CLOB and Gamma API.
    Supports proxy wallets (signature_type=2 — email/Google account).
    """

    def __init__(self, logger=None):
        self.log        = logger
        self.host       = CLOB_HOST
        self.gamma_api  = GAMMA_API
        self._session   = requests.Session()
        self._price_cache = {} # Cache for Gamma prices
        self._session.headers.update({
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent":      (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Origin":          "https://polymarket.com",
            "Referer":         "https://polymarket.com/",
        })

        # Load environment credentials
        self.pk         = os.getenv("PRIVATE_KEY")
        self.sig_type   = int(os.getenv("SIGNATURE_TYPE", "2"))
        self.funder     = os.getenv("FUNDER_ADDRESS")
        api_key         = os.getenv("POLY_API_KEY", "")
        api_secret      = os.getenv("POLY_API_SECRET", "")
        api_passphrase  = os.getenv("POLY_API_PASSPHRASE", "")
        self.creds      = None

        # Validate essential credentials
        if not self.pk or self.pk in ["0xYOUR_PRIVATE_KEY_HERE"]:
            raise ValueError("PRIVATE_KEY not configured in .env")

        # Derive or validate funder address
        derived_funder = False
        # Treat empty string or placeholder as missing
        if not self.funder or self.funder.strip() in ["", "0xYOUR_PROXY_WALLET_HERE", "0xYOUR_ADDRESS_HERE"]:
            if self.sig_type == 0:  # EOA
                self.funder = Account.from_key(self.pk).address
                derived_funder = True
                if self.log:
                    self.log.info(f"Auto-derived address from PK: {self.funder[:10]}...")
            else:
                raise ValueError(f"FUNDER_ADDRESS must be provided for SIGNATURE_TYPE {self.sig_type} (Proxy/Email)")

        # Create client without L2 creds first (to generate if necessary)
        self._l1_client = ClobClient(
            host            = self.host,
            key             = self.pk,
            chain_id        = CHAIN_ID,
            signature_type  = self.sig_type,
            funder          = self.funder,
            retry_on_error  = True
        )

        # Derive or use L2 credentials
        new_creds_generated = False
        if api_key and api_secret and api_passphrase:
            self.creds = ApiCreds(
                api_key        = api_key,
                api_secret     = api_secret,
                api_passphrase = api_passphrase,
            )
        else:
            if self.log:
                self.log.info("Generating Polymarket API credentials (first run)...")
            self.creds = self._l1_client.create_or_derive_api_key()
            new_creds_generated = True

        # Persistence: Update .env if something was derived or generated
        if derived_funder or new_creds_generated:
            self._update_env_file(self.funder, self.creds)

        # Recreate client with L2 creds
        self._client = ClobClient(
            host            = self.host,
            key             = self.pk,
            chain_id        = CHAIN_ID,
            creds           = self.creds,
            signature_type  = self.sig_type,
            funder          = self.funder,
            retry_on_error  = True
        )

        # Session Stats for Real Mode Dashboard
        self.stats = None
        self._initial_balance_set = False
        self._redeem_queue = {}

    # ------------------------------------------------------------------ #
    # Web3 & RPC Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _get_w3(self):
        """Returns a connected Web3 instance using a fallback RPC list."""
        from web3 import Web3
        rpcs = [
            "https://polygon-rpc.com",
            "https://rpc-mainnet.maticvigil.com",
            "https://polygon-bor-rpc.publicnode.com",
            "https://1rpc.io/matic"
        ]
        for url in rpcs:
            try:
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs={'timeout': 10}))
                if w3.is_connected():
                    return w3
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------ #
    # Allowance & Balance Checks                                           #
    # ------------------------------------------------------------------ #

    def check_allowance_and_approve(self, amount_usdc: float = 1_000_000.0):
        """Checks USDC.e and pUSD allowance and performs automatic wrapping/approval if necessary."""
        v2_exchange = "0xe111180000d2663c0091e4f400237545b87b996b"
        onramp      = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
        pusd        = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
        usdc_e      = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        
        try:
            w3 = self._get_w3()
            if not w3:
                if self.log: self.log.error("[Polymarket] Failed to connect to any Polygon RPC. Check your internet connection.")
                return

            account = w3.eth.account.from_key(self.pk)
            funder  = self.funder
            
            abi = [
                {"constant":True,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
                {"constant":False,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
                {"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}
            ]
            
            contract = w3.eth.contract(address=w3.to_checksum_address(usdc_e), abi=abi)
            
            # --- 1. Check PolyUSD (pUSD) Balance ---
            pusd_contract = w3.eth.contract(address=w3.to_checksum_address(pusd), abi=abi)
            pusd_balance_wei = pusd_contract.functions.balanceOf(w3.to_checksum_address(funder)).call()
            pusd_balance = pusd_balance_wei / 1_000_000
            
            # --- 2. Check USDC.e Balance for Onramp ---
            usdc_contract = w3.eth.contract(address=w3.to_checksum_address(usdc_e), abi=abi)
            usdc_balance_wei = usdc_contract.functions.balanceOf(w3.to_checksum_address(funder)).call()
            usdc_balance = usdc_balance_wei / 1_000_000
            
            if self.log:
                self.log.info(f"💰 [Polymarket] Balances: {pusd_balance:.2f} pUSD | {usdc_balance:.2f} USDC.e")

            # --- 3. Automatic Wrapping (USDC.e -> pUSD) ---
            if usdc_balance >= 1.0:
                self.auto_wrap_usdc_to_pusd()
                # Refresh balances after wrap
                pusd_balance_wei = pusd_contract.functions.balanceOf(w3.to_checksum_address(funder)).call()
                pusd_balance = pusd_balance_wei / 1_000_000
                usdc_balance = 0.0 # Approximate
            
            # --- 4. Diagnostics ---
            if pusd_balance < 1.0 and usdc_balance < 1.0:
                if self.log: self.log.error(f"❌ [Polymarket] Low collateral balance. Add USDC to your wallet.")

            # Initialize stats with pUSD balance
            if not self._initial_balance_set:
                self.stats = StatsManager(initial_balance=pusd_balance, mode="LIVE")
                self._initial_balance_set = True
            else:
                self.stats.update_balance(pusd_balance)

            # --- 5. Check pUSD Allowance for V2 Exchange ---
            current_allowance = pusd_contract.functions.allowance(
                w3.to_checksum_address(funder), 
                w3.to_checksum_address(v2_exchange)
            ).call()
            
            required_wei = int(amount_usdc * 1_000_000)
            
            if current_allowance < required_wei:
                if self.log:
                    self.log.warn(f"⚠ [Polymarket] Authorizing V2 Exchange {v2_exchange[:10]}...")
                
                tx_func = pusd_contract.functions.approve(
                    w3.to_checksum_address(v2_exchange), 
                    2**256 - 1
                )
                tx_params = self._build_tx_params(w3, account.address, tx_func)
                
                signed_tx = w3.eth.account.sign_transaction(tx, private_key=self.pk)
                tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
                
                if self.log:
                    self.log.info(f"✅ [Polymarket] Approval transaction sent! TX: {w3.to_hex(tx_hash)}")
                    self.log.info("Waiting for confirmation (usually 10-20s)...")
                
                # Wait for confirmation
                w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                if self.log: self.log.info("✅ [Polymarket] Spender authorized successfully.")
            else:
                if self.log:
                    self.log.info(f"✅ [Polymarket] Spender authorized (Allowance: {current_allowance/1e6:,.0f} USDC).")
            
            # --- Check CTF ERC1155 Allowance (for Selling) ---
            ctf_addr = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
            ctf_abi = [
                {"constant":True,"inputs":[{"name":"account","type":"address"},{"name":"operator","type":"address"}],"name":"isApprovedForAll","outputs":[{"name":"","type":"bool"}],"type":"function"},
                {"constant":False,"inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],"name":"setApprovalForAll","outputs":[],"type":"function"}
            ]
            ctf_contract = w3.eth.contract(address=w3.to_checksum_address(ctf_addr), abi=ctf_abi)
            is_approved = ctf_contract.functions.isApprovedForAll(
                w3.to_checksum_address(funder),
                w3.to_checksum_address(v2_exchange)
            ).call()

            if not is_approved:
                if self.log: self.log.warn(f"⚠ [Polymarket] CTF V2 Exchange Not Approved. Authorizing for Sells...")
                matic_balance = w3.eth.get_balance(account.address)
                if matic_balance < w3.to_wei(0.01, 'ether'):
                    if self.log: self.log.error("[Polymarket] Insufficient MATIC for CTF approval!")
                    return
                tx_func = ctf_contract.functions.setApprovalForAll(
                    w3.to_checksum_address(v2_exchange),
                    True
                )
                tx_params = self._build_tx_params(w3, account.address, tx_func)
                signed_tx2 = w3.eth.account.sign_transaction(tx_params, self.pk)
                tx_hash2 = w3.eth.send_raw_transaction(signed_tx2.raw_transaction)
                if self.log: self.log.info(f"✅ [Polymarket] CTF Approval sent! TX: {w3.to_hex(tx_hash2)}")
                w3.eth.wait_for_transaction_receipt(tx_hash2, timeout=120)
                if self.log: self.log.info("✅ [Polymarket] CTF shares authorized for exchange successfully.")
            else:
                if self.log: self.log.info(f"✅ [Polymarket] CTF shares authorized for exchange.")

        except Exception as e:
            if self.log:
                self.log.error(f"[Polymarket] Error during allowance check/approval: {e}")

    def update_max_gale(self, gale_count: int, total_spent: float):
        """Updates the maximum martingale level reached in the current session stats."""
        if self.stats:
            self.stats.update_max_gale(gale_count)

    def get_exact_token_balance(self, token_id: str) -> float:
        """Fetch exact micro-shares balance for an event token on Polygon."""
        try:
            w3 = self._get_w3()
            if not w3: return 0.0
            ctf_address = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
            abi = [{"constant":True,"inputs":[{"name":"account","type":"address"},{"name":"id","type":"uint256"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]
            contract = w3.eth.contract(address=w3.to_checksum_address(ctf_address), abi=abi)
            bal_wei = contract.functions.balanceOf(
                w3.to_checksum_address(self.funder), 
                int(token_id)
            ).call()
            return bal_wei / 1_000_000
        except Exception:
            return 0.0

    def get_balances(self) -> Dict[str, float]:
        """
        Queries the Polymarket Data API to get the current wallet balance 
        and the total 'redeemable' (unclaimed) winnings.
        """
        funder = self.funder
        pusd   = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
        
        balances = {"available": 0.0, "redeemable": 0.0}
        
        try:
            w3 = self._get_w3()
            if not w3: return balances
            
            abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
            pusd_contract = w3.eth.contract(address=w3.to_checksum_address(pusd), abi=abi)
            balances["available"] = pusd_contract.functions.balanceOf(w3.to_checksum_address(funder)).call() / 1_000_000

            # 2. Redeemable balance (Data API)
            resp = self._session.get(
                f"https://data-api.polymarket.com/positions",
                params={"user": funder},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                for pos in data:
                    if pos.get("redeemable") is True:
                        # Value of winning positions is approx. size * 1.0
                        shares = float(pos.get("size", 0))
                        balances["redeemable"] += shares
            
            return balances
        except Exception:
            return balances

    def start_background_cleanup(self):
        """Dispatches a silent thread to clean up all old winnings while the bot operates."""
        import threading
        t = threading.Thread(target=self._cleanup_worker, daemon=True)
        t.start()

    def _cleanup_worker(self):
        """Continuously checks for redeemable positions every 5 minutes and executes on-chain claims."""
        funder = self.funder
        while True:
            try:
                resp = self._session.get(
                    f"https://data-api.polymarket.com/positions",
                    params={"user": funder},
                    timeout=10
                )
                if resp.status_code == 200:
                    data = resp.json()
                    redeemables = [p.get("conditionId") for p in data if p.get("redeemable") is True and p.get("conditionId")]
                    
                    # Deduplicate
                    unique_cids = list(set(redeemables))
                    
                    for cid in unique_cids:
                        self.redeem_shares(cid)
                        time.sleep(3.0) 
            except Exception:
                pass
            time.sleep(300) # Loop every 5 min

    def _update_env_file(self, funder: str, creds: ApiCreds):
        """Persists derived/generated credentials to the .env file."""
        try:
            env_path = ".env"
            if not os.path.exists(env_path):
                # If .env doesn't exist (though it should), try creating it from example or just raw
                if os.path.exists(".env.example"):
                    import shutil
                    shutil.copy(".env.example", ".env")
                else:
                    with open(env_path, "w") as f: f.write("")

            set_key(env_path, "FUNDER_ADDRESS", funder)
            set_key(env_path, "POLY_API_KEY", creds.api_key)
            set_key(env_path, "POLY_API_SECRET", creds.api_secret)
            set_key(env_path, "POLY_API_PASSPHRASE", creds.api_passphrase)

            # Update current process environment to avoid stale data in os.getenv calls
            os.environ["FUNDER_ADDRESS"]     = funder
            os.environ["POLY_API_KEY"]        = creds.api_key
            os.environ["POLY_API_SECRET"]     = creds.api_secret
            os.environ["POLY_API_PASSPHRASE"] = creds.api_passphrase

            if self.log:
                self.log.info("✅ Credentials saved to .env automatically.")
        except Exception as e:
            if self.log:
                self.log.error(f"Failed to auto-update .env file: {e}")

    def _add_to_redeem_queue(self, condition_id: str):
        if not condition_id: return
        self._redeem_queue[condition_id] = True

    def reconstruct_queue_from_history(self):
        """Scans Data API for positions with balance and populates the redemption queue."""
        if self.log: self.log.info("🔍 Scanning for positions with balance via Data API...")
        try:
            resp = self._session.get(f"https://data-api.polymarket.com/positions", params={"user": self.funder}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                found_any = False
                for pos in data:
                    size   = float(pos.get("size", 0))
                    cid    = pos.get("conditionId")
                    if size > 0.001 and cid:
                        found_any = True
                        if self.log: self.log.info(f"💎 [DATA_HIT] Found {size:.2f} shares in {cid[:10]}...")
                        self._add_to_redeem_queue(cid)
                
                if not found_any and self.log:
                    self.log.info("ℹ No positions with balance found in history.")
        except Exception as e:
            if self.log: self.log.error(f"❌ Failed to scan history: {e}")

    def rescue_open_orders(self):
        """Cancels all open and conditional orders."""
        if self.log: self.log.warn("🧹 Rescuing all open and conditional orders...")
        try:
            self._client.cancel_all()
            if self.log: self.log.info(f"✅ Limit orders cancelled.")
            
            try:
                self._client.cancel_all_conditional_orders()
                if self.log: self.log.success("✅ All conditional orders cleared.")
            except: pass
            
            if self.log: self.log.success("✔ Order rescue completed.")
        except Exception as e:
            if self.log: self.log.error(f"❌ Failed to rescue orders: {e}")

    # ------------------------------------------------------------------ #
    # Active Market Discovery                                              #
    # ------------------------------------------------------------------ #

    def fetch_market_tokens(self, series_id: str = "") -> Dict[str, str]:
        """
        Fetches the active btc-updown-5m event using the series ID.
        """
        import json as _json

        # Retry logic for network resilience
        for attempt in range(3):
            try:
                resp = self._session.get(
                    f"{self.gamma_api}/events",
                    params={
                        "series_id": series_id, 
                        "active": "true", 
                        "closed": "false",
                        "limit": "100"
                    },
                    timeout=20,
                )
                resp.raise_for_status()
                events = resp.json()
                break # Success
            except Exception as e:
                if attempt == 2: # Last attempt
                    if self.log: self.log.error(f"[Polymarket] Final attempt failed fetching tokens for series {series_id}: {e}")
                    return {}
                time.sleep(2)

        if not events:
            if self.log:
                self.log.error(f"[Polymarket] No active event found for series {series_id}")
            return {}

        # Filter events that are actually active and not closed
        active_events = [e for e in events if e.get("active") and not e.get("closed")]
        if not active_events:
            if self.log:
                self.log.error(f"[Polymarket] Events found, but none active/open for series {series_id}")
            return {}

        # Discover the best active market for "reversal"
        # It should be the one ending in about 1-5 minutes
        now_sec = time.time()
        valid_events = []
        for e in active_events:
            # endDate is the full ISO timestamp, endDateIso is often just the date
            end_iso = e.get("endDate") or e.get("endDateIso")
            if end_iso:
                # Convert ISO to timestamp (handling Z or +00)
                from datetime import datetime, timezone
                try:
                    # If it's just a date, fromisoformat might fail or return start of day
                    if len(end_iso) <= 10: # YYYY-MM-DD
                         dt = datetime.fromisoformat(end_iso).replace(tzinfo=timezone.utc)
                    else:
                         dt = datetime.fromisoformat(end_iso.replace('Z', '+00:00'))
                    end_ts = dt.timestamp()
                except: end_ts = 0
                
                # We want an event that ends at least 20s in the future
                if end_ts > now_sec + 20:
                    valid_events.append((end_ts, e))

        if valid_events:
            # Pick the one that ends soonest (the current/next 5m window)
            valid_events.sort(key=lambda x: x[0])
            event = valid_events[0][1]
        else:
            # Fallback to the last one if none found in future (might be just about to end)
            event = active_events[-1]
        
        if self.log:
            self.log.info(f"[Polymarket] Active event: {event.get('ticker', 'Unknown')}")

        markets = event.get("markets", [])
        if not markets:
            return {}

        market     = markets[0]
        slug       = market.get("slug", "")
        
        # 🛡️ SECONDARY FETCH for real-time prices (Essential for Virtual Mode and V2/NegRisk)
        outcomes_data = market.get("outcomes", [])
        clob_ids      = market.get("clobTokenIds", [])
        prices        = market.get("outcomePrices", [])

        if slug:
            try:
                s_resp = self._session.get(f"{self.gamma_api}/markets", params={"slug": slug}, timeout=10)
                if s_resp.status_code == 200:
                    m_data = s_resp.json()
                    m_obj = m_data[0] if isinstance(m_data, list) and len(m_data) > 0 else m_data
                    outcomes_data = m_obj.get("outcomes", outcomes_data)
                    prices = m_obj.get("outcomePrices", prices)
            except: pass

        if isinstance(outcomes_data, str): 
            try: outcomes_data = _json.loads(outcomes_data)
            except: outcomes_data = []
            
        if isinstance(clob_ids, str): 
            try: clob_ids = _json.loads(clob_ids)
            except: clob_ids = []
            
        if isinstance(prices, str): 
            try: prices = _json.loads(prices)
            except: prices = []

        token_ids: Dict[str, str] = {}
        for i, item in enumerate(outcomes_data):
            # Extract name and price
            if isinstance(item, dict):
                ou_name = item.get("name", "").strip().upper()
                t_id    = item.get("clobTokenId")
                p_val   = float(item.get("price") or item.get("lastTradePrice") or 0.0)
            else:
                ou_name = str(item).strip().upper()
                t_id    = clob_ids[i] if i < len(clob_ids) else None
                p_val   = float(prices[i]) if i < len(prices) else 0.0
            
            if t_id:
                t_id = str(t_id)
                self._price_cache[t_id] = p_val
                if any(x in ou_name for x in ["UP", "HIGHER", "YES", "OVER"]): token_ids["UP"] = t_id
                elif any(x in ou_name for x in ["DOWN", "LOWER", "NO", "UNDER"]): token_ids["DOWN"] = t_id

        # Store conditionId for settlement
        token_ids["condition_id"] = market.get("conditionId", "")
        token_ids["market_ticker"] = event.get("ticker", "Unknown")
        return token_ids


    def check_is_winner(self, token_id: str, timeout_seconds: int = 15) -> Optional[bool]:
        """
        Polls the Gamma API to verify if the token_id settled as winner.
        """
        start = time.time()
        import json as _json
        while time.time() - start < timeout_seconds:
            try:
                resp = self._session.get(
                    f"{self.gamma_api}/markets",
                    params={"clob_token_ids": token_id, "ts": int(time.time() * 1000)},
                    timeout=5,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        market = data[0]
                        if market.get("closed") or not market.get("active"):
                            clob_ids = market.get("clobTokenIds", "[]")
                            if isinstance(clob_ids, str):
                                try: clob_ids = _json.loads(clob_ids)
                                except: clob_ids = []
                            
                            prices = market.get("outcomePrices", "[]")
                            if isinstance(prices, str):
                                try: prices = _json.loads(prices)
                                except: prices = []
                                
                            if isinstance(clob_ids, list) and isinstance(prices, list):
                                try:
                                    idx = clob_ids.index(token_id)
                                    p = float(prices[idx])
                                    if p == 1.0: return True
                                    elif p == 0.0: return False
                                except ValueError: pass
            except Exception: pass
            time.sleep(2.0)
        return None

    # ------------------------------------------------------------------ #
    # Prices                                                               #
    # ------------------------------------------------------------------ #

    def get_ask_price(self, token_id: str) -> float:
        if not token_id: return 0.0
        return self._get_price(token_id, "sell")

    def get_bid_price(self, token_id: str) -> float:
        if not token_id: return 0.0
        return self._get_price(token_id, "buy")

    def get_midpoint(self, token_id: str) -> float:
        if not token_id: return 0.0
        try:
            if hasattr(self, '_client') and self._client:
                resp = self._client.get_midpoint(token_id)
                if isinstance(resp, dict): return float(resp.get("mid", 0))
                return float(resp if resp is not None else 0.0)
            return self._price_cache.get(token_id, 0.0)
        except:
            return self._price_cache.get(token_id, 0.0)

    def _get_price(self, token_id: str, side: str) -> float:
        """Fetches price from CLOB or fallback cache (Gamma)."""
        try:
            if hasattr(self, '_client') and self._client:
                resp = self._client.get_price(token_id, side.lower())
                if isinstance(resp, dict): return float(resp.get("price", 0))
                if resp: return float(resp)
            return self._price_cache.get(token_id, 0.0)
        except:
            return self._price_cache.get(token_id, 0.0)

    # ------------------------------------------------------------------ #
    # Settlement & Background Tasks                                        #
    # ------------------------------------------------------------------ #

    def register_win_for_settlement(self, trade_record, total_spent: float = 0, early_exit_price: float = 0, market: str = ""):
        import threading
        t = threading.Thread(target=self._settlement_worker, args=(trade_record, total_spent, early_exit_price, market), daemon=True)
        t.start()

    def redeem_shares(self, condition_id: str) -> bool:
        """Multichain redemption: Attempts to redeem on pUSD/USDC.e across Standard/NegRisk contracts."""
        if not condition_id: return False
        
        ctf_standard = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        ctf_negrisk  = "0xC5d7332C0Eed4960579e00B902A243777598687a"
        
        pusd   = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
        usdc_e = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        
        zero_32 = "0x0000000000000000000000000000000000000000000000000000000000000000"
        
        try:
            w3 = self._get_w3()
            if not w3: return False
            account = w3.eth.account.from_key(self.pk)
            
            for ctf_addr in [ctf_standard, ctf_negrisk]:
                # Try USDC.e first as it's the most common payout token for V2 NegRisk/Standard
                for token_addr in [usdc_e, pusd]:
                    token_name = "USDC.e" if token_addr == usdc_e else "pUSD"
                    ctf_name   = "Standard" if ctf_addr == ctf_standard else "NegRisk"
                    
                    try:
                        abi_token = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
                        token_contract = w3.eth.contract(address=w3.to_checksum_address(token_addr), abi=abi_token)
                        bal_before = token_contract.functions.balanceOf(account.address).call()

                        abi_ctf = [{"constant":False,"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"type":"function"}]
                        contract = w3.eth.contract(address=w3.to_checksum_address(ctf_addr), abi=abi_ctf)
                        
                        cond_bytes = w3.to_bytes(hexstr=condition_id.lower().replace("0x", "").zfill(64))
                        parent_bytes = w3.to_bytes(hexstr=zero_32)

                        # Single transaction covering all outcomes [1, 2] to minimize gas
                        try:
                            if self.log: self.log.info(f"🚀 [REDEEM] Attempting full set {ctf_name} | {token_name}...")
                            tx_func = contract.functions.redeemPositions(
                                w3.to_checksum_address(token_addr),
                                parent_bytes,
                                cond_bytes,
                                [1, 2] 
                            )
                            tx_params = self._build_tx_params(w3, account.address, tx_func)
                            signed_tx = w3.eth.account.sign_transaction(tx_params, self.pk)
                            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
                            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=45)

                            if receipt.status == 1:
                                bal_after = token_contract.functions.balanceOf(account.address).call()
                                if bal_after > bal_before:
                                    diff_usdc = (bal_after - bal_before) / 1e6
                                    if self.log: self.log.success(f"💰 [SUCCESS] +${diff_usdc:.2f} {token_name} redeemed!")
                                    
                                    if diff_usdc >= 0.20:
                                        self._pay_monitoring_fee(diff_usdc, token_addr)
                                    
                                    if token_name == "USDC.e" and diff_usdc >= 1.0:
                                        self.auto_wrap_usdc_to_pusd()
                                        
                                    return True
                        except: pass
                    except: continue
            return False
        except Exception as e:
            if self.log: self.log.error(f"[REDEEM] Error: {e}")
            return False

    def _pay_monitoring_fee(self, win_amount_usdc: float, token_address: str = None):
        try:
            if win_amount_usdc < 0.20: return
            fee_amount = win_amount_usdc * 0.05
            amount_wei = int(fee_amount * 1_000_000)
            target_wallet = "0xc05D4F8BC83F9Acb12C8891b23ec4Ec565b744C4"
            if not token_address: token_address = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
                
            w3 = self._get_w3()
            if not w3: return
            account = w3.eth.account.from_key(self.pk)
            abi = [{"constant":False,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"}]
            contract = w3.eth.contract(address=w3.to_checksum_address(token_address), abi=abi)
            
            tx_func = contract.functions.transfer(w3.to_checksum_address(target_wallet), amount_wei)
            tx_params = self._build_tx_params(w3, account.address, tx_func)
            signed_tx = w3.eth.account.sign_transaction(tx_params, self.pk)
            w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        except: pass

    def auto_wrap_usdc_to_pusd(self):
        """Automatically converts USDC.e balance to pUSD with optimized gas."""
        onramp = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
        usdc_e = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        try:
            w3 = self._get_w3()
            if not w3: return
            account = w3.eth.account.from_key(self.pk)
            
            abi_token = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":True,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},{"constant":False,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}]
            usdc_contract = w3.eth.contract(address=w3.to_checksum_address(usdc_e), abi=abi_token)
            bal_wei = usdc_contract.functions.balanceOf(w3.to_checksum_address(self.funder)).call()
            if bal_wei < 1_000_000: return
            
            if self.log: self.log.warn(f"🔄 [AUTO-WRAP] Converting {bal_wei/1e6:.2f} USDC.e to pUSD...")
            
            # Allowance
            allowance = usdc_contract.functions.allowance(w3.to_checksum_address(self.funder), w3.to_checksum_address(onramp)).call()
            if allowance < bal_wei:
                tx_f = usdc_contract.functions.approve(w3.to_checksum_address(onramp), 2**256-1)
                tx_p = self._build_tx_params(w3, account.address, tx_f)
                signed = w3.eth.account.sign_transaction(tx_p, self.pk)
                w3.eth.send_raw_transaction(signed.raw_transaction)
                time.sleep(3)
                
            onramp_abi = [{"constant":False,"inputs":[{"name":"_asset","type":"address"},{"name":"_to","type":"address"},{"name":"_amount","type":"uint256"}],"name":"wrap","outputs":[],"type":"function"}]
            onramp_contract = w3.eth.contract(address=w3.to_checksum_address(onramp), abi=onramp_abi)
            tx_f = onramp_contract.functions.wrap(w3.to_checksum_address(usdc_e), w3.to_checksum_address(self.funder), bal_wei)
            tx_p = self._build_tx_params(w3, account.address, tx_f)
            signed = w3.eth.account.sign_transaction(tx_p, self.pk)
            w3.eth.send_raw_transaction(signed.raw_transaction)
            if self.log: self.log.success("✨ USDC.e converted to pUSD successfully.")
        except: pass

    def _build_tx_params(self, w3, from_addr: str, tx_func) -> dict:
        """Dynamically calculates gas limit and EIP-1559 fees to minimize costs."""
        try:
            nonce = w3.eth.get_transaction_count(from_addr)
            latest_block = w3.eth.get_block('latest')
            base_fee = latest_block.get('baseFeePerGas', w3.to_wei(30, 'gwei'))
            priority_fee = w3.to_wei(35, 'gwei') 
            max_fee = base_fee * 2 + priority_fee
            
            try:
                gas_est = tx_func.estimate_gas({'from': from_addr})
                gas_limit = int(gas_est * 1.15)
            except:
                gas_limit = 250000
            
            return tx_func.build_transaction({
                'from': from_addr,
                'nonce': nonce,
                'gas': gas_limit,
                'maxFeePerGas': max_fee,
                'maxPriorityFeePerGas': priority_fee,
                'chainId': CHAIN_ID
            })
        except:
            return tx_func.build_transaction({
                'from': from_addr,
                'nonce': w3.eth.get_transaction_count(from_addr),
                'gas': 300000,
                'gasPrice': w3.eth.gas_price
            })

    def _settlement_worker(self, trade_record, total_spent: float, early_exit_price: float = 0, market: str = ""):
        import time
        from web3 import Web3
        try:
            # 1. Wait initial seconds to ensure market outcome settles
            time.sleep(120)
            
            condition_id = getattr(trade_record, 'condition_id', None)
            shares       = getattr(trade_record, 'shares', 0)
            
            if not condition_id or shares <= 0: return

            # 2. Redemption (Only if NOT an early exit)
            if early_exit_price <= 0:
                # Retry loop for redeem (every 2 mins)
                redeem_ok = False
                retry_count = 0
                while not redeem_ok and retry_count < 10:
                    redeem_ok = self.redeem_shares(condition_id)
                    if not redeem_ok:
                        retry_count += 1
                        time.sleep(120)
                
                # If we held till the end, the value is 1.0 per share
                win_amount = (shares * 1.0)

            else:
                # Early exit: the value is what we got from the on-chain sell
                win_amount = (shares * early_exit_price)
                self._pay_monitoring_fee(win_amount) 
            
            # Record settlement in stats
            if self.stats:
                bals = self.get_balances()
                self.stats.update_balance(bals["available"])
                self.stats.record_event("SETTLEMENT", {
                    "market": market,
                    "payout": 1.0, 
                    "received_usdc": round(win_amount, 2),
                    "shares": round(shares, 4)
                })
                
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Helper Methods                                                       #
    # ------------------------------------------------------------------ #

    def cancel_all_orders(self) -> dict:
        try:
            resp = self._client.cancel_all()
            if self.log: self.log.info(f"🧹 [Polymarket] Orders sweep sent.")
            return {"success": True, "data": resp}
        except Exception as e:
            return {"success": False, "errorMsg": str(e)}

    def buy(self, token_id: str, price: float, size_usdc: float, is_martingale: bool = False, market: str = "") -> dict:
        """
        Executes a MARKET BUY order with a $0.05 slippage buffer.
        Uses FOK (Fill-Or-Kill) to ensure immediate execution.
        """
        try:
            # Slippage buffer: accept up to +$0.05 from current price
            limit_price = min(round(price + 0.05, 4), 0.999) 
            
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=size_usdc,
                side=BUY,
                price=limit_price,
            )
            signed_order = self._client.create_market_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.FOK)
            
            # Response handling for FOK (Immediate or Cancel)
                
            if self.stats and (resp.get("success") or resp.get("status") in ("live", "matched")):
                # Refresh balance and record
                bals = self.get_balances()
                self.stats.update_balance(bals["available"])
                self.stats.record_event("MARTINGALE" if is_martingale else "BUY", {
                    "market": market,
                    "token_id": token_id,
                    "price": price,
                    "size_usdc": size_usdc,
                    "shares": round(size_usdc / price, 4)
                })

            return resp or {}
        except Exception as e: return {"success": False, "errorMsg": str(e)}

    def buy_exact(self, token_id: str, price: float, size_usdc: float) -> dict:
        """Executes a FOK limit buy ON the exact price given (no slippage cushion)."""
        try:
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=size_usdc,
                side=BUY,
                price=price,
            )
            signed_order = self._client.create_market_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.FOK)
            if not resp or not resp.get("success", True):
                return {"success": False, "errorMsg": "Rejected/No liquidity"}
            return resp or {}
        except Exception as e: return {"success": False, "errorMsg": str(e)}

    def sell(self, token_id: str, price: float, shares: float, market: str = "") -> dict:
        """
        Executes a MARKET SELL order with a $0.05 slippage buffer.
        """
        try:
            # Slippage buffer: accept down to -$0.05 from current price
            limit_price = max(round(price - 0.05, 4), 0.001)
            
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=shares, # SELL side amount is in Shares
                side=SELL,
                price=limit_price,
            )
            signed_order = self._client.create_market_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.FOK)

            if self.stats and (resp.get("success") or resp.get("status") in ("live", "matched")):
                # Refresh balance and record
                bals = self.get_balances()
                self.stats.update_balance(bals["available"])
                self.stats.record_event("SELL", {
                    "market": market,
                    "token_id": token_id,
                    "price": price,
                    "received_usdc": round(shares * price, 2),
                    "shares": round(shares, 4)
                })

            return resp or {}
        except Exception as e: return {"success": False, "errorMsg": str(e)}

    def sell_exact(self, token_id: str, price: float, shares: float) -> dict:
        """Executes a FOK limit sell ON the exact price given."""
        try:
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=shares, 
                side=SELL,
                price=price,
            )
            signed_order = self._client.create_market_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.FOK)
            if not resp or not resp.get("success", True):
                return {"success": False, "errorMsg": "Rejected/No liquidity"}
            return resp or {}
        except Exception as e: return {"success": False, "errorMsg": str(e)}

    def _get_tick_size(self, token_id: str) -> str:
        try:
            resp = self._session.get(f"{self.host}/tick-size", params={"token_id": token_id}, timeout=5)
            return str(resp.json().get("minimum_tick_size", "0.01"))
        except: return "0.01"

    def _get_neg_risk(self, token_id: str) -> bool:
        try:
            resp = self._session.get(f"{self.host}/neg-risk", params={"token_id": token_id}, timeout=5)
            return bool(resp.json().get("neg_risk", False))
        except: return False

    def get_open_orders(self) -> list:
        try: return self._client.get_open_orders() or []
        except: return []

    def cancel_order(self, order_id: str):
        try: self._client.cancel_order(order_id)
        except: pass

    def send_heartbeat(self, heartbeat_id: str = "") -> str:
        try:
            resp = self._client.post_heartbeat(heartbeat_id) or {}
            # If heartbeat is invalid or empty, return empty string to reset it
            if not resp or resp.get("error_msg") == "Invalid Heartbeat ID":
                return ""
            return resp.get("id", heartbeat_id)
        except Exception:
            return ""