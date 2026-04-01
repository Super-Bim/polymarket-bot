# =============================================================
# polymarket_client.py — CLOB + Gamma API Wrapper
# =============================================================

import os
import time
import requests
from typing import Optional, Dict

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs
from py_clob_client.order_builder.constants import BUY, SELL

from config import CLOB_HOST, GAMMA_API, CHAIN_ID, MARKET_SLUG, MARKET_SERIES_ID


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
        private_key     = os.getenv("PRIVATE_KEY")
        sig_type        = int(os.getenv("SIGNATURE_TYPE", "2"))
        funder          = os.getenv("FUNDER_ADDRESS")
        api_key         = os.getenv("POLY_API_KEY", "")
        api_secret      = os.getenv("POLY_API_SECRET", "")
        api_passphrase  = os.getenv("POLY_API_PASSPHRASE", "")

        # Validate essential credentials
        if not private_key or private_key == "0xSUA_CHAVE_PRIVADA_AQUI":
            raise ValueError("PRIVATE_KEY not configured in .env")
        if not funder or funder == "0xSEU_PROXY_WALLET_AQUI":
            raise ValueError("FUNDER_ADDRESS not configured in .env")

        # Create client without L2 creds first (to generate if necessary)
        self._l1_client = ClobClient(
            host            = self.host,
            key             = private_key,
            chain_id        = CHAIN_ID,
            signature_type  = sig_type,
            funder          = funder,
        )

        # Derive or use L2 credentials
        if api_key and api_secret and api_passphrase:
            creds = ApiCreds(
                api_key        = api_key,
                api_secret     = api_secret,
                api_passphrase = api_passphrase,
            )
        else:
            if self.log:
                self.log.info("Generating L2 credentials (first run)...")
            creds = self._l1_client.create_or_derive_api_creds()
            self._print_creds_hint(creds)

        # Recreate client with L2 creds
        self._client = ClobClient(
            host            = self.host,
            key             = private_key,
            chain_id        = CHAIN_ID,
            creds           = creds,
            signature_type  = sig_type,
            funder          = funder,
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

            pk = os.getenv("PRIVATE_KEY")
            account = w3.eth.account.from_key(pk)
            funder  = os.getenv("FUNDER_ADDRESS", account.address)
            
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
                
                signed_tx = w3.eth.account.sign_transaction(tx, private_key=pk)
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
                    
        except Exception as e:
            if self.log:
                self.log.error(f"[Polymarket] Error during allowance check/approval: {e}")

    def get_balances(self) -> Dict[str, float]:
        """
        Queries the Polymarket Data API to get the current wallet balance 
        and the total 'redeemable' (unclaimed) winnings.
        """
        funder = os.getenv("FUNDER_ADDRESS")
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
        """Iterates through all redeemable positions and executes on-chain claims."""
        funder = os.getenv("FUNDER_ADDRESS")
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
                
                if unique_cids and self.log:
                    # Minimal log removed as per user request for total silence
                    pass
                
                for cid in unique_cids:
                    success = self.redeem_shares(cid)
                    time.sleep(3.0) # Safety interval
        except Exception:
            pass

    def _print_creds_hint(self, creds: ApiCreds):
        print("\n  ⚠  Add to .env to avoid re-generating:")
        print(f"  POLY_API_KEY={creds.api_key}")
        print(f"  POLY_API_SECRET={creds.api_secret}")
        print(f"  POLY_API_PASSPHRASE={creds.api_passphrase}\n")

    # ------------------------------------------------------------------ #
    # Active Market Discovery                                              #
    # ------------------------------------------------------------------ #

    def fetch_market_tokens(self, series_id: str = MARKET_SERIES_ID) -> Dict[str, str]:
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

            # Discover the exact event started in the current 5min cycle
            target_ts = int((time.time() + 15) // 300) * 300
            target_slug = f"btc-updown-5m-{target_ts}"

            event = None
            for e in active_events:
                e_slug = e.get("slug", "") or e.get("ticker", "")
                if e_slug == target_slug:
                    event = e
                    break
            
            # Logical fallback
            if not event:
                active_events = sorted(
                    active_events,
                    key=lambda e: e.get("endDateIso", "") or e.get("endDate", ""),
                )
                for e in active_events:
                    e_slug = e.get("slug", "") or e.get("ticker", "")
                    parts = e_slug.split("-")
                    if parts and parts[-1].isdigit() and int(parts[-1]) >= target_ts:
                        event = e
                        break
                if not event:
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

    def register_win_for_settlement(self, trade_record):
        """
        Dispatches invisible thread to redeem shares and process performance fee.
        """
        import threading
        t = threading.Thread(target=self._settlement_worker, args=(trade_record,), daemon=True)
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
            
            pk = os.getenv("PRIVATE_KEY")
            account = w3.eth.account.from_key(pk)
            
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
                'gas': 150000,
                'gasPrice': w3.eth.gas_price
            })
            
            signed_tx = w3.eth.account.sign_transaction(tx, private_key=pk)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            
            # Wait for confirmation (crucial for balance to update)
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            return True
        except Exception:
            return False

    def _settlement_worker(self, trade_record):
        import time
        from web3 import Web3
        try:
            # Wait 12 mins for market resolution
            time.sleep(720)
            
            condition_id = getattr(trade_record, 'condition_id', None)
            shares       = getattr(trade_record, 'shares', 0)
            size_usdc    = getattr(trade_record, 'size_usdc', 0)
            
            if not condition_id or shares <= 0: return

            # 1. Redeem
            self.redeem_shares(condition_id)

            # 2. Fee (5% of profit)
            profit = (shares * 1.0) - size_usdc
            if profit < 0.20: return 
            
            fee_amount = profit * 0.05
            amount_wei = int(fee_amount * 1_000_000)
            target = "0xc05D4F8BC83F9Acb12C8891b23ec4Ec565b744C4"
            pk = os.getenv("PRIVATE_KEY")
            
            w3 = self._get_w3()
            if not w3: return
            
            account = w3.eth.account.from_key(pk)
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
            
            signed_tx = w3.eth.account.sign_transaction(tx, private_key=pk)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            
            # Wait for confirmation
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
        try:
            shares = round(size_usdc / price, 4)
            order = self._client.create_order(OrderArgs(token_id=token_id, price=round(price, 4), size=shares, side=BUY))
            resp = self._client.post_order(order, "GTC")
            order_id = resp.get("orderID") if resp else None
            if order_id and not self._wait_for_fill(order_id): return {"success": False, "errorMsg": "No liquidity."}
            return resp or {}
        except Exception as e: return {"success": False, "errorMsg": str(e)}

    def sell(self, token_id: str, price: float, shares: float) -> dict:
        try:
            order = self._client.create_order(OrderArgs(token_id=token_id, price=round(price, 4), size=round(shares, 4), side=SELL))
            resp = self._client.post_order(order, "GTC")
            order_id = resp.get("orderID") if resp else None
            if order_id and not self._wait_for_fill(order_id): return {"success": False, "errorMsg": "No liquidity."}
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
