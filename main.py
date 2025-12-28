import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import hyperliquid

HYPERLIQUID_AGENT_KEY = os.environ["HYPERLIQUID_AGENT_KEY"]
HYPERLIQUID_WALLET = os.environ["HYPERLIQUID_WALLET"]
DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", 10))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", 0.04))

# Initialize hyperliquid client
client = hyperliquid.Client(wallet_address=HYPERLIQUID_WALLET, agent_key=HYPERLIQUID_AGENT_KEY)

app = FastAPI()

class WebhookSignal(BaseModel):
    action: str
    coin: str
    leverage: float = DEFAULT_LEVERAGE
    risk_pct: float = MAX_RISK_PCT
    mode: str = "reverse"

@app.post("/webhook")
async def handle_webhook(signal: WebhookSignal):
    action = signal.action.upper()
    if action not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="Invalid action")

    coin = signal.coin.upper()
    pair = f"{coin}-USDC"
    leverage = signal.leverage or DEFAULT_LEVERAGE
    risk_pct = min(signal.risk_pct or MAX_RISK_PCT, MAX_RISK_PCT)

    # Fetch account info for available balance
    account_info = client.get_account_overview()
    # Use freeCollateral as available balance (in USDC)
    balance = float(account_info.get("freeCollateral", 0))

    if balance <= 0:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    # Fetch mark price for the trading pair
    ticker = client.get_ticker(pair)
    price = float(ticker.get("markPrice"))

    # Calculate position size in base units
    usd_exposure = balance * risk_pct * leverage
    quantity = usd_exposure / price

    # Check existing positions
    open_positions = client.get_open_positions()
    for pos in open_positions:
        if pos.get("symbol") == pair:
            pos_side = pos.get("side")
            pos_size = float(pos.get("size"))
            if (action == "BUY" and pos_side == "short") or (action == "SELL" and pos_side == "long"):
                # Close the opposite position
                close_side = "buy" if pos_side == "short" else "sell"
                client.place_order(
                    symbol=pair,
                    side=close_side,
                    quantity=pos_size,
                    order_type="market",
                    reduce_only=True,
                    leverage=leverage,
                    isolated=True,
                )

    # Place new order
    order_side = "buy" if action == "BUY" else "sell"
    client.place_order(
        symbol=pair,
        side=order_side,
        quantity=quantity,
        order_type="market",
        reduce_only=False,
        leverage=leverage,
        isolated=True,
    )

    return {"status": "order placed", "side": order_side, "quantity": quantity}
