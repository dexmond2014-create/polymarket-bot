#!/usr/bin/env python3
"""
Solana Meme Bot v2 — Smarter entry, whale copying, better filters
- Catches tokens EARLIER (near graduation, not after)
- Copies top Pump.fun whale wallets
- Volume spike + momentum detection
- Full safety: stop loss, take profit, honeypot protection
"""

import json
import os
import subprocess
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

TRADE_SIZE_SOL      = 0.05        # SOL per trade
TAKE_PROFIT_X       = 2.0         # Sell at 2x
STOP_LOSS_PCT       = 0.50        # Stop loss at -50%
POLL_INTERVAL       = 15          # Check every 15 seconds (faster)
MAX_OPEN_TRADES     = 5
MAX_TRADES_PER_DAY  = 10
MAX_DAILY_LOSS_SOL  = 0.25
MAX_POSITION_AGE_H  = 24

# Token filters
MIN_MARKET_CAP      = 15_000      # Lower = catch earlier
MAX_MARKET_CAP      = 200_000     # Lower = better risk/reward
MIN_REPLIES         = 8           # Community activity
MIN_VOLUME_5M       = 5_000       # Must have $5k+ volume in last 5 min (momentum)
BONDING_CURVE_MIN   = 80          # Buy when bonding curve is 80%+ complete

# Scam keywords
SCAM_KEYWORDS = [
    "elon", "trump", "inu", "safe", "moon", "gem", "100x", "1000x",
    "rugproof", "safu", "based", "airdrop", "free", "presale", "doge",
    "shib", "pepe", "wojak", "baby", "mini", "reflection", "rebase"
]

# Top Pump.fun whale wallets to copy (proven profitable traders)
WHALE_WALLETS = [
    "GDfnEsia2WLAW5t8yx2X5j2mkfA74i5kwGdDuZHt7XmG",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
    "AVmoTthdrX6tKt4nDjco2D775W4sTnPiQBMHvnPqGT1f",
]

TRADE_LOG   = Path(__file__).parent / "meme_trades.json"
POSITIONS   = Path(__file__).parent / ".meme_positions.json"
BLACKLIST   = Path(__file__).parent / ".meme_blacklist.json"
BULLPEN     = os.environ.get("BULLPEN_BIN", os.path.expanduser("~/.bullpen/bin/bullpen"))

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
    mint    = token.get("mint", "")
    symbol  = token.get("symbol", "?").upper()
    name    = (token.get("name", "") or "").lower()
    mcap    = token.get("usd_market_cap", 0) or 0
    replies = token.get("reply_count", 0) or 0

    # Blacklist
    blacklist = load_json(BLACKLIST, [])
    if mint in blacklist:
        return False, "blacklisted"

    # Market cap filter
    if mcap < MIN_MARKET_CAP:
        return False, f"mcap too low (${mcap:,.0f})"
    if mcap > MAX_MARKET_CAP:
        return False, f"mcap too high (${mcap:,.0f})"

    # Community activity
    if replies < MIN_REPLIES:
        return False, f"low community ({replies} replies)"

    # Scam keyword check
    for kw in SCAM_KEYWORDS:
        if kw in name or kw in symbol.lower():
            return False, f"scam keyword: '{kw}'"

    # Must be graduated OR near graduation (80%+ bonding curve)
    bonding_pct = token.get("bonding_curve_percentage", 0) or 0
    graduated   = token.get("complete", False) or token.get("raydium_pool")
    if not graduated and bonding_pct < BONDING_CURVE_MIN:
        return False, f"too early (bonding {bonding_pct:.0f}%)"

    return True, "ok"

def has_momentum(token):
    """Check for volume spike — sign of whale activity."""
    volume_5m = token.get("volume_5m", 0) or 0
    if volume_5m > 0 and volume_5m < MIN_VOLUME_5M:
        return False, f"low volume ${volume_5m:,.0f}"
    return True, "ok"

# ── Fetch tokens ──────────────────────────────────────────────────────────────

