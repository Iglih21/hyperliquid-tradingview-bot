import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

# Load environment variables
HYPERLIQUID_AGENT_KEY = os.environ["HYPERLIQUID_AGENT_KEY"]
HYPERLIQUID_WALLET = os.environ["HYPERLIQUID_WALLET"]
DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", 10))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", 0.04))

# Initialize Hyperliquid Exchange
client = Exchange(
    HYPERLIQUID_AGENT_KEY,
    HYPERLIQUID_WALLET,
    constants.MAINNET_API_URL
)

app = FastAPI()

class WebhookSignal(BaseModel):
    action: str
    coin: str
    leverage: float | None = None
    risk_pct: float | None = None
    mode: str = "reverse"

@app.post("/webhook")
async def handle_webhook(signal: WebhookSignal):
    action = signal.action.upper()
    if action not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="Invalid action")

    coin = signal.coin.upper()
    symbol = f"{coin}-USDC"

    leverage = signal.leverage if signal.leverage else DEFAULT_LEVERAGE
    risk_pct = min(signal.risk_pct if signal.risk_pct else MAX_RISK_PCT, MAX_RISK_PCT)

    # Fetch account state
    account = client.info.user_state(HYPERLIQUID_WALLET)
    balance = float(account["crossMarginSummary"]["availableBalance"])

    if balance <= 1:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    # Fetch mid price
    mids = client.info.all_mids()
    price = float(mids[symbol])

    # Calculate USD exposure
    usd_exposure = balance * risk_pct * leverage
    size = usd_exposure / price

    # Fetch open positions
    positions = account["assetPositions"]
    for pos in positions:
        if pos["position"]["coin"] == coin:
            pos_size = float(pos["position"]["szi"])
            if pos_size != 0:
                is_long = pos_size > 0
                if (action == "BUY" and not is_long) or (action == "SELL" and is_long):
                    # Close opposite side
                    client.exchange.market_close(coin)

    # Open new position
    if action == "BUY":
        client.exchange.market_open(coin, True, size, leverage)
    else:
        client.exchange.market_open(coin, False, size, leverage)

    return {
        "status": "executed",
        "coin": coin,
        "side": action,
        "usd_exposure": usd_exposure,
        "size": size
    }
