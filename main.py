import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

# =========================
# ENVIRONMENT VARIABLES
# =========================
PRIVATE_KEY = os.environ["HYPERLIQUID_AGENT_KEY"]     # API Wallet Private Key
ACCOUNT_ADDRESS = os.environ["HYPERLIQUID_WALLET"]    # Main Hyperliquid account address

DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", 10))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", 0.04))

# =========================
# WALLET + CLIENT
# =========================
wallet = Account.from_key(PRIVATE_KEY)

client = Exchange(
    wallet=wallet,
    base_url=constants.MAINNET_API_URL,
    account_address=ACCOUNT_ADDRESS
)

app = FastAPI()

# =========================
# WEBHOOK MODEL
# =========================
class WebhookSignal(BaseModel):
    action: str
    coin: str
    leverage: float = DEFAULT_LEVERAGE
    risk_pct: float = MAX_RISK_PCT
    mode: str = "reverse"

# =========================
# HELPER FUNCTIONS
# =========================
def get_available_balance():
    state = client.info.user_state(ACCOUNT_ADDRESS)

    # Hyperliquid returns cross margin collateral here
    balance = float(state["marginSummary"]["accountValue"])
    return balance

def get_price(symbol):
    book = client.info.l2_snapshot(symbol)
    best_bid = float(book["levels"][0][0]["px"])
    best_ask = float(book["levels"][1][0]["px"])
    return (best_bid + best_ask) / 2

def get_open_position(symbol):
    state = client.info.user_state(ACCOUNT_ADDRESS)
    for pos in state["positions"]:
        if pos["coin"] == symbol:
            size = float(pos["szi"])
            if size != 0:
                return pos
    return None

# =========================
# WEBHOOK
# =========================
@app.post("/webhook")
async def webhook(signal: WebhookSignal):

    action = signal.action.upper()
    if action not in ["BUY", "SELL"]:
        raise HTTPException(400, "Invalid action")

    coin = signal.coin.upper()
    symbol = coin

    leverage = signal.leverage or DEFAULT_LEVERAGE
    risk_pct = min(signal.risk_pct or MAX_RISK_PCT, MAX_RISK_PCT)

    # Balance
    balance = get_available_balance()
    if balance <= 0:
        raise HTTPException(400, "No balance")

    # Price
    price = get_price(symbol)

    # Exposure
    usd_size = balance * risk_pct * leverage
    size = usd_size / price

    # Existing position
    pos = get_open_position(symbol)

    # Close opposite if needed
    if pos:
        side = "B" if float(pos["szi"]) < 0 else "S"
        if (action == "BUY" and side == "S") or (action == "SELL" and side == "B"):
            client.order(symbol, side, abs(float(pos["szi"])), {"reduceOnly": True})

    # Open new
    side = "B" if action == "BUY" else "S"

    client.order(
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
        "status": "executed",
        "action": action,
        "symbol": symbol,
        "usd_size": usd_size,
        "size": size,
        "leverage": leverage
    }
