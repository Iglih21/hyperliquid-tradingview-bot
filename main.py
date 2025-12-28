import os
import math
import time
import inspect
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

# =========================
# ENV / CONFIG
# =========================
WALLET = os.getenv("HYPERLIQUID_WALLET")
AGENT_KEY = os.getenv("HYPERLIQUID_AGENT_KEY")

# Optional override if you ever want testnet later
BASE_URL = os.getenv("HYPERLIQUID_BASE_URL", constants.MAINNET_API_URL)

DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", "2"))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", "0.02"))

# Prevent tiny “size becomes 0” issues
MIN_USD_NOTIONAL = float(os.getenv("MIN_USD_NOTIONAL", "10"))  # safety floor
DEFAULT_SZ_DECIMALS = int(os.getenv("DEFAULT_SZ_DECIMALS", "4"))

if not WALLET or not AGENT_KEY:
    # Fail fast with a clean error (Render logs will show this clearly)
    raise RuntimeError(
        "Missing required env vars: HYPERLIQUID_WALLET and/or HYPERLIQUID_AGENT_KEY"
    )

app = FastAPI(title="Hyperliquid TradingView Bot", version="1.0.0")

# =========================
# LAZY SINGLETONS
# =========================
_info: Optional[Info] = None
_exchange: Optional[Exchange] = None
_meta_cache: Optional[Dict[str, Any]] = None
_meta_cache_ts: float = 0.0


def _make_info() -> Info:
    """
    Create Info client using official SDK.
    We strongly prefer disabling websockets to avoid 429 handshake issues.
    Different versions expose different constructor signatures, so we adapt safely.
    """
    sig = inspect.signature(Info.__init__)
    kwargs = {}

    # Most official SDK builds accept base_url as first arg positionally.
    # Some accept skip_ws / use_ws flags.
    # We'll pass BASE_URL positionally and try to disable WS if supported.
    if "skip_ws" in sig.parameters:
        kwargs["skip_ws"] = True
    elif "use_ws" in sig.parameters:
        kwargs["use_ws"] = False
    elif "ws" in sig.parameters:
        kwargs["ws"] = False

    return Info(BASE_URL, **kwargs)


def _make_exchange() -> Exchange:
    """
    Create Exchange client using official SDK.
    The key fix: DO NOT pass (BASE_URL, WALLET, AGENT_KEY) positionally.
    We adapt to the signature and use keyword args to avoid argument-order bugs.
    """
    sig = inspect.signature(Exchange.__init__)
    params = sig.parameters

    # Common official SDK pattern: Exchange(wallet, agent_key, base_url=...)
    # But we support variations safely.
    kwargs = {}

    # wallet / address param name
    if "wallet" in params:
        kwargs["wallet"] = WALLET
    elif "account_address" in params:
        kwargs["account_address"] = WALLET
    elif "address" in params:
        kwargs["address"] = WALLET

    # agent key param name
    if "agent_key" in params:
        kwargs["agent_key"] = AGENT_KEY
    elif "secret" in params:
        kwargs["secret"] = AGENT_KEY
    elif "private_key" in params:
        kwargs["private_key"] = AGENT_KEY

    # base url param name
    if "base_url" in params:
        kwargs["base_url"] = BASE_URL
    elif "url" in params:
        kwargs["url"] = BASE_URL

    # If it doesn't accept keywords for wallet/agent_key, fall back carefully
    # with the most common ordering: (wallet, agent_key, base_url)
    if not kwargs:
        return Exchange(WALLET, AGENT_KEY, BASE_URL)

    # If base_url wasn't accepted via kwargs, still pass it positionally last
    if ("base_url" not in params) and ("url" not in params):
        return Exchange(**kwargs)

    return Exchange(**kwargs)


def get_info() -> Info:
    global _info
    if _info is None:
        _info = _make_info()
    return _info


def get_exchange() -> Exchange:
    global _exchange
    if _exchange is None:
        _exchange = _make_exchange()
    return _exchange


# =========================
# HELPERS
# =========================
def _refresh_meta_if_needed(ttl_seconds: int = 60) -> Dict[str, Any]:
    """
    Pull meta occasionally so we know szDecimals for correct rounding.
    Cached to avoid hammering /info and triggering rate limits.
    """
    global _meta_cache, _meta_cache_ts
    now = time.time()
    if _meta_cache is not None and (now - _meta_cache_ts) < ttl_seconds:
        return _meta_cache

    info = get_info()

    # Different SDK versions: meta() or meta_and_asset_ctxs() etc.
    if hasattr(info, "meta"):
        meta = info.meta()
    elif hasattr(info, "meta_and_asset_ctxs"):
        meta = info.meta_and_asset_ctxs()
    else:
        meta = {}

    _meta_cache = meta
    _meta_cache_ts = now
    return meta