def fetch_pump_graduates():
    """Fetch recently graduated tokens from Pump.fun."""
    try:
        r = requests.get(
            "https://frontend-api.pump.fun/coins/recently-graduated?offset=0&limit=20&includeNsfw=false",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [pump.fun graduates] {e}")
    return []

def fetch_near_graduation():
    """Fetch tokens close to graduating — earlier entry = more upside."""
    try:
        r = requests.get(
            "https://frontend-api.pump.fun/coins?offset=0&limit=50&sort=last_trade_timestamp&order=DESC&includeNsfw=false",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        if r.status_code == 200:
            tokens = r.json()
            # Filter for near-graduation tokens (80-99% bonding curve)
            near = []
            for t in tokens:
                bp = t.get("bonding_curve_percentage", 0) or 0
                if BONDING_CURVE_MIN <= bp < 100:
                    near.append(t)
            return near
    except Exception as e:
        print(f"  [pump.fun near-grad] {e}")
    return []

def fetch_whale_buys():
    """Check whale wallets for recent buys on Pump.fun."""
    tokens = []
    for wallet in WHALE_WALLETS:
        try:
            r = requests.get(
                f"https://frontend-api.pump.fun/trades/all?user={wallet}&limit=5&offset=0",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            if r.status_code == 200:
                trades = r.json()
                for trade in trades:
                    if trade.get("is_buy"):
                        mint = trade.get("mint", "")
                        if mint:
                            # Fetch token details
                            tr = requests.get(
                                f"https://frontend-api.pump.fun/coins/{mint}",
                                headers={"User-Agent": "Mozilla/5.0"},
                                timeout=10
                            )
                            if tr.status_code == 200:
                                token = tr.json()
                                token["_whale_source"] = wallet[:8]
                                tokens.append(token)
        except Exception as e:
            print(f"  [whale {wallet[:8]}] {e}")
    return tokens

# ── Force sell aged positions ─────────────────────────────────────────────────

def check_aged_positions():
    positions = load_json(POSITIONS, {})
    now = datetime.now(timezone.utc)
    for mint, pos in list(positions.items()):
        symbol    = pos.get("symbol", mint[:8])
        bought_at = pos.get("bought_at", "")
        try:
            bought_dt = datetime.fromisoformat(bought_at)
            age_hours = (now - bought_dt).total_seconds() / 3600
            if age_hours >= MAX_POSITION_AGE_H:
                print(f"  ⏰ FORCE SELL {symbol} — held {age_hours:.1f}h")
                sell_token(mint, symbol, f"max_age_{age_hours:.0f}h")
        except:
            pass

# ── Buy ───────────────────────────────────────────────────────────────────────

def buy_token(mint, symbol, reason):
    positions = load_json(POSITIONS, {})

    if mint in positions:
        return
    if len(positions) >= MAX_OPEN_TRADES:
        print(f"  [skip] Max {MAX_OPEN_TRADES} positions reached")
        return

    trades_today, loss_today = get_daily_stats()
    if trades_today >= MAX_TRADES_PER_DAY:
        print(f"  [limit] Daily trade limit reached")
        return
    if loss_today >= MAX_DAILY_LOSS_SOL:
        print(f"  [safety] Daily loss limit hit — pausing")
        return

    sol_balance = get_sol_balance()
    if sol_balance < TRADE_SIZE_SOL + 0.01:
        print(f"  [skip] Low SOL ({sol_balance:.4f})")
        return

    entry_price = get_token_price(mint)
    if not entry_price or entry_price <= 0:
        print(f"  [skip] No price for {symbol}")
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
        print(f"  ✅ Bought {symbol} @ ${entry_price:.8f} | Today: {trades_today+1}/{MAX_TRADES_PER_DAY}")
    else:
        entry["status"] = "failed"
        entry["error"]  = (r.stderr or r.stdout).strip()
        print(f"  [error] BUY failed: {entry['error'][:150]}")

    log_trade(entry)

# ── Sell ──────────────────────────────────────────────────────────────────────

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
        if "insufficient" not in entry["error"].lower():
            blacklist = load_json(BLACKLIST, [])
            if mint not in blacklist:
                blacklist.append(mint)
                save_json(BLACKLIST, blacklist)
                print(f"  [blacklist] {symbol} — possible honeypot")

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

# ── Process token list ────────────────────────────────────────────────────────

def process_tokens(tokens, seen_set, source):
    bought = 0
    for token in tokens:
        mint   = token.get("mint", "")
        symbol = token.get("symbol", "?")
        mcap   = token.get("usd_market_cap", 0) or 0

        if not mint or mint in seen_set:
            continue
        seen_set.add(mint)

        safe, reason = is_safe_token(token)
        if not safe:
            print(f"  [blocked] {symbol} ({source}) — {reason}")
            continue

        momentum_ok, m_reason = has_momentum(token)
        if not momentum_ok:
            print(f"  [no momentum] {symbol} — {m_reason}")
            continue

        whale = token.get("_whale_source", "")
        tag   = f"whale_{whale}" if whale else source
        print(f"  ✅ {symbol} mcap=${mcap:,.0f} — {tag}")
        buy_token(mint, symbol, tag)
        bought += 1
    return bought

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  Solana Meme Bot v2 — Smarter Entry Edition")
    print("=" * 55)
    print(f"  Trade size:      {TRADE_SIZE_SOL} SOL")
    print(f"  Take profit:     {TAKE_PROFIT_X}x (100% gain)")
    print(f"  Stop loss:       {STOP_LOSS_PCT*100:.0f}%")
    print(f"  Max positions:   {MAX_OPEN_TRADES}")
    print(f"  Daily loss cap:  {MAX_DAILY_LOSS_SOL} SOL")
    print(f"  Bonding curve:   Buy at {BONDING_CURVE_MIN}%+ (early entry)")
    print(f"  Whale wallets:   {len(WHALE_WALLETS)} tracked")
    print(f"  Poll interval:   {POLL_INTERVAL}s")
    print("=" * 55)

    sol_balance = get_sol_balance()
    print(f"  SOL balance: {sol_balance:.4f} SOL\n")

    seen_tokens = set(load_json(POSITIONS, {}).keys())
    cycle = 0

    while True:
        cycle += 1
        trades_today, loss_today = get_daily_stats()
        positions = load_json(POSITIONS, {})
        print(f"\n[{now_iso()[:16]}] Positions: {len(positions)}/{MAX_OPEN_TRADES} | Today: {trades_today} trades | Loss: {loss_today:.3f} SOL")

        # 1. Monitor TP/SL
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
            graduates = fetch_pump_graduates()
            process_tokens(graduates, seen_tokens, "graduated")
        except Exception as e:
            print(f"  [graduates error] {e}")

        # 4. Near-graduation tokens (early entry) — check every 2 cycles
        if cycle % 2 == 0:
            try:
                near = fetch_near_graduation()
                if near:
                    print(f"  [near-grad] {len(near)} tokens at {BONDING_CURVE_MIN}%+ bonding curve")
                process_tokens(near, seen_tokens, "near_graduation")
            except Exception as e:
                print(f"  [near-grad error] {e}")

        # 5. Whale wallet copying — check every 3 cycles
        if cycle % 3 == 0:
            try:
                whale_tokens = fetch_whale_buys()
                if whale_tokens:
                    print(f"  [whales] {len(whale_tokens)} buys detected")
                process_tokens(whale_tokens, seen_tokens, "whale_copy")
            except Exception as e:
                print(f"  [whale error] {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nMeme bot stopped.")
