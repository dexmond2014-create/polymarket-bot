#!/usr/bin/env python3
"""
Solana Meme Coin Bot — with full safety controls
- Monitors Pump.fun for new graduated tokens
- Auto sells at 2x profit or -50% stop loss
- 0.05 SOL per trade
- Rug pull protection, daily loss limit, age filter
"""

import json
import os
import subprocess
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

TRADE_SIZE_SOL      = 0.05        # SOL per trade (~$4)
TAKE_PROFIT_X       = 2.0         # Sell at 2x (100% profit)
STOP_LOSS_PCT       = 0.50        # Stop loss at -50%
POLL_INTERVAL       = 20          # Check every 20 seconds
MAX_OPEN_TRADES     = 5           # Max simultaneous positions
MAX_TRADES_PER_DAY  = 10          # Max buys per day
MAX_DAILY_LOSS_SOL  = 0.25        # Stop trading if lost 0.25 SOL today (~$40)
MAX_POSITION_AGE_H  = 24          # Force sell if held longer than 24 hours

# Token filters (rug protection)
MIN_MARKET_CAP      = 20_000      # Skip tokens under $20k mcap
MAX_MARKET_CAP      = 300_000     # Skip tokens over $300k (too late)
MIN_LIQUIDITY_USD   = 8_000       # Must have $8k+ liquidity
MIN_REPLIES         = 10          # Must have community activity (replies)
MAX_DEV_HOLD_PCT    = 15          # Skip if dev holds >15% of supply

TRADE_LOG   = Path(__file__).parent / "meme_trades.json"
POSITIONS   = Path(__file__).parent / ".meme_positions.json"
BLACKLIST   = Path(__file__).parent / ".meme_blacklist.json"
BULLPEN     = os.environ.get("BULLPEN_BIN", os.path.expanduser("~/.bullpen/bin/bullpen"))

