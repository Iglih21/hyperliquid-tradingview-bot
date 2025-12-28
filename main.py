import os
import math
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

# ============================
# CONFIG
# ============================

WALLET = os.environ.get("HYPERLIQUID_WALLET")
AGENT_KEY = os.environ.get("HYPERLIQUID_AGENT_KEY")

if not WALLET or not AGENT_KEY:
    raise RuntimeError("Missing HYPERLIQUID_WALLET or HYPERLIQUID_AGENT_KEY")

BASE_URL = constants.MAINNET_API_URL

DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", "3"))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", "0.02"))
MIN_USD_NOTIONAL = float(os.getenv("MIN_USD_NOTIONAL", "10"))

# ============================
# FASTAPI
# ============================

app = FastAPI(title="Hyperliquid TradingView Bot")

# ============================
# OFFICIAL EXCHANGE CLIENT
# ============================

# THIS is the correct constructor for the official SDK
exchange = Exchange(
    wallet=WALLET,
    agent_key=AGENT_KEY,
    base_url=BASE_URL
)

# We always use the internal Info client from Exchange
info = exchange.info

# ============================
# MODELS
# ============================

class Signal(BaseModel):
    action: str
    coin: str
    leverage: float = DEFAULT_LEVERAGE
    risk_pct: float = MAX_RISK_PCT

# ============================
# HELPERS
# ============================

def get_state():
    return info.user_state(WALLET)

def get_balance():
    return float(get_state()["marginSummary"]["accountValue"])

def get_position(coin):
    for p in get_state().get("assetPositions", []):
        pos = p["position"]
        if pos["coin"] == coin:
            return float(pos["szi"])
    return 0.0

def get_price(coin):
    mids = info.all_mids()
    return float(mids[coin])

def get_sz_decimals(coin):
    meta = info.meta()
    for asset in meta["universe"]:
        if asset["name"] == coin:
            return int(asset["szDecimals"])
    return 4

def round_down(value, decimals):
    factor = 10 ** decimals
    return math.floor(value * factor) / factor

# ============================
# ROUTES
# ============================

@app.get("/")
def root():
    return {"ok": True}

@app.post("/webhook")
def webhook(signal: Signal):
    try:
        coin = signal.coin.upper()
        side = signal.action.upper()

        if side not in ["BUY", "SELL"]:
            raise HTTPException(400, "action must be BUY or SELL")

        leverage = float(signal.leverage)
        risk_pct = min(float(signal.risk_pct), MAX_RISK_PCT)

        balance = get_balance()
        price = get_price(coin)

        usd_risk = balance * risk_pct
        notional = usd_risk * leverage

        if notional < MIN_USD_NOTIONAL:
            notional = MIN_USD_NOTIONAL

        raw_size = notional / price

        decimals = get_sz_decimals(coin)
        size = round_down(raw_size, decimals)

        if size <= 0:
            raise HTTPException(400, "Position size is zero")

        pos = get_position(coin)

        # Close opposite position
        if pos != 0:
            if (pos > 0 and side == "SELL") or (pos < 0 and side == "BUY"):
                exchange.market_close(coin)

        exchange.update_leverage(coin, leverage)

        exchange.market_open(
            coin,
            side == "BUY",
            size
        )

        return {
            "status": "executed",
            "coin": coin,
            "side": side,
            "size": size,
            "price": price,
            "account_value": balance
        }

    except Exception as e:
        raise HTTPException(500, str(e))
