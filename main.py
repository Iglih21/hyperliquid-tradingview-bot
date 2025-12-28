import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from hyperliquid.exchange import Exchange

BASE_URL = "https://api.hyperliquid.xyz"

PRIVATE_KEY = os.environ["HYPERLIQUID_AGENT_KEY"]
WALLET = os.environ["HYPERLIQUID_WALLET"]
DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", 10))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", 0.04))

# Correct initialization for xiangyu SDK
client = Exchange(
    BASE_URL,
    WALLET,
    PRIVATE_KEY
)

app = FastAPI()

class WebhookSignal(BaseModel):
    action: str
    coin: str
    leverage: float = DEFAULT_LEVERAGE
    risk_pct: float = MAX_RISK_PCT
    mode: str = "reverse"

@app.post("/webhook")
async def webhook(signal: WebhookSignal):
    try:
        side = signal.action.upper()
        if side not in ["BUY", "SELL"]:
            raise HTTPException(400, "Invalid action")

        coin = signal.coin.upper()
        symbol = f"{coin}-USDC"
        leverage = signal.leverage
        risk_pct = min(signal.risk_pct, MAX_RISK_PCT)

        # Fetch state
        state = client.info.user_state(WALLET)
        free = float(state["crossMarginSummary"]["freeCollateral"])

        if free <= 0:
            raise HTTPException(400, "No free collateral")

        # Price
        mids = client.info.all_mids()
        price = float(mids[symbol])

        usd = free * risk_pct * leverage
        size = round(usd / price, 4)

        # Close opposite positions (reverse mode)
        for p in state.get("assetPositions", []):
            if p["position"]["coin"] == coin:
                szi = float(p["position"]["szi"])
                if (szi > 0 and side == "SELL") or (szi < 0 and side == "BUY"):
                    client.market_close(symbol)

        # Place order
        is_buy = side == "BUY"
        client.order(symbol, is_buy, size, {"market": {}}, False)

        return {
            "status": "executed",
            "symbol": symbol,
            "side": side,
            "size": size,
            "usd_used": usd,
            "price": price,
            "free_collateral": free
        }

    except Exception as e:
        raise HTTPException(500, str(e))