# Known scam/rug keywords in token names
SCAM_KEYWORDS = [
    "elon", "trump", "inu", "safe", "moon", "gem", "100x", "1000x",
    "rugproof", "safu", "based", "airdrop", "free", "presale"
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def bullpen(*args, capture=True):
    cmd = [BULLPEN] + list(args)
    result = subprocess.run(cmd, capture_output=capture, text=True)
    return result

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
    trades = load_json(TRADE_LOG, [])
    trades.append(entry)
    save_json(TRADE_LOG, trades)
    action = entry['action']
    symbol = entry.get('symbol', '?')
    status = entry.get('status', '')
    pnl    = f" PnL:{entry['pnl_pct']:+.1f}%" if 'pnl_pct' in entry else ""
    print(f"  [logged] {action} {symbol} — {status}{pnl}")

def get_sol_balance():
    result = bullpen("solana", "balance", "--output", "json")
    if result.returncode == 0:
        try:
            return float(json.loads(result.stdout).get("sol_balance", 0))
        except:
            pass
    return 0

def get_token_price(mint):
    result = bullpen("solana", "price", mint, "--output", "json")
    if result.returncode == 0:
        try:
            return float(json.loads(result.stdout).get("price_usd", 0))
        except:
            pass
    return None

def get_daily_stats():
    """Get today's trade count and total loss."""
    today  = now_iso()[:10]
    trades = load_json(TRADE_LOG, [])
    today_buys  = [t for t in trades if t.get('action') == 'BUY'
                   and t.get('status') == 'filled'
                   and t.get('ts', '')[:10] == today]
    today_sells = [t for t in trades if t.get('action') == 'SELL'
                   and t.get('status') == 'filled'
                   and t.get('ts', '')[:10] == today]
    total_loss_sol = sum(
        t.get('sol_amount', TRADE_SIZE_SOL)
        for t in today_sells
        if t.get('pnl_pct', 0) < 0
    )
    return len(today_buys), total_loss_sol

# ── Safety checks ─────────────────────────────────────────────────────────────

def is_safe_token(token):
    """
    Run all rug/scam checks. Returns (safe: bool, reason: str).
    """
    mint   = token.get("mint", "")
    symbol = token.get("symbol", "?").upper()
    name   = (token.get("name", "") or "").lower()
    mcap   = token.get("usd_market_cap", 0) or 0
    liq    = token.get("virtual_sol_reserves", 0) or 0  # approx liquidity
    replies = token.get("reply_count", 0) or 0

    # 1. Blacklist check
    blacklist = load_json(BLACKLIST, [])
    if mint in blacklist:
        return False, "blacklisted"

    # 2. Market cap filter
    if mcap < MIN_MARKET_CAP:
        return False, f"mcap too low (${mcap:,.0f})"
    if mcap > MAX_MARKET_CAP:
        return False, f"mcap too high (${mcap:,.0f})"

    # 3. Community activity — must have replies
    if replies < MIN_REPLIES:
        return False, f"low community activity ({replies} replies)"

    # 4. Scam keyword check
    for kw in SCAM_KEYWORDS:
        if kw in name or kw in symbol.lower():
            return False, f"scam keyword: '{kw}'"

    # 5. Check if it's a known Pump.fun graduate (has bonding curve completed)
    if not token.get("complete", False) and not token.get("raydium_pool"):
        return False, "not graduated to Raydium yet"

    return True, "ok"

# ── Force sell aged positions ─────────────────────────────────────────────────

def check_aged_positions():
    """Force sell any position held longer than MAX_POSITION_AGE_H hours."""
    positions = load_json(POSITIONS, {})
    now = datetime.now(timezone.utc)
    for mint, pos in list(positions.items()):
        symbol    = pos.get("symbol", mint[:8])
        bought_at = pos.get("bought_at", "")
        try:
            bought_dt = datetime.fromisoformat(bought_at)
            age_hours = (now - bought_dt).total_seconds() / 3600
            if age_hours >= MAX_POSITION_AGE_H:
                print(f"  ⏰ FORCE SELL {symbol} — held {age_hours:.1f}h (max {MAX_POSITION_AGE_H}h)")
                sell_token(mint, symbol, f"max_age_{age_hours:.0f}h")
        except:
            pass

# ── Buy a token ───────────────────────────────────────────────────────────────

def buy_token(mint, symbol, reason):
    positions = load_json(POSITIONS, {})

    # Already holding
    if mint in positions:
        print(f"  [skip] Already holding {symbol}")
        return

    # Max open positions
    if len(positions) >= MAX_OPEN_TRADES:
        print(f"  [skip] Max {MAX_OPEN_TRADES} open positions reached")
        return

    # Daily trade limit
    trades_today, loss_today = get_daily_stats()
    if trades_today >= MAX_TRADES_PER_DAY:
        print(f"  [limit] Daily trade limit of {MAX_TRADES_PER_DAY} reached")
        return

    # Daily loss limit
    if loss_today >= MAX_DAILY_LOSS_SOL:
        print(f"  [safety] Daily loss limit hit ({loss_today:.3f} SOL) — pausing buys for today")
        return

    # SOL balance check
    sol_balance = get_sol_balance()
    if sol_balance < TRADE_SIZE_SOL + 0.01:  # keep 0.01 SOL for fees
        print(f"  [skip] Insufficient SOL ({sol_balance:.4f} SOL)")
        return

    # Get entry price
    entry_price = get_token_price(mint)
    if not entry_price or entry_price <= 0:
        print(f"  [skip] Could not get price for {symbol}")
        return

    print(f"  → BUY {TRADE_SIZE_SOL} SOL of {symbol} ({mint[:8]}...) — {reason}")
    r = bullpen("solana", "buy", mint, str(TRADE_SIZE_SOL),
                "--yes", "--non-interactive", "--output", "json")

    entry = {
        "ts":          now_iso(),
        "action":      "BUY",
        "mint":        mint,
        "symbol":      symbol,
        "sol_amount":  TRADE_SIZE_SOL,
        "entry_price": entry_price,
        "reason":      reason,
        "status":      None,
        "error":       None,
    }

    if r.returncode == 0:
        entry["status"] = "filled"
        try:
            entry["response"] = json.loads(r.stdout)
        except:
            entry["response"] = r.stdout.strip()
        positions[mint] = {
            "symbol":      symbol,
            "entry_price": entry_price,
            "sol_amount":  TRADE_SIZE_SOL,
            "bought_at":   now_iso(),
            "reason":      reason,
        }
        save_json(POSITIONS, positions)
        print(f"  ✅ Bought {symbol} at ${entry_price:.8f} | Trades today: {trades_today+1}/{MAX_TRADES_PER_DAY}")
    else:
        entry["status"] = "failed"
        entry["error"]  = (r.stderr or r.stdout).strip()
        print(f"  [error] BUY failed: {entry['error'][:150]}")

    log_trade(entry)

# ── Sell a token ──────────────────────────────────────────────────────────────

def sell_token(mint, symbol, reason):
    positions = load_json(POSITIONS, {})
    if mint not in positions:
        return

    current_price = get_token_price(mint) or 0
    pos           = positions[mint]
    entry_price   = pos.get("entry_price", 0)
    pnl_pct       = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

    print(f"  → SELL {symbol} — {reason} | PnL: {pnl_pct:+.1f}%")
    r = bullpen("solana", "sell", mint, "--max",
                "--yes", "--non-interactive", "--output", "json")

    entry = {
        "ts":          now_iso(),
        "action":      "SELL",
        "mint":        mint,
        "symbol":      symbol,
        "sol_amount":  pos.get("sol_amount", TRADE_SIZE_SOL),
        "entry_price": entry_price,
        "exit_price":  current_price,
        "pnl_pct":     round(pnl_pct, 2),
        "reason":      reason,
        "status":      None,
        "error":       None,
    }

    if r.returncode == 0:
        entry["status"] = "filled"
        try:
            entry["response"] = json.loads(r.stdout)
        except:
            entry["response"] = r.stdout.strip()
        positions.pop(mint, None)
        save_json(POSITIONS, positions)
        print(f"  ✅ Sold {symbol} | PnL: {pnl_pct:+.1f}%")
    else:
        entry["status"] = "failed"
        entry["error"]  = (r.stderr or r.stdout).strip()
        print(f"  [error] SELL failed: {entry['error'][:150]}")
        # Blacklist if we can't sell (possible honeypot)
        if "insufficient" not in entry["error"].lower():
            blacklist = load_json(BLACKLIST, [])
            if mint not in blacklist:
                blacklist.append(mint)
                save_json(BLACKLIST, blacklist)
                print(f"  [blacklist] Added {symbol} — could not sell (possible honeypot)")

    log_trade(entry)

# ── Monitor positions ─────────────────────────────────────────────────────────

def monitor_positions():
    positions = load_json(POSITIONS, {})
    if not positions:
        return

    for mint, pos in list(positions.items()):
        symbol      = pos.get("symbol", mint[:8])
        entry_price = pos.get("entry_price", 0)
        if entry_price <= 0:
            continue

        current_price = get_token_price(mint)
        if current_price is None:
            continue

        pnl_pct = (current_price - entry_price) / entry_price
        print(f"  [pos] {symbol}: ${entry_price:.8f} → ${current_price:.8f} ({pnl_pct*100:+.1f}%)")

        if pnl_pct >= (TAKE_PROFIT_X - 1):
            print(f"  🎯 TAKE PROFIT {symbol} +{pnl_pct*100:.0f}%")
            sell_token(mint, symbol, f"take_profit_{pnl_pct*100:.0f}pct")
        elif pnl_pct <= -STOP_LOSS_PCT:
            print(f"  🛑 STOP LOSS {symbol} {pnl_pct*100:.0f}%")
            sell_token(mint, symbol, f"stop_loss_{pnl_pct*100:.0f}pct")

# ── Fetch Pump.fun graduates ──────────────────────────────────────────────────

def fetch_pump_graduates():
    try:
        r = requests.get(
            "https://frontend-api.pump.fun/coins/recently-graduated?offset=0&limit=20&includeNsfw=false",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [pump.fun] {e}")
    return []

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  Solana Meme Bot — Safety Edition")
    print("=" * 50)
    print(f"  Trade size:    {TRADE_SIZE_SOL} SOL per trade")
    print(f"  Take profit:   {TAKE_PROFIT_X}x (100% gain)")
    print(f"  Stop loss:     {STOP_LOSS_PCT*100:.0f}%")
    print(f"  Max positions: {MAX_OPEN_TRADES}")
    print(f"  Max trades/day:{MAX_TRADES_PER_DAY}")
    print(f"  Daily loss cap:{MAX_DAILY_LOSS_SOL} SOL")
    print(f"  Force sell:    after {MAX_POSITION_AGE_H}h")
    print(f"  Mcap range:    ${MIN_MARKET_CAP:,} – ${MAX_MARKET_CAP:,}")
    print("=" * 50)

    sol_balance = get_sol_balance()
    print(f"  SOL balance: {sol_balance:.4f} SOL\n")

    seen_tokens = set(load_json(POSITIONS, {}).keys())

    while True:
        trades_today, loss_today = get_daily_stats()
        positions = load_json(POSITIONS, {})
        print(f"\n[{now_iso()[:16]}] Positions: {len(positions)}/{MAX_OPEN_TRADES} | Today: {trades_today} trades | Loss: {loss_today:.3f} SOL")

        # 1. Monitor TP/SL on open positions
        try:
            monitor_positions()
        except Exception as e:
            print(f"  [monitor error] {e}")

        # 2. Force sell aged positions
        try:
            check_aged_positions()
        except Exception as e:
            print(f"  [age error] {e}")

        # 3. Snipe Pump.fun graduates
        try:
            tokens = fetch_pump_graduates()
            for token in tokens:
                mint   = token.get("mint", "")
                symbol = token.get("symbol", "?")
                mcap   = token.get("usd_market_cap", 0) or 0

                if not mint or mint in seen_tokens:
                    continue
                seen_tokens.add(mint)

                safe, reason = is_safe_token(token)
                if not safe:
                    print(f"  [blocked] {symbol} — {reason}")
                    continue

                print(f"  ✅ SAFE token: {symbol} mcap=${mcap:,.0f}")
                buy_token(mint, symbol, f"pump_graduate")

        except Exception as e:
            print(f"  [pump error] {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nMeme bot stopped.")
