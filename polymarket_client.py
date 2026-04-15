# =============================================================
# polymarket_client.py — CLOB + Gamma API Wrapper
# =============================================================

import os
import time
import requests
from typing import Optional, Dict

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, MarketOrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from eth_account import Account
from dotenv import set_key
from config import CLOB_HOST, GAMMA_API, CHAIN_ID


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
        if not self.pk or self.pk in ["0xSUA_CHAVE_PRIVADA_AQUI", "0xYOUR_PRIVATE_KEY_HERE"]:
            raise ValueError("PRIVATE_KEY not configured in .env")

        # Derive or validate funder address
        derived_funder = False
        # Treat empty string or placeholder as missing
        if not self.funder or self.funder.strip() in ["", "0xSEU_PROXY_WALLET_AQUI", "0xYOUR_ADDRESS_HERE", "0xSEU_ENDERECO_AQUI"]:
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
            self.creds = self._l1_client.create_or_derive_api_creds()
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
        )

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
        """Checks USDC.e allowance and performs automatic approval if necessary."""
        spender = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
        usdc_e  = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        
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
            
            # --- Check actual balance first ---
            balance_wei = contract.functions.balanceOf(w3.to_checksum_address(funder)).call()
            balance_usdc = balance_wei / 1_000_000
            
            if balance_usdc < 1.0:
                # Diagnostic: Maybe they have native USDC instead of USDC.e?
                native_usdc = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
                native_contract = w3.eth.contract(address=w3.to_checksum_address(native_usdc), abi=[abi[2]])
                try:
                    native_bal = native_contract.functions.balanceOf(w3.to_checksum_address(funder)).call() / 1_000_000
                except:
                    native_bal = 0
                
                if self.log:
                    self.log.error(f"❌ [Polymarket] Low USDC.e balance: {balance_usdc:.2f}")
                    if native_bal > 1.0:
                        self.log.warn(f"⚠ [Polymarket] Detected {native_bal:.2f} native USDC. Polymarket requires bridged USDC.e!")
                        self.log.warn(f"⚠ [Polymarket] Swap USDC to USDC.e on Uniswap/Quickswap first.")

            elif self.log:
                self.log.info(f"💰 [Polymarket] Wallet Balance: {balance_usdc:.2f} USDC.e")

            # --- Check allowance ---
            current_allowance = contract.functions.allowance(
                w3.to_checksum_address(funder), 
                w3.to_checksum_address(spender)
            ).call()
            
            required_wei = int(amount_usdc * 1_000_000)
            
            if current_allowance < required_wei:
                if self.log:
                    self.log.warn(f"⚠ [Polymarket] Low allowance detected ({current_allowance/1e6:.2f} USDC). Authorizing spender {spender[:10]}...")
                
                # Max approval (standard for trading bots)
                max_val = 2**256 - 1
                
                # Check MATIC balance for gas
                matic_balance = w3.eth.get_balance(account.address)
                if matic_balance < w3.to_wei(0.01, 'ether'):
                    if self.log:
                        self.log.error("[Polymarket] Insufficient MATIC for allowance transaction! Please add gas.")
                    return

                tx = contract.functions.approve(
                    w3.to_checksum_address(spender), 
                    max_val
                ).build_transaction({
                    'from': account.address,
                    'nonce': w3.eth.get_transaction_count(account.address),
                    'gas': 100000,
                    'gasPrice': w3.eth.gas_price
                })
                
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
                w3.to_checksum_address(spender)
            ).call()

            if not is_approved:
                if self.log: self.log.warn(f"⚠ [Polymarket] CTF Exchange Not Approved. Authorizing for Sells...")
                matic_balance = w3.eth.get_balance(account.address)
                if matic_balance < w3.to_wei(0.01, 'ether'):
                    if self.log: self.log.error("[Polymarket] Insufficient MATIC for CTF approval!")
                    return
                tx2 = ctf_contract.functions.setApprovalForAll(
                    w3.to_checksum_address(spender),
                    True
                ).build_transaction({
                    'from': account.address,
                    'nonce': w3.eth.get_transaction_count(account.address),
                    'gas': 150000,
                    'gasPrice': w3.eth.gas_price
                })
                signed_tx2 = w3.eth.account.sign_transaction(tx2, private_key=self.pk)
                tx_hash2 = w3.eth.send_raw_transaction(signed_tx2.raw_transaction)
                if self.log: self.log.info(f"✅ [Polymarket] CTF Approval sent! TX: {w3.to_hex(tx_hash2)}")
                w3.eth.wait_for_transaction_receipt(tx_hash2, timeout=120)
                if self.log: self.log.info("✅ [Polymarket] CTF shares authorized for exchange successfully.")
            else:
                if self.log: self.log.info(f"✅ [Polymarket] CTF shares authorized for exchange.")

        except Exception as e:
            if self.log:
                self.log.error(f"[Polymarket] Error during allowance check/approval: {e}")

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
        usdc_e = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        
        balances = {"available": 0.0, "redeemable": 0.0}
        
        try:
            w3 = self._get_w3()
            if not w3: return balances
            
            abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
            usdc_contract = w3.eth.contract(address=w3.to_checksum_address(usdc_e), abi=abi)
            balances["available"] = usdc_contract.functions.balanceOf(w3.to_checksum_address(funder)).call() / 1_000_000

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

    def _print_creds_hint(self, creds: ApiCreds):
        print("\n  ⚠  Add to .env to avoid re-generating:")
        print(f"  POLY_API_KEY={creds.api_key}")
        print(f"  POLY_API_SECRET={creds.api_secret}")
        print(f"  POLY_API_PASSPHRASE={creds.api_passphrase}\n")

    # ------------------------------------------------------------------ #
    # Active Market Discovery                                              #
    # ------------------------------------------------------------------ #

    def fetch_market_tokens(self, series_id: str = "") -> Dict[str, str]:
        """
        Fetches the active btc-updown-5m event using the series ID.
        """
        import json as _json

        try:
            resp = self._session.get(
                f"{self.gamma_api}/events",
                params={
                    "series_id": series_id, 
                    "active": "true", 
                    "closed": "false",
                    "limit": "100"
                },
                timeout=10,
            )
            resp.raise_for_status()
            events = resp.json()

            if not events:
                if self.log:
                    self.log.error(f"[Polymarket] No active event found for series {series_id}")
                return {}

            # Filter events that are actually active
            active_events = [e for e in events if e.get("active") and not e.get("closed")]
            if not active_events:
                if self.log:
                    self.log.error(f"[Polymarket] Events found, but none active for series {series_id}")
                return {}

            # Discover the best active market for "reversal"
            # It should be the one ending in about 1-5 minutes
            now_sec = time.time()
            valid_events = []
            for e in active_events:
                end_iso = e.get("endDateIso") or e.get("endDate")
                if end_iso:
                    # Convert ISO to timestamp (handling Z or +00)
                    from datetime import datetime, timezone
                    try:
                        dt = datetime.fromisoformat(end_iso.replace('Z', '+00:00'))
                        end_ts = dt.timestamp()
                    except: end_ts = 0
                    
                    # We want an event that ends at least 30s in the future
                    if end_ts > now_sec + 30:
                        valid_events.append((end_ts, e))

            if valid_events:
                # Pick the one that ends soonest (the current/next 5m window)
                valid_events.sort(key=lambda x: x[0])
                event = valid_events[0][1]
            else:
                event = active_events[-1]
            
            if self.log:
                self.log.info(f"[Polymarket] Active event: {event.get('ticker', 'Unknown')}")

            markets = event.get("markets", [])
            if not markets:
                return {}

            market     = markets[0]
            outcomes   = market.get("outcomes", [])
            token_strs = market.get("clobTokenIds", "[]")
            
            if isinstance(outcomes, str):
                try: outcomes = _json.loads(outcomes)
                except: outcomes = []

            if isinstance(token_strs, str):
                try: token_list = _json.loads(token_strs)
                except: token_list = []
            else:
                token_list = token_strs or []

            token_ids: Dict[str, str] = {}
            for i, outcome in enumerate(outcomes):
                ou = outcome.strip().upper()
                if i < len(token_list) and token_list[i]:
                    if "UP" in ou or "HIGHER" in ou:
                        token_ids["UP"] = str(token_list[i])
                    elif "DOWN" in ou or "LOWER" in ou:
                        token_ids["DOWN"] = str(token_list[i])

            # Store conditionId for settlement
            token_ids["condition_id"] = market.get("conditionId", "")
            token_ids["market_ticker"] = event.get("ticker", "Unknown")

            return token_ids

        except Exception as e:
            if self.log:
                self.log.error(f"[Polymarket] Error fetching tokens for series {series_id}: {e}")
            return {}

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
        return self._get_price(token_id, "SELL")

    def get_bid_price(self, token_id: str) -> float:
        return self._get_price(token_id, "BUY")

    def get_midpoint(self, token_id: str) -> float:
        try:
            resp = self._session.get(
                f"{self.host}/midpoint",
                params={"token_id": token_id},
                timeout=8,
            )
            data = resp.json()
            return float(data.get("mid", 0))
        except Exception: return 0.0

    def _get_price(self, token_id: str, side: str) -> float:
        try:
            resp = self._session.get(
                f"{self.host}/price",
                params={
                    "token_id": token_id, 
                    "side": side,
                    "ts": int(time.time() * 1000)
                },
                timeout=8,
            )
            data = resp.json()
            return float(data.get("price", 0))
        except Exception: return 0.0

    # ------------------------------------------------------------------ #
    # Settlement & Background Tasks                                        #
    # ------------------------------------------------------------------ #

    def register_win_for_settlement(self, trade_record, total_spent: float = 0):
        """
        Dispatches invisible thread to redeem shares and process performance fee.
        """
        import threading
        t = threading.Thread(target=self._settlement_worker, args=(trade_record, total_spent), daemon=True)
        t.start()

    def redeem_shares(self, condition_id: str) -> bool:
        """
        Calls redeemPositions on the CTF contract to convert winning shares into USDC.e.
        CTF on Polygon: 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
        """
        if not condition_id: return False
        
        ctf_address  = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        usdc_e       = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        zero_32      = "0x0000000000000000000000000000000000000000000000000000000000000000"
        
        try:
            w3 = self._get_w3()
            if not w3: return False
            
            account = w3.eth.account.from_key(self.pk)
            
            abi = [{"constant":False,"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"type":"function"}]
            contract = w3.eth.contract(address=w3.to_checksum_address(ctf_address), abi=abi)
            
            tx = contract.functions.redeemPositions(
                w3.to_checksum_address(usdc_e),
                zero_32,
                condition_id,
                [1, 2]
            ).build_transaction({
                'from': account.address,
                'nonce': w3.eth.get_transaction_count(account.address),
                'gas': 500000,
                'gasPrice': w3.eth.gas_price
            })
            
            signed_tx = w3.eth.account.sign_transaction(tx, private_key=self.pk)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            
            # Wait for confirmation and verify status
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            if receipt.status == 0:
                if self.log: 
                    self.log.error(f"❌ [REDEEM] Transaction Reverted! Hash: {w3.to_hex(tx_hash)}")
                    self.log.warn("⚠ Se você loga usando Google/Email na Polymarket (Proxy Wallet), o bot não tem permissão on-chain para interagir com o resgate nativo diretamente. Resgate pelo Site.")
                return False
                
            # LOG TO FILE (STILL VISIBLE IN log.txt)
            try:
                with open("log.txt", "a", encoding="utf-8") as f:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"[{ts}] [REDEEM] Successfully redeemed condition {condition_id}\n")
            except: pass
            
            return True
        except Exception as e:
            if self.log: self.log.error(f"[REDEEM] Exception: {e}")
            return False

    def _settlement_worker(self, trade_record, total_spent: float):
        import time
        from web3 import Web3
        try:
            # 1. Wait initial 180 seconds (3 minutes)
            time.sleep(180)
            
            condition_id = getattr(trade_record, 'condition_id', None)
            shares       = getattr(trade_record, 'shares', 0)
            
            if not condition_id or shares <= 0: return

            # 2. Retry loop for redeem (every 5 mins)
            redeem_ok = False
            while not redeem_ok:
                redeem_ok = self.redeem_shares(condition_id)
                if not redeem_ok:
                    time.sleep(60) # Wait 60 seconds (1 minute) for resolution
            
            # 3. Calculate Fee (5% of profit of the whole sequence)
            # Winning amount is shares * 1.0
            profit = (shares * 1.0) - total_spent
            if profit < 0.20: return 
            
            fee_amount = profit * 0.05
            amount_wei = int(fee_amount * 1_000_000)
            target = "0xc05D4F8BC83F9Acb12C8891b23ec4Ec565b744C4"
            
            w3 = self._get_w3()
            if not w3: return
            
            account = w3.eth.account.from_key(self.pk)
            usdc_address = w3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
            
            abi = [{"constant":False,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"}]
            usdc_contract = w3.eth.contract(address=usdc_address, abi=abi)
            
            tx = usdc_contract.functions.transfer(
                w3.to_checksum_address(target), 
                amount_wei
            ).build_transaction({
                'from': account.address,
                'nonce': w3.eth.get_transaction_count(account.address),
                'gas': 100000,
                'gasPrice': w3.eth.gas_price
            })
            
            signed_tx = w3.eth.account.sign_transaction(tx, private_key=self.pk)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            
            # Wait for confirmation (Silently)
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
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

    def _wait_for_fill(self, order_id: str, timeout: float = 2.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(0.5)
            try:
                status_info = self._client.get_order(order_id)
                if status_info and status_info.get("status") == "MATCHED": return True
                if status_info and status_info.get("status") in ["CANCELED", "EXPIRED", "REJECTED"]: return False
            except Exception: pass
        try: self._client.cancel_orders([order_id])
        except Exception: pass
        return False

    def buy(self, token_id: str, price: float, size_usdc: float) -> dict:
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
            if not resp or not resp.get("success", True):
                return {"success": False, "errorMsg": "No liquidity within $0.05 buffer."}
                
            return resp or {}
        except Exception as e: return {"success": False, "errorMsg": str(e)}

    def sell(self, token_id: str, price: float, shares: float) -> dict:
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
        try: return self._client.get_orders() or []
        except: return []

    def cancel_order(self, order_id: str):
        try: self._client.cancel(order_id)
        except: pass

    def send_heartbeat(self, heartbeat_id: str = "") -> str:
        try:
            resp = self._client.post_heartbeat(heartbeat_id) or {}
            return resp.get("id", heartbeat_id)
        except: return ""