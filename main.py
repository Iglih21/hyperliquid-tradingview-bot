import os
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

# ==============================
# ENV
# ==============================

WALLET = os.environ["HYPERLIQUID_WALLET"]
AGENT_KEY = os.environ["HYPERLIQUID_AGENT_KEY"]

BASE_URL = constants.MAINNET_API_URL

DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", 2))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", 0.01))

app = FastAPI()

# ==============================
# Safe singletons
# ==============================

_info = None
_exchange = None
_last_init = 0

def reset_clients():
    global _info, _exchange
    _info = None
    _exchange = None

def get_info():
    global _info, _last_init

    # refresh every 5 minutes to avoid dead websocket
    if _info is None or time.time() - _last_init > 300:
        _info = Info(BASE_URL, skip_ws=True)
        _last_init = time.time()

    return _info

def get_exchange():
    global _exchange

    if _exchange is None:
        _exchange = Exchange(
            BASE_URL,
            WALLET,
            AGENT_KEY,
            skip_ws=True   # 🚨 prevents Cloudflare ban
        )

    return _exchange

# ==============================
# Models
# ==============================

class Signal(BaseModel):
    action: str
    coin: str
    leverage: float = DEFAULT_LEVERAGE
    risk_pct: float = MAX_RISK_PCT

# ==============================
# Helpers
# ==============================

def get_state():
    return get_info().user_state(WALLET)

def get_balance():
    state = get_state()
    return float(state["marginSummary"]["accountValue"])

def get_position(symbol):
    state = get_state()
    for p in state.get("assetPositions", []):
        pos = p["position"]
        if pos["coin"] == symbol:
            return float(pos["szi"])
    return 0.0

def get_price(symbol):
    mids = get_info().all_mids()
    return float(mids[symbol])

# ==============================
# Webhook
# ==============================

@app.post("/webhook")
def webhook(signal: Signal):
    try:
        symbol = signal.coin.upper()
        side = signal.action.upper()

        if side not in ["BUY", "SELL"]:
            raise HTTPException(400, "action must be BUY or SELL")

        leverage = float(signal.leverage)
        risk_pct = min(float(signal.risk_pct), MAX_RISK_PCT)

        price = get_price(symbol)
        balance = get_balance()

        usd_risk = balance * risk_pct
        size = round((usd_risk * leverage) / price, 5)

        if size <= 0:
            raise HTTPException(400, "position size too small")

        pos = get_position(symbol)
        exchange = get_exchange()

        # Close opposite side
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
        reset_clients()
        raise HTTPException(500, str(e))
