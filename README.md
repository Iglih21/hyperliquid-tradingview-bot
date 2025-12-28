# hyperliquid-tradingview-bot

This repository contains a minimal webhook server that connects TradingView alerts to Hyperliquid. It listens for JSON alerts from TradingView and executes trades on the Hyperliquid exchange using an API wallet.

## Deploy to Render

1. Fork or clone this repository.
2. Go to https://dashboard.render.com/ and create a new Web Service.
3. Connect your GitHub account and select this repository.
4. Render will detect `render.yaml` and auto-configure the service. The service will install the requirements and run `uvicorn main:app --host 0.0.0.0 --port 10000`.
5. Set the following environment variables in your Render service:

- `HYPERLIQUID_AGENT_KEY` – the private key of your Hyperliquid API wallet (agent).
- `HYPERLIQUID_WALLET` – your Hyperliquid wallet address (the public address).
- `DEFAULT_LEVERAGE` – the default leverage to use (e.g. 10).
- `MAX_RISK_PCT` – the maximum fraction of account balance to risk per trade (e.g. 0.04 for 4%).

After deployment, Render will provide a public URL for your service. The webhook endpoint is `/webhook`.

## TradingView Setup

1. Open your chart and add the **Tradevisor V2** indicator.
2. Create two alerts:

- **Buy alert** – Condition: `Tradevisor V2 → Buy`. Set the interval to `Once per bar close`. Enable webhook notifications and paste your Render webhook URL into the *Webhook URL* field. In the *Message* field, paste the following JSON:

```json
{
  "action": "BUY",
  "coin": "BTC",
  "leverage": 10,
  "risk_pct": 0.04,
  "mode": "reverse"
}
```

- **Sell alert** – Condition: `Tradevisor V2 → Sell`. Use the same settings as the Buy alert, but change `"action": "SELL"` in the message:

```json
{
  "action": "SELL",
  "coin": "BTC",
  "leverage": 10,
  "risk_pct": 0.04,
  "mode": "reverse"
}
```

3. Save the alerts. Whenever Tradevisor prints a buy or sell signal, TradingView will send a webhook to your bot. The bot will close the opposite position (if any) and open a new position on Hyperliquid with the specified leverage and risk.

---

**Important:** Keep your API wallet key secure. Never commit it to version control or share it publicly.
