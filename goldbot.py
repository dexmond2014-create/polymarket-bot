#!/usr/bin/env python3
"""
Gold (XAU/USD) Trading Bot — MT5
- Buys on dips, sells at take profit
- Hard stop loss on every trade
- Daily loss limit — stops automatically
- Safe position sizing — never risks more than $20/trade
"""

import time
import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import MetaTrader5 as mt5
except ImportError:
    print("Installing MetaTrader5...")
    import subprocess
    subprocess.run(["pip3", "install", "MetaTrader5", "mt5linux", "--break-system-packages"])
    import MetaTrader5 as mt5

# ── Config ────────────────────────────────────────────────────────────────────

MT5_ACCOUNT  = 14292840
MT5_PASSWORD = "^h7SS$6k"
MT5_SERVER   = "weltrade-Real"

SYMBOL           = "XAUUSD"       # Gold
TRADE_USD        = 20.0           # Risk $20 per trade
TAKE_PROFIT_PCT  = 0.015          # +1.5% take profit
STOP_LOSS_PCT    = 0.02           # -2% stop loss
DAILY_LOSS_LIMIT = 30.0           # Stop bot if lost $30 today
MAX_OPEN_TRADES  = 3              # Max simultaneous positions
DIP_TRIGGER_PCT  = 0.01           # Buy when price drops 1% from recent high
POLL_INTERVAL    = 60             # Check every 60 seconds
TRADE_START_H    = 8              # Start trading 8am UTC
TRADE_END_H      = 22             # Stop trading 10pm UTC

LOG_FILE       = Path(__file__).parent / "gold_trades.json"
NEWS_CACHE     = Path(__file__).parent / ".news_cache.json"

# News pause window (minutes before and after high-impact news)
NEWS_PAUSE_BEFORE = 30   # pause 30 min before news
NEWS_PAUSE_AFTER  = 30   # pause 30 min after news

# High-impact keywords to watch for gold
GOLD_NEWS_KEYWORDS = [
    "non-farm", "nfp", "fed", "fomc", "interest rate", "cpi", "inflation",
    "gdp", "powell", "jobless", "unemployment", "pce", "retail sales",
    "ism", "pmi", "treasury", "debt ceiling", "rate decision"
]

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

def is_trading_hours():
    hour = datetime.now(timezone.utc).hour
    return TRADE_START_H <= hour < TRADE_END_H

def get_daily_loss():
    today = now_iso()[:10]
    trades = load_json(LOG_FILE, [])
    losses = [
        abs(t.get("profit_usd", 0))
        for t in trades
        if t.get("ts", "")[:10] == today
        and t.get("profit_usd", 0) < 0
    ]
    return sum(losses)

# ── News filter ──────────────────────────────────────────────────────────────

def fetch_news_events():
    """Fetch high-impact economic events from ForexFactory API."""
    try:
        cache = load_json(NEWS_CACHE, {})
        now   = datetime.now(timezone.utc)

        # Refresh cache every 4 hours
        last_fetch = cache.get("fetched_at", "")
        if last_fetch:
            last_dt = datetime.fromisoformat(last_fetch)
            if (now - last_dt).total_seconds() < 14400:
                return cache.get("events", [])

        # ForexFactory calendar API
        date_str = now.strftime("%b%d.%Y").lower()
        r = requests.get(
            f"https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            all_events = r.json()
            # Filter HIGH impact USD events only
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
                        "impact": event.get("impact"),
                    })

            cache = {"fetched_at": now.isoformat(), "events": high_impact}
            save_json(NEWS_CACHE, cache)
            print(f"  [news] Fetched {len(high_impact)} high-impact events this week")
            return high_impact

    except Exception as e:
        print(f"  [news] Could not fetch calendar: {e}")

    return []

