import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

# =========================
# ENV
# =========================
WALLET = os.environ["HYPERLIQUID_WALLET"]
AGENT_KEY = os.environ["HYPERLIQUID_AGENT_KEY"]

DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", 2))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", 0.01))

BASE_URL = constants.MAINNET_API_URL

# Correct xiangyu constructors
info = Info(BASE_URL)
exchange = Exchange(BASE_URL, WALLET, AGENT_KEY)

app = FastAPI()

# =========================
# Webhook schema
# =========================
class Signal(BaseModel):
    action: str
    coin: str
    leverage: float = DEFAULT_LEVERAGE
    risk_pct: float = MAX_RISK_PCT


# =========================
# Helpers
# =========================
def get_state():
    return info.user_state(WALLET)

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
    mids = info.all_mids()
    return float(mids[symbol])


# =========================
# Webhook
# =========================
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
        size = round((usd_risk * leverage) / price, 4)

        pos = get_position(symbol)

        # Close opposite position
        if pos != 0:
            if (pos > 0 and side == "SELL") or (pos < 0 and side == "BUY"):
                exchange.market_close(symbol)

        # Set leverage
        exchange.update_leverage(symbol, leverage)

        # Open position
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
