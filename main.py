import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

# =========================
# ENVIRONMENT
# =========================
PRIVATE_KEY = os.environ["HYPERLIQUID_AGENT_KEY"]
WALLET = os.environ["HYPERLIQUID_WALLET"]
DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", 10))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", 0.04))

BASE_URL = constants.MAINNET_API_URL

# =========================
# INIT HYPERLIQUID
# =========================
wallet = Account.from_key(PRIVATE_KEY)

exchange = Exchange(
    wallet=wallet,
    base_url=BASE_URL,
    account_address=WALLET,
)

info = Info(BASE_URL)

# =========================
# FASTAPI
# =========================
app = FastAPI()

class Signal(BaseModel):
    action: str
    coin: str
    leverage: float | None = None
    risk_pct: float | None = None
    mode: str = "reverse"

# =========================
# HELPERS
# =========================

def get_balance():
    state = info.user_state(WALLET)
    return float(state["marginSummary"]["availableBalance"])

def get_position(symbol):
    state = info.user_state(WALLET)
    for p in state["assetPositions"]:
        pos = p["position"]
        if pos["coin"] == symbol:
            return pos
    return None

def market_order(symbol, is_buy, size, reduce_only=False):
    exchange.order(
        symbol,
        is_buy,
        size,
        None,
        {
            "market": {}
        },
        reduce_only=reduce_only
    )

# =========================
# WEBHOOK
# =========================

@app.post("/webhook")
def webhook(signal: Signal):

    action = signal.action.upper()
    if action not in ["BUY", "SELL"]:
        raise HTTPException(400, "Invalid action")

    coin = signal.coin.upper()
    leverage = signal.leverage or DEFAULT_LEVERAGE
    risk_pct = min(signal.risk_pct or MAX_RISK_PCT, MAX_RISK_PCT)

    # Balance
    balance = get_balance()
    if balance <= 0:
        raise HTTPException(400, "No available balance")

    # Price
    price = float(info.all_mids()[coin])

    # Exposure
    usd = balance * risk_pct * leverage
    size = round(usd / price, 4)

    # Existing position
    pos = get_position(coin)

    # Close opposite if exists
    if pos:
        pos_size = float(pos["szi"])
        if action == "BUY" and pos_size < 0:
            market_order(coin, True, abs(pos_size), True)
        if action == "SELL" and pos_size > 0:
            market_order(coin, False, abs(pos_size), True)

    # Open new
    if action == "BUY":
        market_order(coin, True, size, False)
    else:
        market_order(coin, False, size, False)

    return {
        "status": "executed",
        "symbol": coin,
        "side": action,
        "size": size
    }