def is_news_time():
    """
    Returns (paused: bool, reason: str)
    Pauses trading 30 min before and 30 min after high-impact news.
    """
    events = fetch_news_events()
    now    = datetime.now(timezone.utc)

    for event in events:
        try:
            # Parse event datetime
            date_str  = event.get("date", "")
            time_str  = event.get("time", "")
            if not date_str or not time_str:
                continue

            event_dt = datetime.strptime(
                f"{date_str} {time_str}", "%m-%d-%Y %I:%M%p"
            ).replace(tzinfo=timezone.utc)

            # Check if we're within the pause window
            before = event_dt - timedelta(minutes=NEWS_PAUSE_BEFORE)
            after  = event_dt + timedelta(minutes=NEWS_PAUSE_AFTER)

            if before <= now <= after:
                title = event.get("title", "News")
                if now < event_dt:
                    mins = int((event_dt - now).total_seconds() / 60)
                    return True, f"⏸ {title} in {mins} min"
                else:
                    mins = int((now - event_dt).total_seconds() / 60)
                    return True, f"⏸ {title} was {mins} min ago"

        except Exception:
            continue

    return False, ""

# ── MT5 connection ────────────────────────────────────────────────────────────

def connect():
    if not mt5.initialize():
        print(f"  [mt5] initialize() failed: {mt5.last_error()}")
        return False

    if not mt5.login(MT5_ACCOUNT, password=MT5_PASSWORD, server=MT5_SERVER):
        print(f"  [mt5] login failed: {mt5.last_error()}")
        mt5.shutdown()
        return False

    info = mt5.account_info()
    print(f"  ✅ Connected to MT5 | Balance: ${info.balance:.2f} | Equity: ${info.equity:.2f}")
    return True

def disconnect():
    mt5.shutdown()

# ── Market data ───────────────────────────────────────────────────────────────

def get_price():
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick:
        return tick.bid, tick.ask
    return None, None

def get_open_trades():
    positions = mt5.positions_get(symbol=SYMBOL)
    return list(positions) if positions else []

def get_recent_high(bars=20):
    """Get highest price in last N bars (1min)."""
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 0, bars)
    if rates is not None and len(rates) > 0:
        return max(r['high'] for r in rates)
    return None

# ── Trade execution ───────────────────────────────────────────────────────────

def calc_lot_size(price):
    """Calculate lot size for $TRADE_USD risk with 2% stop loss."""
    stop_distance = price * STOP_LOSS_PCT
    symbol_info = mt5.symbol_info(SYMBOL)
    if not symbol_info:
        return 0.01

    tick_value = symbol_info.trade_tick_value
    tick_size  = symbol_info.trade_tick_size

    if tick_size == 0:
        return 0.01

    # Risk per lot = (stop_distance / tick_size) * tick_value
    risk_per_lot = (stop_distance / tick_size) * tick_value
    if risk_per_lot == 0:
        return 0.01

    lot = TRADE_USD / risk_per_lot
    lot = round(lot, 2)
    lot = max(0.01, min(lot, 0.5))  # between 0.01 and 0.5 lots
    return lot

def buy_gold(reason):
    """Open a BUY position on XAUUSD."""
    bid, ask = get_price()
    if not ask:
        print("  [error] Could not get price")
        return False

    lot  = calc_lot_size(ask)
    sl   = round(ask * (1 - STOP_LOSS_PCT), 2)
    tp   = round(ask * (1 + TAKE_PROFIT_PCT), 2)

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       lot,
        "type":         mt5.ORDER_TYPE_BUY,
        "price":        ask,
        "sl":           sl,
        "tp":           tp,
        "deviation":    20,
        "magic":        20260531,
        "comment":      f"goldbot_{reason[:20]}",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"  ✅ BUY {lot} lots @ ${ask:.2f} | SL=${sl:.2f} | TP=${tp:.2f}")
        log_trade({
            "ts":         now_iso(),
            "action":     "BUY",
            "symbol":     SYMBOL,
            "lot":        lot,
            "price":      ask,
            "sl":         sl,
            "tp":         tp,
            "reason":     reason,
            "ticket":     result.order,
        })
        return True
    else:
        print(f"  [error] BUY failed: {result.retcode} — {result.comment}")
        return False

