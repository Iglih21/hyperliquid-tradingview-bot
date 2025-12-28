import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

# ===========================
# ENVIRONMENT
# ===========================

WALLET = os.environ["HYPERLIQUID_WALLET"]
AGENT_KEY = os.environ["HYPERLIQUID_AGENT_KEY"]

DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", 2))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", 0.01))

BASE_URL = constants.MAINNET_API_URL

# ===========================
# FASTAPI
# ===========================

app = FastAPI()

# ===========================
# SINGLETON CLIENTS (NO WS)
# ===========================

_info = None
_exchange = None

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

# ===========================
# MODELS
# ===========================

class Signal(BaseModel):
    action: str
    coin: str
    leverage: float = DEFAULT_LEVERAGE
    risk_pct: float = MAX_RISK_PCT

# ===========================
# HELPERS
# ===========================

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

# ===========================
# WEBHOOK
# ===========================

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

        # Close opposite side
        if pos != 0:
            if (pos > 0 and side == "SELL") or (pos < 0 and side == "BUY"):
                exchange.market_close(symbol)

        # Set leverage
        exchange.update_leverage(symbol, leverage)

        # Open new position
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
        raise HTTPException(500, str(e))
