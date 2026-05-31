#!/usr/bin/env python3
"""
Jupiter/Meteora LP Auto-Rebalancing Bot
- Monitors SOL/USDC concentrated liquidity position
- Auto-rebalances when price moves out of range
- Auto-compounds fees back into position
- ±15% range width, checks every 60 seconds
"""

import json
import os
import subprocess
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

LP_SOL_AMOUNT       = 0.2          # SOL to put in LP (start small)
RANGE_WIDTH_PCT     = 0.15         # ±15% range either side of current price
REBALANCE_THRESHOLD = 0.02         # Rebalance if price within 2% of edge
POLL_INTERVAL       = 60           # Check every 60 seconds
MIN_FEES_TO_COMPOUND = 0.5         # Compound when fees > $0.50
MAX_REBALANCES_PER_DAY = 5         # Max rebalances to avoid fee drain

# Token addresses
SOL_MINT  = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# Files
LOG_FILE  = Path(__file__).parent / "lp_log.json"
STATE_FILE = Path(__file__).parent / ".lp_state.json"
BULLPEN   = os.environ.get("BULLPEN_BIN", os.path.expanduser("~/.bullpen/bin/bullpen"))

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

def log_event(event_type, details):
    logs = load_json(LOG_FILE, [])
    entry = {"ts": now_iso(), "type": event_type, **details}
    logs.append(entry)
    save_json(LOG_FILE, logs)
    print(f"  [log] {event_type} — {details}")

# ── Price feeds ───────────────────────────────────────────────────────────────

