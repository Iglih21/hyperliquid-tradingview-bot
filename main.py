```python
import os
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from eth_account import Account
from hyperliquid.exchange import Exchange

# Configure basic logging
logging.basicConfig(level=logging.INFO)

# Load environment variables for credentials and defaults
WALLET_ADDR = os.getenv("HYPERLIQUID_WALLET")
PRIVATE_KEY = os.getenv("HYPERLIQUID_PRIVATE_KEY")
DEFAULT_LEVERAGE = os.getenv("DEFAULT_LEVERAGE")
MAX_RISK_PCT = os.getenv("MAX_RISK_PCT")

# Validate critical config
if not PRIVATE_KEY or not WALLET_ADDR:
    logging.error("Missing HYPERLIQUID_WALLET or HYPERLIQUID_PRIVATE_KEY environment variable.")
    # If running in an environment like Render, it's okay to raise an error to prevent deployment without creds
    raise RuntimeError("Hyperliquid credentials not configured")

# Initialize Hyperliquid Exchange client
try:
    wallet = Account.from_key(PRIVATE_KEY)
    if wallet.address.lower() != WALLET_ADDR.lower():
        logging.warning("Wallet address from private key does not match HYPERLIQUID_WALLET env variable.")
    exchange = Exchange(wallet)  # uses default base_url (mainnet):contentReference[oaicite:14]{index=14}
    logging.info(f"Hyperliquid Exchange client initialized for account: {wallet.address}")
except Exception as e:
    logging.error(f"Failed to initialize Hyperliquid client: {e}")
    # If the SDK initialization fails, halt the app startup
    raise

# Ensure numeric defaults for leverage and risk
try:
    default_leverage_val = int(DEFAULT_LEVERAGE) if DEFAULT_LEVERAGE else 1
except Exception:
    logging.error(f"Invalid DEFAULT_LEVERAGE value: {DEFAULT_LEVERAGE}")
    default_leverage_val = 1
try:
    default_risk_pct = float(MAX_RISK_PCT) if MAX_RISK_PCT else 1.0
except Exception:
    logging.error(f"Invalid MAX_RISK_PCT value: {MAX_RISK_PCT}")
    default_risk_pct = 1.0

app = FastAPI()

@app.post("/webhook")
def tradingview_webhook(payload: dict):
    """
    TradingView webhook endpoint to execute BTC perpetual trades on Hyperliquid.
    """
    action = payload.get("action") or payload.get("signal")
    if not action:
        # If the alert doesn't specify an action, return a 400 Bad Request
        logging.error("Received webhook with no action field")
        raise HTTPException(status_code=400, detail="No action specified in alert")
    action = action.upper()
    # Determine target leverage and risk percentage (overrides if provided in alert)
    leverage_val = default_leverage_val
    risk_pct = default_risk_pct
    if "leverage" in payload:
        try:
            leverage_val = int(payload["leverage"])
        except Exception as e:
            logging.error(f"Invalid leverage value in payload: {payload.get('leverage')}")
    if "risk_pct" in payload:
        try:
            risk_pct = float(payload["risk_pct"])
        except Exception as e:
            logging.error(f"Invalid risk_pct value in payload: {payload.get('risk_pct')}")

    # Constrain risk_pct to a reasonable range (0-100) just for safety
    if risk_pct > 100:
        risk_pct = 100.0
    if risk_pct < 0:
        risk_pct = 0.0

    try:
        # Fetch current BTC position (if any) to decide if we need to close it
        address = exchange.wallet.address
        user_state = exchange.info.user_state(address)
        current_position = None
        current_size = 0.0
        for pos in user_state.get("assetPositions", []):
            pos_info = pos.get("position", {})
            if pos_info.get("coin") == "BTC":
                # Found BTC position
                szi = float(pos_info.get("szi", 0))
                current_size = szi
                if abs(szi) < 1e-9:
                    current_position = "FLAT"
                elif szi > 0:
                    current_position = "LONG"
                elif szi < 0:
                    current_position = "SHORT"
                break

        logging.info(f"Current BTC position: {current_position} (size={current_size})")
        # Close opposite position if needed
        if action == "BUY" and current_position == "SHORT":
            result_close = exchange.market_close("BTC")
            if result_close:
                # Check status of close operation
                if isinstance(result_close, dict) and result_close.get("status") != "ok":
                    logging.error(f"Error closing short position: {result_close}")
                    return JSONResponse(content={"status": "error"})
            logging.info("Closed SHORT position to prepare for LONG")
        elif action == "SELL" and current_position == "LONG":
            result_close = exchange.market_close("BTC")
            if result_close:
                if isinstance(result_close, dict) and result_close.get("status") != "ok":
                    logging.error(f"Error closing long position: {result_close}")
                    return JSONResponse(content={"status": "error"})
            logging.info("Closed LONG position to prepare for SHORT")

        # If already in the desired position (no opposite position to close and not flat), we can decide to skip or adjust
        if action == "BUY" and current_position == "LONG":
            logging.info("Already in LONG position, no new trade executed")
            return {"action": "BUY", "size": abs(current_size), "price": None, "account_value": float(user_state['crossMarginSummary']['accountValue']), "status": "success"}
        if action == "SELL" and current_position == "SHORT":
            logging.info("Already in SHORT position, no new trade executed")
            return {"action": "SELL", "size": abs(current_size), "price": None, "account_value": float(user_state['crossMarginSummary']['accountValue']), "status": "success"}

        # Update leverage setting (cross margin) for BTC
        try:
            result_leverage = exchange.update_leverage(leverage_val, "BTC", is_cross=True)
            if isinstance(result_leverage, dict) and result_leverage.get("status") != "ok":
                logging.error(f"Leverage update failed: {result_leverage}")
                return JSONResponse(content={"status": "error"})
        except Exception as e:
            logging.error(f"Exception during leverage update: {e}")
            # If leverage update fails, abort the trade for safety
            return JSONResponse(content={"status": "error"})

        # Calculate position size (BTC) for the new trade based on risk_pct and account value
        # Refresh account value after any position close
        new_state = exchange.info.user_state(address)
        account_value = float(new_state['crossMarginSummary']['accountValue'])
        risk_fraction = risk_pct / 100.0
        risk_amount = account_value * risk_fraction  # USD to use as margin
        # current BTC price (mid price)
        price_info = exchange.info.all_mids()
        btc_price = float(price_info.get("BTC", 0))
        if btc_price <= 0:
            logging.error("Failed to retrieve BTC price for sizing")
            return JSONResponse(content={"status": "error"})
        # Position notional = risk_amount * leverage, position size in BTC:
        position_value = risk_amount * leverage_val
        size_btc = position_value / btc_price if btc_price > 0 else 0
        # Round size to allowed decimals
        try:
            meta = exchange.info.meta()
            sz_decimals = 0
            for asset in meta["universe"]:
                if asset["name"] == "BTC":
                    sz_decimals = asset.get("szDecimals", 0)
                    break
            size_btc = round(size_btc, sz_decimals)
        except Exception as e:
            # If meta fetch fails, just round to 3 decimal places as a fallback
            size_btc = round(size_btc, 3)
        if size_btc <= 0:
            logging.error(f"Calculated trade size is non-positive: {size_btc} BTC")
            return JSONResponse(content={"status": "error"})

        # Execute the market order to open the new position
        is_buy = True if action == "BUY" else False
        result_open = exchange.market_open("BTC", is_buy, size_btc)
        if isinstance(result_open, dict) and result_open.get("status") != "ok":
            logging.error(f"Market order failed: {result_open}")
            return JSONResponse(content={"status": "error"})
        logging.info(f"Opened {'LONG' if is_buy else 'SHORT'} position of size {size_btc} BTC")

        # Get updated account state to retrieve fill price and new equity
        final_state = exchange.info.user_state(address)
        account_value = float(final_state['crossMarginSummary']['accountValue'])
        # Find BTC position in final state (should exist if trade succeeded)
        final_price = None
        final_size = 0.0
        for pos in final_state.get("assetPositions", []):
            pos_info = pos.get("position", {})
            if pos_info.get("coin") == "BTC" and pos_info.get("entryPx") is not None:
                final_size = abs(float(pos_info.get("szi", 0)))
                final_price = float(pos_info.get("entryPx", 0))
                break

        return {
            "action": "BUY" if is_buy else "SELL",
            "size": final_size,
            "price": final_price,
            "account_value": account_value,
            "status": "success"
        }

    except Exception as e:
        # Catch-all for any unexpected errors
        logging.error(f"Exception in webhook processing: {e}")
        return JSONResponse(content={"status": "error"})

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
```python
