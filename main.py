import os
import math
import logging
from fastapi import FastAPI, HTTPException
from eth_account import Account
from hyperliquid.exchange import Exchange

logging.basicConfig(level=logging.ERROR)

# ================================
# ENV
# ================================
WALLET = os.getenv("HYPERLIQUID_WALLET")
PRIVATE_KEY = os.getenv("HYPERLIQUID_PRIVATE_KEY")

DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", "3"))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", "2"))  # percent

if not WALLET or not PRIVATE_KEY:
    raise RuntimeError("Missing HYPERLIQUID_WALLET or HYPERLIQUID_PRIVATE_KEY")

# ================================
# HYPERLIQUID CLIENT
# ================================
acct = Account.from_key(PRIVATE_KEY)

if acct.address.lower() != WALLET.lower():
    raise RuntimeError("PRIVATE KEY does not match API wallet address")

exchange = Exchange(acct)

app = FastAPI()

# ================================
# HELPERS
# ================================
def get_state():
    return exchange.info.user_state(acct.address)

def get_balance():
    return float(get_state()["crossMarginSummary"]["accountValue"])

def get_position():
    for p in get_state()["assetPositions"]:
        pos = p["position"]
        if pos["coin"] == "BTC":
            return float(pos["szi"]), float(pos.get("entryPx", 0))
    return 0.0, 0.0

def get_price():
    return float(exchange.info.all_mids()["BTC"])

def get_decimals():
    meta = exchange.info.meta()
    for m in meta["universe"]:
        if m["name"] == "BTC":
            return int(m["szDecimals"])
    return 3

def round_down(v, d):
    f = 10 ** d
    return math.floor(v * f) / f

# ================================
# API
# ================================
@app.get("/")
def root():
    return {"ok": True}

@app.post("/webhook")
def trade(signal: dict):
    try:
        side = signal.get("action", "").upper()
        if side not in ["BUY", "SELL"]:
            raise HTTPException(400, "action must be BUY or SELL")

        leverage = float(signal.get("leverage", DEFAULT_LEVERAGE))
        risk_pct = min(float(signal.get("risk_pct", MAX_RISK_PCT)), MAX_RISK_PCT)

        balance = get_balance()
        price = get_price()
        pos, entry = get_position()
        decimals = get_decimals()

        risk_usd = balance * (risk_pct / 100)
        notional = risk_usd * leverage
        size = round_down(notional / price, decimals)

        if size <= 0:
            raise HTTPException(400, "Trade size too small")

        # Close opposite
        if pos > 0 and side == "SELL":
            exchange.market_close("BTC")
        if pos < 0 and side == "BUY":
            exchange.market_close("BTC")

        exchange.update_leverage(leverage, "BTC", is_cross=True)

        is_buy = side == "BUY"
        exchange.market_open("BTC", is_buy, size)

        # Fetch updated state
        new_pos, new_entry = get_position()
        new_balance = get_balance()

        return {
            "status": "success",
            "side": side,
            "size": abs(new_pos),
            "price": new_entry,
            "account_value": new_balance
        }

    except Exception as e:
        logging.error(str(e))
        return {"status": "error"}
