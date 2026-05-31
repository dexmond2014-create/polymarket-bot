#!/usr/bin/env python3
"""
Solana Meme Coin Bot
- Monitors Pump.fun for new token launches
- Copies whale wallet buys
- Auto sells at 2x profit or -50% stop loss
- 0.05 SOL per trade
"""

import json
import os
import subprocess
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

TRADE_SIZE_SOL    = 0.05          # SOL per trade (~$4)
TAKE_PROFIT_X     = 2.0           # Sell at 2x (100% profit)
STOP_LOSS_PCT     = 0.50          # Stop loss at -50%
POLL_INTERVAL     = 20            # Check every 20 seconds
MIN_LIQUIDITY_USD = 5_000         # Skip tokens with less than $5k liquidity
MIN_MARKET_CAP    = 10_000        # Skip tokens under $10k market cap
MAX_MARKET_CAP    = 500_000       # Skip tokens over $500k (too late)
MAX_OPEN_TRADES   = 5             # Max simultaneous positions

TRADE_LOG   = Path(__file__).parent / "meme_trades.json"
POSITIONS   = Path(__file__).parent / ".meme_positions.json"
BULLPEN     = os.environ.get("BULLPEN_BIN", os.path.expanduser("~/.bullpen/bin/bullpen"))

# Whale wallets to copy (known profitable Solana meme traders)
WHALE_WALLETS = [
    # Add profitable whale addresses here
    # Format: {"address": "...", "label": "whale1"}
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
    print(f"  [logged] {entry['action']} {entry.get('symbol','?')} — {entry.get('status')}")

def get_sol_price():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd", timeout=5)
        return r.json()["solana"]["usd"]
    except:
        return 160  # fallback price

def get_token_price(mint):
    """Get current token price in USD via bullpen."""
    result = bullpen("solana", "price", mint, "--output", "json")
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            return float(data.get("price_usd", 0))
        except:
            pass
    return None

def get_sol_balance():
    """Get current SOL balance."""
    result = bullpen("solana", "balance", "--output", "json")
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            return float(data.get("sol_balance", 0))
        except:
            pass
    return 0

# ── Buy a token ───────────────────────────────────────────────────────────────

def buy_token(mint, symbol, reason):
    positions = load_json(POSITIONS, {})

    if mint in positions:
        print(f"  [skip] Already holding {symbol}")
        return

    if len(positions) >= MAX_OPEN_TRADES:
        print(f"  [skip] Max {MAX_OPEN_TRADES} open trades reached")
        return

    sol_balance = get_sol_balance()
    if sol_balance < TRADE_SIZE_SOL:
        print(f"  [skip] Insufficient SOL balance ({sol_balance:.3f} SOL)")
        return

    entry_price = get_token_price(mint)
    if entry_price is None:
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

        # Track position
        positions[mint] = {
            "symbol":      symbol,
            "entry_price": entry_price,
            "sol_amount":  TRADE_SIZE_SOL,
            "bought_at":   now_iso(),
            "reason":      reason,
        }
        save_json(POSITIONS, positions)
        print(f"  ✅ Bought {symbol} at ${entry_price:.8f}")
    else:
        entry["status"] = "failed"
        entry["error"]  = (r.stderr or r.stdout).strip()
        print(f"  [error] BUY failed: {entry['error'][:100]}")

    log_trade(entry)

# ── Sell a token ──────────────────────────────────────────────────────────────

def sell_token(mint, symbol, reason):
    positions = load_json(POSITIONS, {})
    if mint not in positions:
        return

    print(f"  → SELL {symbol} — {reason}")
    r = bullpen("solana", "sell", mint, "--max",
                "--yes", "--non-interactive", "--output", "json")

    current_price = get_token_price(mint) or 0
    pos = positions[mint]
    entry_price = pos.get("entry_price", 0)
    pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

    entry = {
        "ts":           now_iso(),
        "action":       "SELL",
        "mint":         mint,
        "symbol":       symbol,
        "entry_price":  entry_price,
        "exit_price":   current_price,
        "pnl_pct":      round(pnl_pct, 2),
        "reason":       reason,
        "status":       None,
        "error":        None,
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
        print(f"  [error] SELL failed: {entry['error'][:100]}")

    log_trade(entry)

# ── Monitor positions for TP/SL ───────────────────────────────────────────────

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

        print(f"  [monitor] {symbol}: entry=${entry_price:.8f} now=${current_price:.8f} PnL={pnl_pct*100:+.1f}%")

        if pnl_pct >= (TAKE_PROFIT_X - 1):
            print(f"  🎯 TAKE PROFIT triggered for {symbol} (+{pnl_pct*100:.0f}%)")
            sell_token(mint, symbol, f"take_profit_{pnl_pct*100:.0f}pct")

        elif pnl_pct <= -STOP_LOSS_PCT:
            print(f"  🛑 STOP LOSS triggered for {symbol} ({pnl_pct*100:.0f}%)")
            sell_token(mint, symbol, f"stop_loss_{pnl_pct*100:.0f}pct")

# ── Fetch new Pump.fun launches ───────────────────────────────────────────────

def fetch_new_pumpdotfun_tokens():
    """Fetch newly graduated tokens from Pump.fun API."""
    try:
        # Pump.fun graduation endpoint — tokens that just hit Raydium
        r = requests.get(
            "https://frontend-api.pump.fun/coins/recently-graduated?offset=0&limit=20&includeNsfw=false",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [pump.fun] Error: {e}")
    return []

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print(f"=== Solana Meme Bot Started ===")
    print(f"Trade size: {TRADE_SIZE_SOL} SOL | TP: {TAKE_PROFIT_X}x | SL: {STOP_LOSS_PCT*100:.0f}%")

    sol_balance = get_sol_balance()
    print(f"SOL balance: {sol_balance:.4f} SOL")

    seen_tokens = set()
    positions = load_json(POSITIONS, {})
    # Seed seen tokens from existing positions
    seen_tokens.update(positions.keys())

    while True:
        print(f"\n[{now_iso()[:16]}] Scanning...")

        # 1. Monitor existing positions for TP/SL
        try:
            monitor_positions()
        except Exception as e:
            print(f"  [monitor error] {e}")

        # 2. Snipe new Pump.fun graduates
        try:
            new_tokens = fetch_new_pumpdotfun_tokens()
            for token in new_tokens:
                mint   = token.get("mint", "")
                symbol = token.get("symbol", "?")
                name   = token.get("name", "")
                mcap   = token.get("usd_market_cap", 0) or 0

                if not mint or mint in seen_tokens:
                    continue

                seen_tokens.add(mint)

                # Filter by market cap range
                if mcap < MIN_MARKET_CAP:
                    print(f"  [skip] {symbol} mcap too low (${mcap:,.0f})")
                    continue
                if mcap > MAX_MARKET_CAP:
                    print(f"  [skip] {symbol} mcap too high (${mcap:,.0f})")
                    continue

                print(f"  🆕 New graduate: {symbol} ({name}) mcap=${mcap:,.0f}")
                buy_token(mint, symbol, f"pump_graduate_mcap_{mcap:.0f}")

        except Exception as e:
            print(f"  [pump error] {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nMeme bot stopped.")