def check_closed_trades():
    """Log any trades that were closed by TP/SL."""
    from_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    to_date   = datetime.now(timezone.utc)
    history   = mt5.history_deals_get(from_date, to_date)

    if not history:
        return

    logged = load_json(LOG_FILE, [])
    logged_tickets = {t.get("ticket") for t in logged}

    for deal in history:
        if deal.ticket in logged_tickets:
            continue
        if deal.symbol != SYMBOL:
            continue
        if deal.entry != mt5.DEAL_ENTRY_OUT:
            continue

        profit = deal.profit
        price  = deal.price
        result = "✅ TP" if profit > 0 else "🛑 SL"
        print(f"  {result} closed | profit: ${profit:.2f}")
        log_trade({
            "ts":         now_iso(),
            "action":     "CLOSE",
            "symbol":     SYMBOL,
            "price":      price,
            "profit_usd": profit,
            "ticket":     deal.ticket,
        })

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  Gold Bot (XAU/USD) — MT5 Safe Edition")
    print("=" * 55)
    print(f"  Account:       {MT5_ACCOUNT} @ {MT5_SERVER}")
    print(f"  Trade size:    ${TRADE_USD} per trade")
    print(f"  Take profit:   +{TAKE_PROFIT_PCT*100:.1f}%")
    print(f"  Stop loss:     -{STOP_LOSS_PCT*100:.1f}%")
    print(f"  Daily limit:   -${DAILY_LOSS_LIMIT}")
    print(f"  Max trades:    {MAX_OPEN_TRADES}")
    print(f"  Hours:         {TRADE_START_H}:00-{TRADE_END_H}:00 UTC")
    print("=" * 55)

    if not connect():
        print("  [error] Could not connect to MT5 — retrying in 60s")
        time.sleep(60)
        if not connect():
            print("  [fatal] MT5 connection failed twice. Check credentials.")
            return

    recent_prices = []

    while True:
        try:
            # Check closed trades (TP/SL hits)
            check_closed_trades()

            bid, ask = get_price()
            if not bid:
                print(f"  [price] No data")
                time.sleep(POLL_INTERVAL)
                continue

            open_trades  = get_open_trades()
            daily_loss   = get_daily_loss()
            trading_hrs  = is_trading_hours()

            print(f"\n[{now_iso()[:16]}] XAU=${bid:.2f} | Open: {len(open_trades)}/{MAX_OPEN_TRADES} | Loss today: ${daily_loss:.2f}/${DAILY_LOSS_LIMIT}")

            # Track recent prices for dip detection
            recent_prices.append(bid)
            if len(recent_prices) > 20:
                recent_prices.pop(0)

            # Safety checks
            if daily_loss >= DAILY_LOSS_LIMIT:
                print(f"  [safety] Daily loss limit ${DAILY_LOSS_LIMIT} hit — paused for today")
                time.sleep(POLL_INTERVAL)
                continue

            if not trading_hrs:
                print(f"  [hours] Outside trading hours ({TRADE_START_H}-{TRADE_END_H} UTC)")
                time.sleep(POLL_INTERVAL)
                continue

            # News filter — pause around high-impact events
            paused, news_reason = is_news_time()
            if paused:
                print(f"  [news] Paused — {news_reason}")
                time.sleep(POLL_INTERVAL)
                continue

            if len(open_trades) >= MAX_OPEN_TRADES:
                print(f"  [full] Max {MAX_OPEN_TRADES} trades open")
                time.sleep(POLL_INTERVAL)
                continue

            # Dip detection — buy when price drops 1% from recent high
            if len(recent_prices) >= 5:
                recent_high = max(recent_prices[-10:]) if len(recent_prices) >= 10 else max(recent_prices)
                dip_pct = (recent_high - bid) / recent_high

                if dip_pct >= DIP_TRIGGER_PCT:
                    print(f"  📉 Dip detected! -{dip_pct*100:.2f}% from recent high ${recent_high:.2f}")
                    buy_gold(f"dip_{dip_pct*100:.1f}pct")
                else:
                    print(f"  [watching] High=${recent_high:.2f} | dip={dip_pct*100:.2f}% (need {DIP_TRIGGER_PCT*100:.1f}%)")

        except Exception as e:
            print(f"  [error] {e}")
            # Reconnect if needed
            try:
                connect()
            except:
                pass

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGold bot stopped.")
        disconnect()
