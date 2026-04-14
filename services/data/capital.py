import asyncio
import datetime
import aiohttp
from config.settings import settings

BASE_URL_DEMO = "https://demo-api-capital.backend-capital.com/api/v1"
BASE_URL_LIVE = "https://api-capital.backend-capital.com/api/v1"


class CapitalClient:
    def __init__(self):
        self.api_key = settings.capital_api_key
        self.base_url = (
            BASE_URL_DEMO if settings.capital_mode == "demo"
            else BASE_URL_LIVE
        )
        self.session_token = None
        self.account_token = None
        self._session_lock = None   # lazy-init inside event loop

    async def ensure_session(self):
        """Safe session check — only one create_session() runs at a time."""
        if self.session_token:
            return
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()
        async with self._session_lock:
            if not self.session_token:   # re-check after acquiring lock
                await self.create_session()

    async def create_session(self) -> bool:
        if not settings.capital_email or not settings.capital_password:
            print("Capital.com credentials not configured")
            return False
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.post(
                    f"{self.base_url}/session",
                    headers={
                        "X-CAP-API-KEY": self.api_key,
                        "Content-Type": "application/json"
                    },
                    json={
                        "identifier": settings.capital_email,
                        "password": settings.capital_password,
                        "encryptedPassword": False
                    }
                )
                if r.status == 200:
                    self.session_token = r.headers.get("CST")
                    self.account_token = r.headers.get("X-SECURITY-TOKEN")
                    print(f"Capital.com session created ({settings.capital_mode} mode)")
                    return True
                else:
                    text = await r.text()
                    print(f"Capital.com session failed: {r.status} — {text[:100]}")
                    return False
        except Exception as e:
            print(f"Capital.com connection error: {e}")
            return False

    def get_headers(self) -> dict:
        return {
            "X-CAP-API-KEY": self.api_key,
            "CST": self.session_token or "",
            "X-SECURITY-TOKEN": self.account_token or "",
            "Content-Type": "application/json"
        }

    async def get_price(self, epic: str) -> dict:
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(
                    f"{self.base_url}/markets/{epic}",
                    headers=self.get_headers()
                )
                d = await r.json()
                instrument = d.get("instrument", {})
                snapshot = d.get("snapshot", {})
                bid = snapshot.get("bid", 0)
                offer = snapshot.get("offer", 0)
                price = (bid + offer) / 2 if bid and offer else 0
                return {
                    "epic": epic,
                    "price": round(price, 5),
                    "bid": bid,
                    "offer": offer,
                    "high": snapshot.get("high", 0),
                    "low": snapshot.get("low", 0),
                    "change_pct": snapshot.get("percentageChange", 0),
                    "name": instrument.get("name", epic)
                }
        except Exception as e:
            return {"epic": epic, "price": 0, "error": str(e)}

    async def get_candles(self, epic: str, resolution: str = "MINUTE_5",
                          max_candles: int = 50) -> list:
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(
                    f"{self.base_url}/prices/{epic}",
                    headers=self.get_headers(),
                    params={"resolution": resolution, "max": max_candles}
                )
                d = await r.json()
                prices = d.get("prices", [])
                closes = []
                for p in prices:
                    close = p.get("closePrice", {})
                    mid = (close.get("bid", 0) + close.get("ask", 0)) / 2
                    closes.append(mid)
                return closes
        except Exception as e:
            print(f"Capital candles error: {e}")
            return []

    async def get_account_balance(self) -> dict:
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(
                    f"{self.base_url}/accounts",
                    headers=self.get_headers()
                )
                d = await r.json()
                accounts = d.get("accounts", [])
                if accounts:
                    balance = accounts[0].get("balance", {})
                    return {
                        "balance": balance.get("balance", 0),
                        "available": balance.get("available", 0),
                        "profit_loss": balance.get("pnl", 0),
                        "currency": "EUR",
                        "mode": settings.capital_mode
                    }
                return {"balance": 0, "available": 0}
        except Exception as e:
            return {"balance": 0, "error": str(e)}

    async def get_positions(self) -> list:
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(
                    f"{self.base_url}/positions",
                    headers=self.get_headers()
                )
                d = await r.json()
                positions = []
                for p in d.get("positions", []):
                    pos = p.get("position", {})
                    market = p.get("market", {})
                    positions.append({
                        "deal_id": pos.get("dealId"),
                        "epic": market.get("epic"),
                        "name": market.get("instrumentName"),
                        "direction": pos.get("direction"),
                        "size": pos.get("size"),
                        "entry_price": pos.get("openLevel"),
                        "current_price": market.get("bid"),
                        "pnl": pos.get("upl"),
                        "stop_loss": pos.get("stopLevel"),
                        "take_profit": pos.get("profitLevel")
                    })
                return positions
        except Exception as e:
            return []

    async def place_order(self, epic: str, direction: str,
                          size: float, stop_loss: float,
                          take_profit: float) -> dict:
        try:
            payload = {
                "epic": epic,
                "direction": direction.upper(),
                "size": size,
                "guaranteedStop": False,
                "stopLevel": stop_loss,
                "profitLevel": take_profit
            }
            async with aiohttp.ClientSession() as s:
                r = await s.post(
                    f"{self.base_url}/positions",
                    headers=self.get_headers(),
                    json=payload
                )
                d = await r.json()
                if r.status == 200:
                    return {"status": "success", "deal_id": d.get("dealReference"), "response": d}

                err = d.get("errorCode", "")

                # Capital.com enforces a minimum distance between entry and stop.
                # The error gives us the exact allowed boundary — clamp and retry once.
                # e.g. "error.invalid.stoploss.maxvalue: 73114.25"  (BUY: stop too high)
                #      "error.invalid.stoploss.minvalue: 73191.75"  (SELL: stop too low)
                adjusted_stop = None
                if "invalid.stoploss.maxvalue" in err:
                    try:
                        adjusted_stop = float(err.split(":")[-1].strip())
                        # Add a small buffer below the boundary so we're safely outside
                        adjusted_stop = round(adjusted_stop * 0.9998, 2)
                    except ValueError:
                        pass
                elif "invalid.stoploss.minvalue" in err:
                    try:
                        adjusted_stop = float(err.split(":")[-1].strip())
                        adjusted_stop = round(adjusted_stop * 1.0002, 2)
                    except ValueError:
                        pass

                if adjusted_stop is not None:
                    print(
                        f"Stop distance too tight ({stop_loss}) — "
                        f"retrying with Capital.com boundary {adjusted_stop}"
                    )
                    payload["stopLevel"] = adjusted_stop
                    r2 = await s.post(
                        f"{self.base_url}/positions",
                        headers=self.get_headers(),
                        json=payload
                    )
                    d2 = await r2.json()
                    if r2.status == 200:
                        return {"status": "success", "deal_id": d2.get("dealReference"), "response": d2}
                    err = d2.get("errorCode", d2.get("error", "unknown"))
                    print(f"Retry also failed — {err}")
                    return {"status": "error", "deal_id": None, "response": d2}

                return {"status": "error", "deal_id": None, "response": d}
        except Exception as e:
            return {"status": "error", "reason": str(e)}

    async def update_stop_loss(self, deal_id: str, stop_loss: float,
                               take_profit: float) -> dict:
        """Update stop loss (and optionally TP) on an open position."""
        try:
            payload = {"stopLevel": stop_loss}
            if take_profit:
                payload["profitLevel"] = take_profit
            async with aiohttp.ClientSession() as s:
                r = await s.put(
                    f"{self.base_url}/positions/{deal_id}",
                    headers=self.get_headers(),
                    json=payload
                )
                d = await r.json()
                if r.status == 200:
                    return {"status": "success", "deal_reference": d.get("dealReference")}
                else:
                    err = d.get("errorCode", d.get("error", "unknown"))
                    print(f"update_stop_loss failed — {deal_id} status={r.status} err={err}")
                    return {"status": "error", "reason": err}
        except Exception as e:
            return {"status": "error", "reason": str(e)}

    async def get_deal_confirmation(self, deal_reference: str) -> dict:
        """
        Fetch the confirmed dealId for an order.

        Capital.com place_order returns a dealReference (temporary).
        The permanent dealId — needed for closing positions — is only
        available via GET /confirms/{dealReference}.
        """
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(
                    f"{self.base_url}/confirms/{deal_reference}",
                    headers=self.get_headers()
                )
                d = await r.json()
                return {
                    "deal_id":        d.get("dealId"),
                    "deal_reference": d.get("dealReference"),
                    "status":         d.get("dealStatus"),   # ACCEPTED / REJECTED
                    "reason":         d.get("reason"),
                    "entry_price":    d.get("level"),
                    "size":           d.get("size"),
                    "direction":      d.get("direction"),
                    "stop_loss":      d.get("stopLevel"),
                    "take_profit":    d.get("profitLevel"),
                    "epic":           d.get("epic"),
                }
        except Exception as e:
            print(f"Capital confirmation error ({deal_reference}): {e}")
            return {}

    async def search_market(self, term: str) -> list:
        """Search Capital.com markets by name to discover correct epic codes."""
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(
                    f"{self.base_url}/markets",
                    headers=self.get_headers(),
                    params={"searchTerm": term}
                )
                d = await r.json()
                return d.get("markets", [])
        except Exception as e:
            print(f"Capital market search error: {e}")
            return []

    async def get_ohlcv(self, epic: str, resolution: str = "MINUTE",
                        max_candles: int = 150) -> list:
        """Returns list of {open, high, low, close, volume} dicts for full indicator calculation."""
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(
                    f"{self.base_url}/prices/{epic}",
                    headers=self.get_headers(),
                    params={"resolution": resolution, "max": max_candles}
                )
                d = await r.json()
                prices = d.get("prices", [])
                if not prices:
                    err = d.get("errorCode", d.get("error", "unknown"))
                    print(f"Capital OHLCV empty — epic={epic} status={r.status} err={err}")
                    return []
                candles = []
                for p in prices:
                    def _mid(key, _p=p):
                        px = _p.get(key, {})
                        b, a = px.get("bid", 0), px.get("ask", 0)
                        return (b + a) / 2 if b and a else (b or a)
                    snap = (
                        p.get("snapshotTime")
                        or p.get("openTimeUtc")
                        or p.get("closeTimeUtc")
                    )
                    candles.append({
                        "open":   _mid("openPrice"),
                        "high":   _mid("highPrice"),
                        "low":    _mid("lowPrice"),
                        "close":  _mid("closePrice"),
                        "volume": p.get("lastTradedVolume", 0),
                        "snapshot_time": snap,
                    })
                return candles
        except Exception as e:
            print(f"Capital OHLCV error — epic={epic}: {e}")
            return []

    async def close_position(self, deal_id: str) -> dict:
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.delete(
                    f"{self.base_url}/positions/{deal_id}",
                    headers=self.get_headers()
                )
                d = await r.json()
                return {
                    "status": "success" if r.status == 200 else "error",
                    "response": d
                }
        except Exception as e:
            return {"status": "error", "reason": str(e)}


    async def close_position_partial(self, deal_id: str, size: float) -> dict:
        """
        Attempt a partial close by passing size in the DELETE body.
        Capital.com demo may not support this — falls back to full close.
        Returns {"status", "partial": bool, "response"}.
        """
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.delete(
                    f"{self.base_url}/positions/{deal_id}",
                    headers=self.get_headers(),
                    json={"size": size}
                )
                d = await r.json()
                if r.status == 200:
                    return {"status": "success", "partial": True, "response": d}
                # Partial not supported — fall back to full close
                err = d.get("errorCode", "")
                print(f"Partial close unsupported ({err}) — falling back to full close")
                full = await self.close_position(deal_id)
                full["partial"] = False
                return full
        except Exception as e:
            return {"status": "error", "reason": str(e)}


capital_client = CapitalClient()