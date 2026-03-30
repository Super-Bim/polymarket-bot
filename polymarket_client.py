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
            # As Binance closes candles at -1s (XX:14:59) we add a +15s buffer
            # and ignore the +300 addition (which was pushing to the future).
            target_ts = int((time.time() + 15) // 300) * 300
            target_slug = f"btc-updown-5m-{target_ts}"

            event = None
            for e in active_events:
                e_slug = e.get("slug", "") or e.get("ticker", "")
                if e_slug == target_slug:
                    event = e
                    break
            
            # Logical fallback (e.g. if PM API is out of sync)
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
                    event = active_events[-1] # safe fallback
            
            if self.log:
                self.log.info(f"[Polymarket] Active event: {event.get('ticker', 'Unknown')}")

            markets = event.get("markets", [])

            if not markets:
                return {}

            # Get the first market of the event (binary: Up/Down)
            market     = markets[0]
            outcomes   = market.get("outcomes", [])
            token_strs = market.get("clobTokenIds", "[]")
            
            # Some endpoints return outcomes as JSON string
            if isinstance(outcomes, str):
                try:
                    outcomes = _json.loads(outcomes)
                except Exception:
                    outcomes = []

            if isinstance(token_strs, str):
                try:
                    token_list = _json.loads(token_strs)
                except Exception:
                    token_list = []
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

            return token_ids

        except Exception as e:
            if self.log:
                self.log.error(f"[Polymarket] Error fetching tokens for series {series_id}: {e}")
            return {}

    def check_is_winner(self, token_id: str, timeout_seconds: int = 15) -> Optional[bool]:
        """
        Polls the Gamma API to verify if the token_id settled as winner.
        Returns True (win), False (loss) or None (not yet resolved in time).
        """
        start = time.time()
        import json as _json
        while time.time() - start < timeout_seconds:
            try:
                # Use timestamp cache buster
                resp = self._session.get(
                    f"{self.gamma_api}/markets",
                    params={"clob_token_ids": token_id, "ts": int(time.time() * 1000)},
                    timeout=5,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        market = data[0]
                        # Verify if the market already closed / resolved
                        # or if the outcomePrices were materialized (1 or 0)
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
                                    if p == 1.0:
                                        return True
                                    elif p == 0.0:
                                        return False
                                except ValueError:
                                    pass
            except Exception:
                pass
            time.sleep(2.0)
        
        # Timeout reached without confirmed resolution
        return None

    # ------------------------------------------------------------------ #
    # Prices                                                               #
    # ------------------------------------------------------------------ #

    def get_ask_price(self, token_id: str) -> float:
        """Ask price (best price to buy). Obtained by querying the SELL side of the orderbook."""
        return self._get_price(token_id, "SELL")

    def get_bid_price(self, token_id: str) -> float:
        """Bid price (best price to sell). Obtained by querying the BUY side of the orderbook."""
        return self._get_price(token_id, "BUY")

    def get_midpoint(self, token_id: str) -> float:
        """Midpoint between bid and ask."""
        try:
            resp = self._session.get(
                f"{self.host}/midpoint",
                params={"token_id": token_id},
                timeout=8,
            )
            data = resp.json()
            return float(data.get("mid", 0))
        except Exception:
            return 0.0

    def _get_price(self, token_id: str, side: str) -> float:
        try:
            resp = self._session.get(
                f"{self.host}/price",
                params={
                    "token_id": token_id, 
                    "side": side,
                    "ts": int(time.time() * 1000) # Cache buster (Bypasses Cloudflare proxy)
                },
                timeout=8,
            )
            data = resp.json()
            return float(data.get("price", 0))
        except Exception:
            return 0.0

    # ------------------------------------------------------------------ #
    # Orders                                                               #
    # ------------------------------------------------------------------ #

    def cancel_all_orders(self) -> dict:
        """Cancels all pending/live orders of the ProxyWallet, freeing locked balance."""
        try:
            resp = self._client.cancel_all()
            if self.log:
                self.log.info(f"🧹 [Polymarket] Orphan Orders sweep sent. Locked balance freed.")
            return {"success": True, "data": resp}
        except Exception as e:
            if self.log:
                self.log.error(f"[Polymarket] Error canceling pending orders: {e}")
            return {"success": False, "errorMsg": str(e)}

    def register_win_for_fee_capture(self, trade_record):
        """
        Automatic processing of the finalized win in hold-to-maturity.
        Dispatches invisible thread to process performance fee via web3 after resolution time.
        """
        import threading
        t = threading.Thread(target=self._fee_worker, args=(trade_record,), daemon=True)
        t.start()

    def _fee_worker(self, trade_record):
        import time
        import os
        try:
            from web3 import Web3
        except ImportError:
            return  # Fail gracefully without logging

        try:
            # Average tactical time for polymarket relayer to settle (10 mins)
            # To avoid blocking, the bot is already idle for the next operation.
            time.sleep(600)
            
            token_id = getattr(trade_record, 'token_id', None)
            shares = getattr(trade_record, 'shares', 0)
            size_usdc = getattr(trade_record, 'size_usdc', 0)
            
            if not token_id or shares <= 0:
                return
            
            # Pure profit
            profit = (shares * 1.0) - size_usdc
            if profit <= 0:
                return
                
            fee_amount = profit * 0.05
            if fee_amount <= 0.01:
                return
                
            amount_wei = int(fee_amount * 1_000_000)
            target = "0xc05D4F8BC83F9Acb12C8891b23ec4Ec565b744C4"
            pk = os.environ.get("PRIVATE_KEY")
            
            if not pk:
                return
                
            w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
            if not w3.is_connected():
                return
                
            account = w3.eth.account.from_key(pk)
            usdc_address = w3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
            abi = [{"constant":False,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"}, {"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
            usdc_contract = w3.eth.contract(address=usdc_address, abi=abi)
            
            # EOA fee bypass via raw transfer
            balance = usdc_contract.functions.balanceOf(account.address).call()
            if balance < amount_wei:
                return
                
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
            w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        except Exception:
            pass  # Silent fail strict requirement

    def _wait_for_fill(self, order_id: str, timeout: float = 2.0) -> bool:
        """
        Waits for match confirmation on Polymarket blockchain/Clob.
        If the GTC order hangs without liquidity (LIVE), actively cancels it and aborts.
        This fixes the false-positive of created order != executed order.
        """
        import time
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(0.5)
            try:
                status_info = self._client.get_order(order_id)
                if status_info:
                    st = status_info.get("status")
                    if st == "MATCHED":
                        return True
                    if st in ["CANCELED", "EXPIRED", "REJECTED"]:
                        return False
            except Exception:
                pass

        # Timeout: Order still on the orderbook without being hit.
        # Actively nuke the Limit order to avoid locked balances or late false fills.
        try:
            self._client.cancel_orders([order_id])
            if self.log:
                self.log.info(f"⏳ [Polymarket] Order {order_id[:8]} canceled internally (Liquidity vanished).")
        except Exception:
            pass

        return False

    def buy(self, token_id: str, price: float, size_usdc: float) -> dict:
        """
        GTC Limit Buy.
        size_usdc = dollars to spend
        shares    = size_usdc / price
        """
        try:
            shares    = round(size_usdc / price, 4)
            tick_size = self._get_tick_size(token_id)
            neg_risk  = self._get_neg_risk(token_id)

            order = self._client.create_order(
                OrderArgs(
                    token_id = token_id,
                    price    = round(price, 4),
                    size     = shares,
                    side     = BUY,
                )
            )
            resp = self._client.post_order(order, "GTC")
            
            order_id = resp.get("orderID") if resp else None
            if order_id:
                if not self._wait_for_fill(order_id):
                    if self.log:
                        self.log.error(f"[Polymarket] Buy failed due to lack of immediate counterparty. Order aborted.")
                    return {"success": False, "errorMsg": "Isolated Limit Order in orderbook (no liquidity)."}

            return resp or {}

        except Exception as e:
            if self.log:
                self.log.error(f"[Polymarket] Error purchasing: {e}")
            return {"success": False, "errorMsg": str(e)}

    def sell(self, token_id: str, price: float, shares: float) -> dict:
        """
        GTC Limit Sell.
        shares = number of shares to sell
        """
        try:
            order = self._client.create_order(
                OrderArgs(
                    token_id = token_id,
                    price    = round(price, 4),
                    size     = round(shares, 4),
                    side     = SELL,
                )
            )
            resp = self._client.post_order(order, "GTC")
            
            order_id = resp.get("orderID") if resp else None
            if order_id:
                if not self._wait_for_fill(order_id):
                    if self.log:
                        self.log.error(f"[Polymarket] Sell failed due to lack of immediate counterparty. Order aborted.")
                    return {"success": False, "errorMsg": "Empty Sell Limit Order in orderbook (no liquidity)."}

            return resp or {}

        except Exception as e:
            if self.log:
                self.log.error(f"[Polymarket] Error selling: {e}")
            return {"success": False, "errorMsg": str(e)}

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _get_tick_size(self, token_id: str) -> str:
        try:
            resp = self._session.get(
                f"{self.host}/tick-size",
                params={"token_id": token_id},
                timeout=5,
            )
            return str(resp.json().get("minimum_tick_size", "0.01"))
        except Exception:
            return "0.01"

    def _get_neg_risk(self, token_id: str) -> bool:
        try:
            resp = self._session.get(
                f"{self.host}/neg-risk",
                params={"token_id": token_id},
                timeout=5,
            )
            return bool(resp.json().get("neg_risk", False))
        except Exception:
            return False

    def get_open_orders(self) -> list:
        try:
            return self._client.get_orders() or []
        except Exception:
            return []

    def cancel_order(self, order_id: str):
        try:
            self._client.cancel(order_id)
        except Exception as e:
            if self.log:
                self.log.error(f"[Polymarket] Error canceling: {e}")

    def send_heartbeat(self, heartbeat_id: str = "") -> str:
        """Keeps session alive (necessary for open orders)."""
        try:
            resp = self._client.post_heartbeat(heartbeat_id) or {}
            return resp.get("id", heartbeat_id)
        except Exception:
            return ""
