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

# Incoming TradingView webhook schema
class WebhookSignal(BaseModel):
    action: str
    coin: str
    leverage: float = DEFAULT_LEVERAGE
    risk_pct: float = MAX_RISK_PCT
    mode: str = "reverse"   # reverse = auto close opposite positions


@app.post("/webhook")
async def handle_webhook(signal: WebhookSignal):
    try:
        action = signal.action.upper()
        if action not in ("BUY", "SELL"):
            raise HTTPException(status_code=400, detail="Invalid action")

        coin = signal.coin.upper()
        symbol = f"{coin}-USDC"
        leverage = signal.leverage
        risk_pct = min(signal.risk_pct, MAX_RISK_PCT)

        # Fetch account state
        state = client.info.user_state(HYPERLIQUID_WALLET)

        # This is the ONLY tradable balance on Hyperliquid
        balance = float(state["crossMarginSummary"]["freeCollateral"])

        if balance <= 1:
            raise HTTPException(status_code=400, detail="No free collateral")

        # Get mark price
        prices = client.info.all_mids()
        price = float(prices[symbol])

        # USD exposure
        usd_exposure = balance * risk_pct * leverage

        # Contract size
        quantity = round(usd_exposure / price, 4)

        # Get open positions
        positions = state.get("assetPositions", [])

        # Close opposite side if exists
        for pos in positions:
            if pos["position"]["coin"] == coin:
                pos_size = float(pos["position"]["szi"])
                if (pos_size > 0 and action == "SELL") or (pos_size < 0 and action == "BUY"):
                    client.market_close(symbol)

        # Place new order
        is_buy = action == "BUY"

        client.order(
            symbol,
            is_buy,
            quantity,
            {"market": {}},
            reduce_only=False
        )

        return {
            "status": "order placed",
            "symbol": symbol,
            "side": action,
            "quantity": quantity,
            "usd_used": usd_exposure,
            "price": price,
            "balance": balance
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
