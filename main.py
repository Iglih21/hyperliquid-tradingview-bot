import os
import math
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

# ============================================================
# ENV
# ============================================================
ACCOUNT = os.getenv("HYPERLIQUID_WALLET")           # 0xcE7ed6ffbf75807b0f767EaF858b847660Db7190
PRIVATE_KEY = os.getenv("HYPERLIQUID_PRIVATE_KEY") # API Wallet Private Key
DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", "3"))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", "0.02"))

if not ACCOUNT or not PRIVATE_KEY:
    raise RuntimeError("Missing HYPERLIQUID_WALLET or HYPERLIQUID_PRIVATE_KEY")

BASE_URL = constants.MAINNET_API_URL

# ============================================================
# CLIENTS  (OFFICIAL SDK v0.21.0)
# ============================================================
info = Info(BASE_URL)
exchange = Exchange(ACCOUNT, PRIVATE_KEY, BASE_URL)

print("✅ Connected to Hyperliquid:", ACCOUNT)

app = FastAPI()

# ============================================================
# HELPERS
# ============================================================
def get_state():
    return info.user_state(ACCOUNT)

def get_balance():
    return float(get_state()["marginSummary"]["accountValue"])

def get_position():
    for p in get_state()["assetPositions"]:
        pos = p["position"]
        if pos["coin"] == "BTC":
            return float(pos["szi"])
    return 0.0

def get_price():
    return float(info.all_mids()["BTC"])

def get_sz_decimals():
    meta = info.meta()
    for m in meta["universe"]:
        if m["name"] == "BTC":
            return int(m["szDecimals"])
    return 3

def round_down(v, d):
    f = 10 ** d
    return math.floor(v * f) / f

# ============================================================
# API
# ============================================================
class Signal(BaseModel):
    action: str
    risk_pct: float = MAX_RISK_PCT
    leverage: float = DEFAULT_LEVERAGE

@app.get("/")
def root():
    return {"ok": True}

@app.post("/webhook")
def trade(signal: Signal):
    side = signal.action.upper()
    if side not in ["BUY", "SELL"]:
        raise HTTPException(400, "action must be BUY or SELL")

    balance = get_balance()
    price = get_price()
    pos = get_position()
    sz_dec = get_sz_decimals()

    risk = min(signal.risk_pct, MAX_RISK_PCT)
    notional = balance * risk * signal.leverage
    size = round_down(notional / price, sz_dec)

    if size <= 0:
        raise HTTPException(400, "Size too small")

    print("Price:", price)
    print("Balance:", balance)
    print("Current Position:", pos)
    print("Order Size:", size)

    # Close opposite side
    if pos > 0 and side == "SELL":
        exchange.market_close("BTC")
    if pos < 0 and side == "BUY":
        exchange.market_close("BTC")

    exchange.update_leverage("BTC", signal.leverage)

    is_buy = side == "BUY"
    resp = exchange.market_open("BTC", is_buy, size)

    print("Exchange response:", resp)

    return {
        "status": "executed",
        "side": side,
        "size": size,
        "price": price,
        "exchange": resp
    }