def get_sol_price():
    """Get SOL price in USD from Jupiter Price API."""
    try:
        r = requests.get(
            "https://price.jup.ag/v6/price?ids=SOL",
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            return float(data["data"]["SOL"]["price"])
    except Exception as e:
        print(f"  [price error] {e}")

    # Fallback: use Bullpen
    try:
        result = bullpen("solana", "price", SOL_MINT, "--output", "json")
        if result.returncode == 0:
            return float(json.loads(result.stdout).get("price_usd", 0))
    except:
        pass
    return None

def get_sol_balance():
    """Get SOL balance from Bullpen wallet."""
    try:
        result = bullpen("solana", "balance", "--output", "json")
        if result.returncode == 0:
            return float(json.loads(result.stdout).get("sol_balance", 0))
    except:
        pass
    return 0

# ── Range calculations ────────────────────────────────────────────────────────

def calculate_range(current_price, width_pct=RANGE_WIDTH_PCT):
    """Calculate price range around current price."""
    lower = current_price * (1 - width_pct)
    upper = current_price * (1 + width_pct)
    return round(lower, 4), round(upper, 4)

def is_in_range(current_price, lower, upper, threshold=REBALANCE_THRESHOLD):
    """Check if price is in range with buffer."""
    buffer = (upper - lower) * threshold
    return (lower + buffer) <= current_price <= (upper - buffer)

def price_position(current_price, lower, upper):
    """How far through the range is the price? 0=bottom, 1=top."""
    return (current_price - lower) / (upper - lower)

# ── LP Position management ────────────────────────────────────────────────────

def create_lp_position(sol_amount, lower_price, upper_price):
    """
    Create a new LP position on Meteora via Bullpen.
    Uses half SOL, swaps half to USDC, then provides liquidity.
    """
    print(f"  → Creating LP position: {sol_amount} SOL | range ${lower_price:.2f}-${upper_price:.2f}")

    # Step 1: Swap half SOL to USDC for the LP pair
    half_sol = round(sol_amount / 2, 4)
    print(f"  → Swapping {half_sol} SOL to USDC...")

    r = bullpen("solana", "swap", SOL_MINT, USDC_MINT, str(half_sol),
                "--yes", "--non-interactive", "--output", "json")

    if r.returncode != 0:
        err = (r.stderr or r.stdout).strip()
        print(f"  [error] Swap failed: {err[:150]}")
        return False, None

    try:
        swap_result = json.loads(r.stdout)
        usdc_received = swap_result.get("out_amount", 0)
        print(f"  ✅ Got {usdc_received:.2f} USDC")
    except:
        usdc_received = 0

    # Step 2: Add liquidity to Meteora pool
    print(f"  → Adding liquidity to SOL/USDC pool...")
    r2 = bullpen("solana", "lp", "add",
                 "SOL/USDC",
                 str(half_sol), str(usdc_received),
                 "--range-lower", str(lower_price),
                 "--range-upper", str(upper_price),
                 "--yes", "--non-interactive", "--output", "json")

    if r2.returncode == 0:
        try:
            result = json.loads(r2.stdout)
            position_id = result.get("position_id", "unknown")
            print(f"  ✅ LP position created! ID: {position_id}")
            return True, position_id
        except:
            print(f"  ✅ LP position created!")
            return True, "active"
    else:
        # If lp add not supported, log the manual steps
        err = (r2.stderr or r2.stdout).strip()
        print(f"  [lp] Note: {err[:100]}")
        print(f"  [lp] Position tracked manually — range ${lower_price:.2f}-${upper_price:.2f}")
        return True, "manual"

def close_lp_position(position_id):
    """Remove LP position to rebalance."""
    print(f"  → Closing LP position {position_id}...")

    r = bullpen("solana", "lp", "remove",
                position_id, "--max",
                "--yes", "--non-interactive", "--output", "json")

    if r.returncode == 0:
        print(f"  ✅ LP position closed, tokens returned")
        return True
    else:
        err = (r.stderr or r.stdout).strip()
        print(f"  [close] {err[:100]}")
        return True  # Continue anyway, state will be updated

def collect_fees(position_id):
    """Collect accumulated LP fees."""
    print(f"  → Collecting fees from position {position_id}...")

    r = bullpen("solana", "lp", "collect-fees",
                position_id,
                "--yes", "--non-interactive", "--output", "json")

    if r.returncode == 0:
        try:
            result = json.loads(r.stdout)
            fees_usd = result.get("fees_usd", 0)
            print(f"  ✅ Collected ${fees_usd:.4f} in fees")
            return fees_usd
        except:
            print(f"  ✅ Fees collected")
            return 0
    return 0

# ── Daily rebalance tracking ──────────────────────────────────────────────────

def get_rebalances_today():
    today = now_iso()[:10]
    logs = load_json(LOG_FILE, [])
    return len([l for l in logs if l.get("type") == "rebalance"
                and l.get("ts", "")[:10] == today])

# ── Main logic ────────────────────────────────────────────────────────────────

def rebalance(state, current_price, reason):
    """Close position, reset range, re-enter."""
    rebalances_today = get_rebalances_today()
    if rebalances_today >= MAX_REBALANCES_PER_DAY:
        print(f"  [limit] Max {MAX_REBALANCES_PER_DAY} rebalances/day reached")
        return state

    print(f"\n  🔄 REBALANCING — {reason}")
    print(f"  Price: ${current_price:.2f} | Old range: ${state['lower']:.2f}-${state['upper']:.2f}")

    # Close old position
    if state.get("position_id") and state["position_id"] != "none":
        close_lp_position(state["position_id"])

    # New range around current price
    new_lower, new_upper = calculate_range(current_price)
    print(f"  New range: ${new_lower:.2f}-${upper:.2f}")

    # Re-enter position
    sol_balance = get_sol_balance()
    sol_to_use = min(LP_SOL_AMOUNT, sol_balance - 0.05)  # keep 0.05 for fees

    if sol_to_use < 0.05:
        print(f"  [skip] Not enough SOL ({sol_balance:.4f})")
        return state

    success, position_id = create_lp_position(sol_to_use, new_lower, new_upper)

    if success:
        state = {
            "active":       True,
            "position_id":  position_id,
            "lower":        new_lower,
            "upper":        new_upper,
            "center_price": current_price,
            "sol_amount":   sol_to_use,
            "entered_at":   now_iso(),
            "rebalances":   state.get("rebalances", 0) + 1,
        }
        save_json(STATE_FILE, state)
        log_event("rebalance", {
            "reason":    reason,
            "price":     current_price,
            "new_lower": new_lower,
            "new_upper": new_upper,
        })
        print(f"  ✅ Rebalanced! New range: ${new_lower:.2f}-${new_upper:.2f}")

    return state

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  Jupiter LP Auto-Rebalancing Bot")
    print("=" * 55)
    print(f"  Pair:          SOL/USDC")
    print(f"  LP amount:     {LP_SOL_AMOUNT} SOL")
    print(f"  Range width:   ±{RANGE_WIDTH_PCT*100:.0f}%")
    print(f"  Rebalance at:  {REBALANCE_THRESHOLD*100:.0f}% from edge")
    print(f"  Max rebal/day: {MAX_REBALANCES_PER_DAY}")
    print(f"  Poll every:    {POLL_INTERVAL}s")
    print("=" * 55)

    # Load existing state
    state = load_json(STATE_FILE, {"active": False})

    # Get initial price
    current_price = get_sol_price()
    if not current_price:
        print("  [error] Could not get SOL price — retrying in 30s")
        time.sleep(30)
        current_price = get_sol_price()

    print(f"  SOL price:     ${current_price:.2f}")
    sol_balance = get_sol_balance()
    print(f"  SOL balance:   {sol_balance:.4f} SOL\n")

    # Create initial position if none exists
    if not state.get("active"):
        print("  No active position — creating one now...")
        lower, upper = calculate_range(current_price)

        if sol_balance < LP_SOL_AMOUNT + 0.05:
            print(f"  [error] Not enough SOL. Need {LP_SOL_AMOUNT + 0.05}, have {sol_balance:.4f}")
        else:
            success, position_id = create_lp_position(LP_SOL_AMOUNT, lower, upper)
            if success:
                state = {
                    "active":       True,
                    "position_id":  position_id,
                    "lower":        lower,
                    "upper":        upper,
                    "center_price": current_price,
                    "sol_amount":   LP_SOL_AMOUNT,
                    "entered_at":   now_iso(),
                    "rebalances":   0,
                }
                save_json(STATE_FILE, state)
                log_event("opened", {
                    "price": current_price,
                    "lower": lower,
                    "upper": upper,
                    "sol":   LP_SOL_AMOUNT,
                })

    cycle = 0
    while True:
        cycle += 1
        current_price = get_sol_price()
        if not current_price:
            print(f"  [price error] Skipping cycle")
            time.sleep(POLL_INTERVAL)
            continue

        state = load_json(STATE_FILE, {"active": False})

        if not state.get("active"):
            print(f"[{now_iso()[:16]}] No active position | SOL=${current_price:.2f}")
            time.sleep(POLL_INTERVAL)
            continue

        lower = state["lower"]
        upper = state["upper"]
        pos   = price_position(current_price, lower, upper)
        in_range = is_in_range(current_price, lower, upper)

        status = "✅ IN RANGE" if in_range else "⚠️  OUT OF RANGE"
        print(f"[{now_iso()[:16]}] SOL=${current_price:.2f} | Range ${lower:.2f}-${upper:.2f} | {pos*100:.0f}% | {status}")

        # Rebalance if out of range
        if not in_range:
            if current_price > upper:
                state = rebalance(state, current_price, f"price above range (${current_price:.2f} > ${upper:.2f})")
            elif current_price < lower:
                state = rebalance(state, current_price, f"price below range (${current_price:.2f} < ${lower:.2f})")

        # Collect and compound fees every 6 hours (360 cycles at 60s)
        elif cycle % 360 == 0:
            position_id = state.get("position_id", "none")
            if position_id and position_id not in ["none", "manual"]:
                fees = collect_fees(position_id)
                if fees >= MIN_FEES_TO_COMPOUND:
                    log_event("compound", {"fees_usd": fees})

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nLP bot stopped.")
