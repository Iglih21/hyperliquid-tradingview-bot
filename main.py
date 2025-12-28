import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

PRIVATE_KEY = os.environ["HYPERLIQUID_AGENT_KEY"]
WALLET = os.environ["HYPERLIQUID_WALLET"]
DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", 10))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", 0.04))

client = Exchange(
    PRIVATE_KEY,
    WALLET,
    constants.MAINNET_API_URL
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
        pair = f"{coin}-USDC"

        leverage = signal.leverage
        risk_pct = min(signal.risk_pct, MAX_RISK_PCT)

        state = client.get_account_overview()
        balance = float(state["freeCollateral"])

        if balance <= 0:
            raise HTTPException(400, "No free collateral")

        ticker = client.get_ticker(pair)
        price = float(ticker["markPrice"])

        usd = balance * risk_pct * leverage
        size = round(usd / price, 4)

        # Close opposite
        for pos in client.get_open_positions():
            if pos["symbol"] == pair:
                if (pos["side"] == "long" and side == "SELL") or (pos["side"] == "short" and side == "BUY"):
                    client.place_order(
                        symbol=pair,
                        side="sell" if pos["side"] == "long" else "buy",
                        quantity=float(pos["size"]),
                        order_type="market",
                        reduce_only=True,
                        leverage=leverage,
                        isolated=True
                    )

        order_side = "buy" if side == "BUY" else "sell"

        client.place_order(
            symbol=pair,
            side=order_side,
            quantity=size,
            order_type="market",
            reduce_only=False,
            leverage=leverage,
            isolated=True
        )

        return {
            "status": "executed",
            "symbol": pair,
            "side": side,
            "size": size,
            "usd_used": usd,
            "price": price
        }

    except Exception as e:
        raise HTTPException(500, str(e))
