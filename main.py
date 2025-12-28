import os
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

WALLET = os.environ["HYPERLIQUID_WALLET"]
AGENT_KEY = os.environ["HYPERLIQUID_AGENT_KEY"]

DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", 2))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", 0.02))

BASE_URL = constants.MAINNET_API_URL

app = FastAPI()

_info = None
_exchange = None
_last_info_fetch = 0
_cached_state = None
_cached_mids = None

RATE_LIMIT_COOLDOWN = 5  # seconds

class Signal(BaseModel):
    action: str
    coin: str
    leverage: float = DEFAULT_LEVERAGE
    risk_pct: float = MAX_RISK_PCT

def get_info():
    global _info
    if _info is None:
        _info = Info(BASE_URL, skip_ws=True)
    return _info

def get_exchange():
    global _exchange
    if _exchange is None:
        _exchange = Exchange(BASE_URL, WALLET, AGENT_KEY, skip_ws=True)
    return _exchange

def refresh_state():
    global _cached_state, _cached_mids, _last_info_fetch

    now = time.time()
    if now - _last_info_fetch < 2:
        return

    try:
        info = get_info()
        _cached_state = info.user_state(WALLET)
        _cached_mids = info.all_mids()
        _last_info_fetch = now
    except Exception as e:
        if "429" in str(e):
            time.sleep(RATE_LIMIT_COOLDOWN)
        raise

def get_balance():
    refresh_state()
    return float(_cached_state["marginSummary"]["accountValue"])

def get_position(symbol):
    refresh_state()
    for p in _cached_state.get("assetPositions", []):
        pos = p["position"]
        if pos["coin"] == symbol:
            return float(pos["szi"])
    return 0.0

def get_price(symbol):
    refresh_state()
    return float(_cached_mids[symbol])

@app.post("/webhook")
def webhook(signal: Signal):
    try:
        symbol = signal.coin.upper()
        side = signal.action.upper()

        if side not in ["BUY", "SELL"]:
            raise HTTPException(400, "action must be BUY or SELL")

        leverage = float(signal.leverage)
        risk_pct = min(float(signal.risk_pct), MAX_RISK_PCT)

        balance = get_balance()
        price = get_price(symbol)

        usd_risk = balance * risk_pct
        size = (usd_risk * leverage) / price

        if size <= 0:
            raise HTTPException(400, "Calculated size is zero")

        size = round(size, 4)

        exchange = get_exchange()
        pos = get_position(symbol)

        if pos != 0:
            if (pos > 0 and side == "SELL") or (pos < 0 and side == "BUY"):
                exchange.market_close(symbol)

        exchange.update_leverage(symbol, leverage)
        exchange.market_open(symbol, side == "BUY", size)

        return {
            "status": "executed",
            "symbol": symbol,
            "side": side,
            "size": size,
            "price": price,
            "account_value": balance
        }

    except Exception as e:
        if "429" in str(e):
            raise HTTPException(429, "Hyperliquid rate limited. Try again in 10 seconds.")
        raise HTTPException(500, str(e))
