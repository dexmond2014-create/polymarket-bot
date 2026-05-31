#!/usr/bin/env python3
"""
Gold (XAU/USD) Trading Bot — Deriv WebSocket API
- Trades gold 24/7 on Deriv
- Buys on dips, sells at take profit
- Hard stop loss on every trade
- News filter — pauses during high-impact events
- Daily loss limit — stops automatically
"""

import json
import time
import asyncio
import requests
import websockets
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

DERIV_TOKEN      = "pat_75773816bdfe45a0bb4b746971982eb227333e4dad6f6c391a517ead3820c6c2"
DERIV_WS_URL     = "wss://ws.binaryws.com/websockets/v3?app_id=1089"

SYMBOL           = "frxXAUUSD"    # Gold on Deriv
TRADE_USD        = 5.0            # $5 per trade (safe start)
TAKE_PROFIT_PCT  = 0.015          # +1.5% take profit
STOP_LOSS_PCT    = 0.02           # -2% stop loss
DAILY_LOSS_LIMIT = 30.0           # Stop if lost $30 today
MAX_OPEN_TRADES  = 3              # Max simultaneous trades
DIP_TRIGGER_PCT  = 0.008          # Buy when price dips 0.8%
POLL_INTERVAL    = 60             # Check every 60 seconds
TRADE_START_H    = 8              # Start 8am UTC
TRADE_END_H      = 22             # Stop 10pm UTC

# News filter
NEWS_PAUSE_BEFORE = 30
NEWS_PAUSE_AFTER  = 30
GOLD_NEWS_KEYWORDS = [
    "non-farm", "nfp", "fed", "fomc", "interest rate", "cpi", "inflation",
    "gdp", "powell", "jobless", "unemployment", "pce", "retail sales",
    "ism", "pmi", "treasury", "rate decision"
]

LOG_FILE   = Path(__file__).parent / "gold_trades.json"
NEWS_CACHE = Path(__file__).parent / ".news_cache.json"

# ── Helpers ───────────────────────────────────────────────────────────────────

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def load_json(path, default):
    if Path(path).exists():
        try:
            return json.loads(Path(path).read_text())
        except:
            return default
    return default

def save_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2))

def log_trade(entry):
    trades = load_json(LOG_FILE, [])
    trades.append(entry)
    save_json(LOG_FILE, trades)
    action = entry.get("action", "")
    pnl    = f" PnL:${entry['profit_usd']:+.2f}" if "profit_usd" in entry else ""
    print(f"  [logged] {action} XAU/USD{pnl}")

def is_trading_hours():
    hour = datetime.now(timezone.utc).hour
    return TRADE_START_H <= hour < TRADE_END_H

def get_daily_loss():
    today  = now_iso()[:10]
    trades = load_json(LOG_FILE, [])
    return sum(
        abs(t.get("profit_usd", 0))
        for t in trades
        if t.get("ts", "")[:10] == today
        and t.get("profit_usd", 0) < 0
    )

# ── News filter ───────────────────────────────────────────────────────────────

def fetch_news_events():
    try:
        cache = load_json(NEWS_CACHE, {})
        now   = datetime.now(timezone.utc)
        last_fetch = cache.get("fetched_at", "")
        if last_fetch:
            last_dt = datetime.fromisoformat(last_fetch)
            if (now - last_dt).total_seconds() < 14400:
                return cache.get("events", [])

        r = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            all_events = r.json()
            high_impact = []
            for event in all_events:
                if event.get("impact") != "High":
                    continue
                if event.get("country") not in ["USD", "US"]:
                    continue
                title = (event.get("title") or "").lower()
                if any(kw in title for kw in GOLD_NEWS_KEYWORDS):
                    high_impact.append({
                        "title": event.get("title"),
                        "date":  event.get("date"),
                        "time":  event.get("time"),
                    })
            cache = {"fetched_at": now.isoformat(), "events": high_impact}
            save_json(NEWS_CACHE, cache)
            print(f"  [news] {len(high_impact)} high-impact events this week")
            return high_impact
    except Exception as e:
        print(f"  [news] {e}")
    return []

