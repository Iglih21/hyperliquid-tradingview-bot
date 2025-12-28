import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

# ======================
# ENV
# ======================
PRIVATE_KEY = os.environ["HYPERLIQUID_AGENT_KEY"]
ACCOUNT_ADDRESS = os.environ["HYPERLIQUID_WALLET"]

DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", 10))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", 0.04))

app = FastAPI()

# ======================
# MODEL
# ======================
class WebhookSignal(BaseModel):
    action: str
    coin: str
    leverage: float = DEFAULT_LEVERAGE
    risk_pct: float = MAX_RISK_PCT
    mode: str = "reverse"

# ======================
# LAZY CLIENT
# ======================
client = None

def get_client():
    global client
    if client is None:
        wallet = Account.from_key(PRIVATE_KEY)
        client = Exchange(
            wallet=wallet,
            base_url=constants.MAINNET_API_URL,
            account_address=ACCOUNT_ADDRESS
        )
    return client

# ======================
# HELPERS
# ======================
def get_balance(c):
    state = c.info.user_state(ACCOUNT_ADDRESS)
    return float(state["marginSummary"]["accountValue"])

def get_price(c, symbol):
    book = c.info.l2_snapshot(symbol)
    bid = float(book["levels"][0][0]["px"])
    ask = float(book["levels"][1][0]["px"])
    return (bid + ask) / 2

def get_position(c, symbol):
    state = c.info.user_state(ACCOUNT_ADDRESS)
    for pos in state["positions"]:
        if pos["coin"] == symbol:
            if float(pos["szi"]) != 0:
                return pos
    return None

# ======================
# WEBHOOK
# ======================
@app.post("/webhook")
async def webhook(signal: WebhookSignal):
    c = get_client()

    action = signal.action.upper()
    if action not in ["BUY", "SELL"]:
        raise HTTPException(400, "Invalid action")

    symbol = signal.coin.upper()
    leverage = signal.leverage
    risk = min(signal.risk_pct, MAX_RISK_PCT)

    balance = get_balance(c)
    if balance <= 0:
        raise HTTPException(400, "No funds")

    price = get_price(c, symbol)
    usd = balance * risk * leverage
    size = usd / price

    pos = get_position(c, symbol)

    # close opposite
    if pos:
        side = "B" if float(pos["szi"]) < 0 else "S"
        if (action == "BUY" and side == "S") or (action == "SELL" and side == "B"):
            c.order(symbol, side, abs(float(pos["szi"])), {"reduceOnly": True})

    # open new
    side = "B" if action == "BUY" else "S"

    c.order(
        symbol,
        side,
        size,
        {
            "leverage": leverage,
            "isCross": False,
            "type": "market"
        }
    )

    return {
        "status": "ok",
        "symbol": symbol,
        "side": side,
        "usd": usd,
        "size": size
    }