def _get_sz_decimals(coin: str) -> int:
    """
    Try to find szDecimals for coin in meta.
    Fallback to DEFAULT_SZ_DECIMALS.
    """
    meta = _refresh_meta_if_needed()

    # Many SDKs: meta["universe"] is list of dicts like {"name":"ETH","szDecimals":...}
    universe = None
    if isinstance(meta, dict):
        universe = meta.get("universe") or meta.get("meta", {}).get("universe")

    if isinstance(universe, list):
        for item in universe:
            try:
                if str(item.get("name", "")).upper() == coin.upper():
                    return int(item.get("szDecimals", DEFAULT_SZ_DECIMALS))
            except Exception:
                continue

    return DEFAULT_SZ_DECIMALS


def _round_size(size: float, decimals: int) -> float:
    """
    Round DOWN to allowed decimals to avoid rejection.
    """
    if decimals < 0:
        return size
    factor = 10 ** decimals
    return math.floor(size * factor) / factor


def get_state() -> Dict[str, Any]:
    return get_info().user_state(WALLET)


def get_balance() -> float:
    state = get_state()
    return float(state["marginSummary"]["accountValue"])


def get_position(coin: str) -> float:
    state = get_state()
    for p in state.get("assetPositions", []):
        pos = p.get("position", {})
        if str(pos.get("coin", "")).upper() == coin.upper():
            return float(pos.get("szi", 0))
    return 0.0


def get_price(coin: str) -> float:
    mids = get_info().all_mids()
    # mids keys are typically "ETH", "BTC", etc.
    try:
        return float(mids[coin.upper()])
    except Exception:
        raise HTTPException(400, f"Unknown coin or no mid price for: {coin}")


# =========================
# API
# =========================
class Signal(BaseModel):
    action: str = Field(..., description="BUY or SELL")
    coin: str = Field(..., description="e.g., ETH, BTC")
    leverage: float = Field(DEFAULT_LEVERAGE, ge=1, le=50)
    risk_pct: float = Field(MAX_RISK_PCT, gt=0)


@app.get("/")
def root():
    return {"ok": True, "service": "hyperliquid-tradingview-bot"}


@app.get("/health")
def health():
    # lightweight checks, avoids spamming Hyperliquid endpoints
    return {
        "ok": True,
        "base_url": BASE_URL,
        "wallet_set": bool(WALLET),
        "agent_key_set": bool(AGENT_KEY),
    }


@app.post("/webhook")
def webhook(signal: Signal):
    try:
        coin = signal.coin.upper().strip()
        side = signal.action.upper().strip()

        if side not in ("BUY", "SELL"):
            raise HTTPException(400, "action must be BUY or SELL")

        leverage = float(signal.leverage)
        risk_pct = min(float(signal.risk_pct), MAX_RISK_PCT)

        balance = get_balance()
        price = get_price(coin)

        usd_risk = balance * risk_pct
        notional = usd_risk * leverage

        # enforce minimum notional so size doesn't floor to 0
        if notional < MIN_USD_NOTIONAL:
            notional = MIN_USD_NOTIONAL

        raw_size = notional / price

        sz_decimals = _get_sz_decimals(coin)
        size = _round_size(raw_size, sz_decimals)

        if size <= 0:
            raise HTTPException(
                400,
                f"Calculated size is zero (raw={raw_size}, decimals={sz_decimals}). "
                f"Increase risk_pct/leverage or MIN_USD_NOTIONAL.",
            )

        ex = get_exchange()
        pos = get_position(coin)

        # If position exists and signal is opposite, close first
        if pos != 0:
            if (pos > 0 and side == "SELL") or (pos < 0 and side == "BUY"):
                # market_close signature differs across SDK versions; try common patterns
                if hasattr(ex, "market_close"):
                    ex.market_close(coin)
                elif hasattr(ex, "close_position"):
                    ex.close_position(coin)
                else:
                    raise HTTPException(500, "Exchange client has no close method")

        # Update leverage if supported
        if hasattr(ex, "update_leverage"):
            ex.update_leverage(coin, leverage)
        elif hasattr(ex, "set_leverage"):
            ex.set_leverage(coin, leverage)

        is_buy = (side == "BUY")

        # Open market order
        if hasattr(ex, "market_open"):
            ex.market_open(coin, is_buy, size)
        elif hasattr(ex, "market_order"):
            ex.market_order(coin, is_buy, size)
        else:
            raise HTTPException(500, "Exchange client has no market open/order method")

        return {
            "status": "executed",
            "coin": coin,
            "side": side,
            "size": size,
            "price": price,
            "account_value": balance,
            "risk_pct_used": risk_pct,
            "leverage_used": leverage,
            "sz_decimals": sz_decimals,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