def is_news_time():
    events = fetch_news_events()
    now    = datetime.now(timezone.utc)
    for event in events:
        try:
            date_str = event.get("date", "")
            time_str = event.get("time", "")
            if not date_str or not time_str:
                continue
            event_dt = datetime.strptime(
                f"{date_str} {time_str}", "%m-%d-%Y %I:%M%p"
            ).replace(tzinfo=timezone.utc)
            before = event_dt - timedelta(minutes=NEWS_PAUSE_BEFORE)
            after  = event_dt + timedelta(minutes=NEWS_PAUSE_AFTER)
            if before <= now <= after:
                title = event.get("title", "News")
                if now < event_dt:
                    mins = int((event_dt - now).total_seconds() / 60)
                    return True, f"{title} in {mins} min"
                else:
                    mins = int((now - event_dt).total_seconds() / 60)
                    return True, f"{title} was {mins} min ago"
        except:
            continue
    return False, ""

# ── Deriv API ─────────────────────────────────────────────────────────────────

async def deriv_request(ws, request):
    await ws.send(json.dumps(request))
    response = await asyncio.wait_for(ws.recv(), timeout=15)
    return json.loads(response)

async def get_balance(ws):
    resp = await deriv_request(ws, {"balance": 1, "account": "current"})
    return resp.get("balance", {}).get("balance", 0)

async def get_gold_price(ws):
    resp = await deriv_request(ws, {
        "ticks": SYMBOL,
        "subscribe": 0
    })
    tick = resp.get("tick", {})
    return tick.get("ask") or tick.get("quote")

async def get_open_contracts(ws):
    resp = await deriv_request(ws, {"portfolio": 1})
    contracts = resp.get("portfolio", {}).get("contracts", [])
    return [c for c in contracts if SYMBOL in c.get("underlying", "")]

async def buy_gold(ws, current_price, reason):
    """Open a gold CALL (buy) contract on Deriv."""
    tp_price = round(current_price * (1 + TAKE_PROFIT_PCT), 2)
    sl_price = round(current_price * (1 - STOP_LOSS_PCT), 2)

    resp = await deriv_request(ws, {
        "buy": 1,
        "price": TRADE_USD,
        "parameters": {
            "contract_type":  "CALL",
            "currency":       "USD",
            "symbol":         SYMBOL,
            "duration":       4,
            "duration_unit":  "h",
            "basis":          "stake",
            "amount":         TRADE_USD,
            "limit_order": {
                "take_profit": tp_price,
                "stop_loss":   sl_price,
            }
        }
    })

    if "buy" in resp:
        contract_id    = resp["buy"].get("contract_id")
        buy_price      = resp["buy"].get("buy_price", TRADE_USD)
        print(f"  ✅ BUY gold ${buy_price:.2f} | TP=${tp_price:.2f} | SL=${sl_price:.2f} | ID={contract_id}")
        log_trade({
            "ts":          now_iso(),
            "action":      "BUY",
            "price":       current_price,
            "trade_usd":   TRADE_USD,
            "tp":          tp_price,
            "sl":          sl_price,
            "contract_id": contract_id,
            "reason":      reason,
        })
        return True
    else:
        error = resp.get("error", {}).get("message", "unknown error")
        print(f"  [error] BUY failed: {error}")
        return False

async def check_closed_contracts(ws):
    """Check for any closed contracts and log P&L."""
    try:
        resp = await deriv_request(ws, {
            "profit_table": 1,
            "description":  1,
            "limit":        10,
            "offset":       0,
            "sort":         "DESC"
        })
        contracts = resp.get("profit_table", {}).get("transactions", [])
        logged    = load_json(LOG_FILE, [])
        logged_ids = {str(t.get("contract_id")) for t in logged}

        for c in contracts:
            cid = str(c.get("contract_id", ""))
            if cid in logged_ids:
                continue
            if SYMBOL not in c.get("shortcode", ""):
                continue
            profit = float(c.get("sell_price", 0)) - float(c.get("buy_price", 0))
            result = "✅ WIN" if profit > 0 else "🛑 LOSS"
            print(f"  {result} | profit: ${profit:+.2f}")
            log_trade({
                "ts":          now_iso(),
                "action":      "CLOSE",
                "contract_id": cid,
                "profit_usd":  round(profit, 2),
            })
    except Exception as e:
        pass

