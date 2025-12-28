import os
import time
from fastapi import FastAPI, Request
from eth_account import Account
from hyperliquid.exchange import Exchange

# Load configuration from environment
PRIVATE_KEY = os.getenv("HYPERLIQUID_PRIVATE_KEY") or os.getenv("PRIVATE_KEY")
if not PRIVATE_KEY:
    raise RuntimeError("Private key not set. Please define HYPERLIQUID_PRIVATE_KEY in environment.")

# Determine API endpoints (default to testnet, switch to mainnet if configured)
USE_TESTNET = os.getenv("HYPERLIQUID_TESTNET", "true").lower() in ("true", "1", "yes")
if USE_TESTNET:
    BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL", "https://api.hyperliquid-testnet.xyz")
else:
    BASE_URL = os.getenv("HYPERLIQUID_PUBLIC_BASE_URL", "https://api.hyperliquid.xyz")

SYMBOL     = os.getenv("SYMBOL", "BTC")         # e.g. "BTC" for BTC/USD pair on Hyperliquid
ORDER_SIZE = float(os.getenv("ORDER_SIZE", "0.001"))  # position size to trade (in BTC, for example)

# Initialize wallet from private key
wallet = Account.from_key(PRIVATE_KEY)

app = FastAPI()
exchange = None  # will be initialized on startup

@app.on_event("startup")
def initialize_exchange():
    """Initialize the Hyperliquid exchange connection with retry to avoid rate-limit issues."""
    global exchange
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            exchange = Exchange(wallet, BASE_URL)
            print("✅ Hyperliquid Exchange initialized successfully.")
            break
        except Exception as e:
            print(f"⚠️  Exchange init attempt {attempt} failed: {e}")
            if attempt < attempts:
                time.sleep(1)  # wait a moment before retrying
    if exchange is None:
        print("❌ Failed to initialize Exchange on startup. Will attempt again on first webhook call.")

@app.post("/webhook")
async def handle_webhook(request: Request):
    """
    Handle TradingView webhook alerts. Expects JSON with an 'action' field 
    (e.g., {"action": "BUY"} or {"action": "SELL"}).
    On "BUY": close short (if any), then open long.
    On "SELL": close long (if any), then open short.
    """
    global exchange
    alert = await request.json()  # parse the JSON payload
    action = None
    # The alert may use different keys or formats; try a few common ones:
    if isinstance(alert, dict):
        action = alert.get("action") or alert.get("signal") or alert.get("alert")
    if not action and isinstance(alert, str):
        action = alert  # if the payload is just a raw string like "BUY" or "SELL"
    if not action:
        return {"error": "No 'action' specified in alert payload."}
    action = str(action).strip().lower()  # normalize action string

    # Ensure the exchange is initialized (in case startup init failed or was deferred)
    if exchange is None:
        try:
            exchange = Exchange(wallet, BASE_URL)
            print("✅ Hyperliquid Exchange initialized on-demand.")
        except Exception as e:
            err_msg = f"Exchange initialization failed: {e}"
            print(f"❌ {err_msg}")
            return {"error": err_msg}

    # Fetch current positions for our symbol
    address = exchange.wallet.address
    try:
        user_state = exchange.info.user_state(address)
    except Exception as e:
        # If fetching user state fails, we can still attempt orders (or abort)
        print(f"⚠️  Could not fetch user state: {e}")
        user_state = {}
    positions = {pos["position"]["coin"]: float(pos["position"]["szi"]) 
                 for pos in user_state.get("assetPositions", [])}
    current_size = positions.get(SYMBOL, 0.0)

    result = {}  # to store results of actions

    if action == "buy":
        # Close short position if currently short
        if current_size < 0:
            try:
                close_res = exchange.market_close(SYMBOL)
                result["closed_short"] = close_res
                print(f"Closed short position on {SYMBOL}.")
            except Exception as e:
                result["close_short_error"] = str(e)
                print(f"❌ Error closing short: {e}")
        # Open long position if not already long
        if current_size <= 0:
            try:
                open_res = exchange.market_open(SYMBOL, is_buy=True, sz=ORDER_SIZE)
                result["opened_long"] = open_res
                print(f"Opened long position on {SYMBOL} (size={ORDER_SIZE}).")
            except Exception as e:
                result["open_long_error"] = str(e)
                print(f"❌ Error opening long: {e}")
        else:
            result["note"] = "Already in a long position; no new long opened."
            print(f"⚠️  Alert was BUY, but already in a long position (size={current_size}).")

    elif action == "sell":
        # Close long position if currently long
        if current_size > 0:
            try:
                close_res = exchange.market_close(SYMBOL)
                result["closed_long"] = close_res
                print(f"Closed long position on {SYMBOL}.")
            except Exception as e:
                result["close_long_error"] = str(e)
                print(f"❌ Error closing long: {e}")
        # Open short position if not already short
        if current_size >= 0:
            try:
                open_res = exchange.market_open(SYMBOL, is_buy=False, sz=ORDER_SIZE)
                result["opened_short"] = open_res
                print(f"Opened short position on {SYMBOL} (size={ORDER_SIZE}).")
            except Exception as e:
                result["open_short_error"] = str(e)
                print(f"❌ Error opening short: {e}")
        else:
            result["note"] = "Already in a short position; no new short opened."
            print(f"⚠️  Alert was SELL, but already in a short position (size={current_size}).")

    else:
        return {"error": f"Unrecognized action '{action}'. Expected 'BUY' or 'SELL'."}

    return {"status": "ok", "symbol": SYMBOL, "action": action.upper(), "details": result}