# ── Main loop ─────────────────────────────────────────────────────────────────

async def main_async():
    print("=" * 55)
    print("  Gold Bot (XAU/USD) — Deriv Edition")
    print("=" * 55)
    print(f"  Trade size:    ${TRADE_USD} per trade")
    print(f"  Take profit:   +{TAKE_PROFIT_PCT*100:.1f}%")
    print(f"  Stop loss:     -{STOP_LOSS_PCT*100:.1f}%")
    print(f"  Daily limit:   -${DAILY_LOSS_LIMIT}")
    print(f"  Max trades:    {MAX_OPEN_TRADES}")
    print(f"  Hours:         {TRADE_START_H}:00-{TRADE_END_H}:00 UTC")
    print(f"  News filter:   ±{NEWS_PAUSE_BEFORE} min around events")
    print("=" * 55)

    recent_prices = []
    cycle = 0

    while True:
        try:
            async with websockets.connect(DERIV_WS_URL) as ws:
                # Authorize
                auth = await deriv_request(ws, {"authorize": DERIV_TOKEN})
                if "error" in auth:
                    print(f"  [error] Auth failed: {auth['error']['message']}")
                    await asyncio.sleep(30)
                    continue

                account = auth.get("authorize", {})
                balance = account.get("balance", 0)
                currency = account.get("currency", "USD")
                print(f"  ✅ Connected | Balance: {currency} {balance:.2f}\n")

                while True:
                    cycle += 1

                    # Check closed contracts
                    await check_closed_contracts(ws)

                    # Get current price
                    price = await get_gold_price(ws)
                    if not price:
                        print(f"  [price] No data")
                        await asyncio.sleep(POLL_INTERVAL)
                        continue

                    open_trades = await get_open_contracts(ws)
                    daily_loss  = get_daily_loss()
                    trading_hrs = is_trading_hours()

                    print(f"\n[{now_iso()[:16]}] XAU=${price:.2f} | Open: {len(open_trades)}/{MAX_OPEN_TRADES} | Loss: ${daily_loss:.2f}/${DAILY_LOSS_LIMIT}")

                    # Track prices for dip detection
                    recent_prices.append(price)
                    if len(recent_prices) > 20:
                        recent_prices.pop(0)

                    # Safety checks
                    if daily_loss >= DAILY_LOSS_LIMIT:
                        print(f"  [safety] Daily loss limit hit — paused today")
                        await asyncio.sleep(POLL_INTERVAL)
                        continue

                    if not trading_hrs:
                        print(f"  [hours] Outside trading hours")
                        await asyncio.sleep(POLL_INTERVAL)
                        continue

                    if len(open_trades) >= MAX_OPEN_TRADES:
                        print(f"  [full] Max {MAX_OPEN_TRADES} trades open")
                        await asyncio.sleep(POLL_INTERVAL)
                        continue

                    # News filter
                    paused, news_reason = is_news_time()
                    if paused:
                        print(f"  [news] Paused — ⏸ {news_reason}")
                        await asyncio.sleep(POLL_INTERVAL)
                        continue

                    # Dip detection
                    if len(recent_prices) >= 5:
                        recent_high = max(recent_prices[-10:]) if len(recent_prices) >= 10 else max(recent_prices)
                        dip_pct = (recent_high - price) / recent_high

                        if dip_pct >= DIP_TRIGGER_PCT:
                            print(f"  📉 Dip -{dip_pct*100:.2f}% from ${recent_high:.2f} — buying!")
                            await buy_gold(ws, price, f"dip_{dip_pct*100:.1f}pct")
                        else:
                            print(f"  [watch] High=${recent_high:.2f} dip={dip_pct*100:.2f}% (need {DIP_TRIGGER_PCT*100:.1f}%)")

                    await asyncio.sleep(POLL_INTERVAL)

        except websockets.exceptions.ConnectionClosed:
            print("  [ws] Connection closed — reconnecting in 15s...")
            await asyncio.sleep(15)
        except Exception as e:
            print(f"  [error] {e} — retrying in 15s")
            await asyncio.sleep(15)

def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nGold bot stopped.")

if __name__ == "__main__":
    main()
