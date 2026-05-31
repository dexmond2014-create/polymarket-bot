#!/usr/bin/env python3
"""
Polymarket copy trading bot.
Watches target traders every 30s and mirrors their trades via bullpen CLI.
"""

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

# Slugs containing these keywords will be skipped (long-term futures only)
SKIP_SLUG_KEYWORDS = [
    "win-the-2026-fifa-world-cup",
    "win-the-world-cup",
    "world-cup-winner",
    "world-cup-champion",
]

TARGETS = [
    # Top active traders by PnL — currently trading sports/FIFA
    {"address": "0x204f72f35326db932158CBA6AdfF0B9A1DA95e14", "label": "swisstony"},
    {"address": "0x2005D16a84CEEfa912D4e380cD32E7ff827875Ea", "label": "RN1"},
    {"address": "0x6A72f61820b26b1fe4d956E17B6DC2A1Ea3033EE", "label": "kch123"},
]

TRADE_SIZE_USD    = 3.0
MAX_TRADES_PER_DAY = 5              # max trades per day to protect balance
POLL_INTERVAL     = 30
TRADER_CHECK_SECS = 24 * 60 * 60   # check trader activity every 24 hours
TRADE_LOG         = Path(__file__).parent / "trades.json"
SEEN_LOG          = Path(__file__).parent / ".seen_txns.json"
POSITIONS_LOG     = Path(__file__).parent / ".positions.json"
TARGETS_LOG       = Path(__file__).parent / ".targets.json"
BULLPEN           = os.environ.get("BULLPEN_BIN", os.path.expanduser("~/.bullpen/bin/bullpen"))

# Minimum 7d volume to be considered active ($100k)
MIN_VOLUME_7D = 100_000

# ── In-memory position tracker ────────────────────────────────────────────────
# keyed by "slug::outcome" (lowercase) → True/False (we hold it)
held_positions: dict = {}


def pos_key(slug: str, outcome: str) -> str:
    return f"{slug}::{outcome}".lower()


def load_positions_from_log():
    """Rebuild held positions from trades.json on startup — most reliable source."""
    trades = load_json(TRADE_LOG, [])
    pos: dict = {}
    for t in sorted(trades, key=lambda x: x.get("ts", "")):
        key = pos_key(t.get("slug", ""), t.get("outcome", ""))
        if t.get("action") == "BUY" and t.get("status") == "filled":
            pos[key] = True
        elif t.get("action") == "SELL" and t.get("status") == "filled":
            pos.pop(key, None)
    return pos


def seed_positions_from_api():
    """Also check live API positions and merge — catches anything bought outside the bot."""
    result = bullpen("polymarket", "positions", "--output", "json")
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
        pos = {}
        for p in data.get("positions", []):
            if p.get("shares", 0) > 0:
                key = pos_key(p.get("slug", ""), p.get("outcome", ""))
                pos[key] = True
        return pos
    except Exception:
        return {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def bullpen(*args, capture=True):
    cmd = [BULLPEN] + list(args)
    result = subprocess.run(cmd, capture_output=capture, text=True)
    return result


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2))


def log_trade(entry: dict):
    trades = load_json(TRADE_LOG, [])
    trades.append(entry)
    save_json(TRADE_LOG, trades)
    print(f"  [logged] {entry['action']} {entry['slug']} / {entry['outcome']} — {entry.get('status')}")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Execute a copy trade ──────────────────────────────────────────────────────

def copy_buy(slug: str, outcome: str, trader_label: str, txn_hash: str):
    global held_positions
    entry = {
        "ts":          now_iso(),
        "action":      "BUY",
        "slug":        slug,
        "outcome":     outcome,
        "amount_usd":  TRADE_SIZE_USD,
        "copied_from": trader_label,
        "copied_txn":  txn_hash,
        "status":      None,
        "error":       None,
    }
    try:
        print(f"  → BUY ${TRADE_SIZE_USD} {outcome} on {slug}")
        r = bullpen(
            "polymarket", "buy",
            slug, outcome, str(TRADE_SIZE_USD),
            "--yes", "--non-interactive", "--output", "json",
        )
        if r.returncode == 0:
            entry["status"] = "filled"
            try:
                entry["response"] = json.loads(r.stdout)
            except Exception:
                entry["response"] = r.stdout.strip()
            # ✅ Track position immediately in memory
            held_positions[pos_key(slug, outcome)] = True
            save_json(POSITIONS_LOG, held_positions)
        else:
            entry["status"] = "failed"
            entry["error"]  = (r.stderr or r.stdout).strip()
            print(f"  [error] BUY failed: {entry['error']}")
    except Exception as e:
        entry["status"] = "exception"
        entry["error"]  = str(e)
        print(f"  [error] BUY exception: {e}")
    log_trade(entry)


def copy_sell(slug: str, outcome: str, trader_label: str, txn_hash: str):
    global held_positions
    entry = {
        "ts":          now_iso(),
        "action":      "SELL",
        "slug":        slug,
        "outcome":     outcome,
        "copied_from": trader_label,
        "copied_txn":  txn_hash,
        "status":      None,
        "error":       None,
    }
    try:
        print(f"  → SELL all {outcome} on {slug}")
        r = bullpen(
            "polymarket", "sell",
            slug, outcome,
            "--max", "--yes", "--non-interactive", "--output", "json",
        )
        if r.returncode == 0:
            entry["status"] = "filled"
            try:
                entry["response"] = json.loads(r.stdout)
            except Exception:
                entry["response"] = r.stdout.strip()
            # ✅ Remove position immediately from memory
            held_positions.pop(pos_key(slug, outcome), None)
            save_json(POSITIONS_LOG, held_positions)
        else:
            entry["status"] = "failed"
            entry["error"]  = (r.stderr or r.stdout).strip()
            print(f"  [error] SELL failed: {entry['error']}")
    except Exception as e:
        entry["status"] = "exception"
        entry["error"]  = str(e)
        print(f"  [error] SELL exception: {e}")
    log_trade(entry)


# ── Auto-redeem resolved positions ───────────────────────────────────────────

def auto_redeem():
    """Check for redeemable positions and redeem them all in one shot."""
    result = bullpen("polymarket", "positions", "--output", "json")
    if result.returncode != 0:
        return
    try:
        data = json.loads(result.stdout)
    except Exception:
        return

    redeemable = [p for p in data.get("positions", []) if p.get("redeemable")]
    if not redeemable:
        return

    condition_ids = ",".join(p["condition_id"] for p in redeemable)
    slugs = [f"{p['slug']} / {p['outcome']}" for p in redeemable]
    print(f"  → REDEEM {len(redeemable)} resolved position(s): {slugs}")

    r = bullpen(
        "polymarket", "redeem",
        "--condition-ids", condition_ids,
        "--yes", "--non-interactive", "--output", "json",
    )

    for p in redeemable:
        entry = {
            "ts":          now_iso(),
            "action":      "REDEEM",
            "slug":        p.get("slug", ""),
            "outcome":     p.get("outcome", ""),
            "copied_from": "auto",
            "copied_txn":  None,
            "status":      "filled" if r.returncode == 0 else "failed",
            "error":       None if r.returncode == 0 else (r.stderr or r.stdout).strip(),
        }
        if r.returncode == 0:
            try:
                entry["response"] = json.loads(r.stdout)
            except Exception:
                entry["response"] = r.stdout.strip()
            # Remove from held positions
            held_positions.pop(pos_key(p.get("slug", ""), p.get("outcome", "")), None)
        else:
            entry["error"] = (r.stderr or r.stdout).strip()
            print(f"  [error] REDEEM failed: {entry['error']}")
        log_trade(entry)

    if r.returncode == 0:
        save_json(POSITIONS_LOG, held_positions)


# ── Fetch recent trades for a target ─────────────────────────────────────────

def fetch_trades(address: str) -> list:
    result = bullpen(
        "polymarket", "activity",
        "--address", address,
        "--type", "trade",
        "--limit", "10",
        "--output", "json",
    )
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout).get("activities", [])
    except Exception:
        return []


# ── Trader activity check & auto-rotation ────────────────────────────────────

def get_last_trade_ts(address: str) -> Optional[str]:
    """Return ISO timestamp of most recent trade for this address, or None."""
    result = bullpen(
        "polymarket", "activity",
        "--address", address,
        "--type", "trade",
        "--limit", "1",
        "--output", "json",
    )
    if result.returncode != 0:
        return None
    try:
        activities = json.loads(result.stdout).get("activities", [])
        if activities:
            return activities[0].get("timestamp")
    except Exception:
        pass
    return None


def fetch_top_traders(limit: int = 50) -> list:
    """Fetch leaderboard and return top active traders sorted by 7d PnL."""
    result = bullpen(
        "polymarket", "data", "leaderboard",
        "--sort", "pnl",
        "--time-period", "7d",
        "--hide-bots", "--hide-farmers",
        "--limit", str(limit),
        "--output", "json",
    )
    if result.returncode != 0:
        return []
    try:
        traders = json.loads(result.stdout).get("leaderboard", [])
        # Filter by minimum volume
        return [t for t in traders if (t.get("volume_7d") or 0) >= MIN_VOLUME_7D]
    except Exception:
        return []


def check_and_rotate_traders(targets: list, seen: dict) -> list:
    """Check each trader for activity in last 24h. Replace inactive ones."""
    now = datetime.now(timezone.utc)
    inactive = []

    for t in targets:
        last_ts = get_last_trade_ts(t["address"])
        if last_ts is None:
            print(f"[rotation] Could not check {t['label']} — skipping")
            continue
        try:
            # Parse timestamp
            ts_str = last_ts.replace("UTC", "+00:00").replace(" ", "T")
            last_dt = datetime.fromisoformat(ts_str)
            hours_ago = (now - last_dt).total_seconds() / 3600
            if hours_ago > 24:
                print(f"[rotation] {t['label']} inactive for {hours_ago:.1f}h — marking for replacement")
                inactive.append(t["address"])
            else:
                print(f"[rotation] {t['label']} active — last trade {hours_ago:.1f}h ago ✅")
        except Exception as e:
            print(f"[rotation] Error parsing timestamp for {t['label']}: {e}")

    if not inactive:
        return targets

    # Fetch fresh top traders
    print(f"[rotation] Fetching new traders to replace {len(inactive)} inactive...")
    top = fetch_top_traders(50)
    current_addresses = {t["address"].lower() for t in targets}

    new_targets = [t for t in targets if t["address"] not in inactive]

    for trader in top:
        addr = trader.get("wallet_address", "")
        name = trader.get("display_name") or addr[:10]
        if addr.lower() not in current_addresses and addr not in inactive:
            label = f"{addr[:6]}_{name[:12]}"
            new_targets.append({"address": addr, "label": label})
            seen.setdefault(addr, [])
            print(f"[rotation] ✅ Added new trader: {label} (7d PnL: ${trader.get('realized_pnl_7d', 0):,.0f})")
            current_addresses.add(addr.lower())
            if len(new_targets) >= len(targets):
                break

    # Save updated targets
    save_json(TARGETS_LOG, new_targets)
    print(f"[rotation] Targets updated: {[t['label'] for t in new_targets]}")
    return new_targets


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    global held_positions

    print(f"Polymarket copy bot started — watching {len(TARGETS)} traders, ${TRADE_SIZE_USD}/trade, every {POLL_INTERVAL}s")
    print(f"Trade log: {TRADE_LOG}")

    # Seed positions: trades.json is source of truth, API fills any gaps
    held_positions = load_positions_from_log()
    api_positions  = seed_positions_from_api()
    held_positions.update(api_positions)
    save_json(POSITIONS_LOG, held_positions)
    print(f"Loaded {len(held_positions)} held position(s): {list(held_positions.keys()) or 'none'}\n")

    seen: dict = load_json(SEEN_LOG, {})

    # Load saved targets or use defaults
    targets = load_json(TARGETS_LOG, TARGETS)
    for t in targets:
        seen.setdefault(t["address"], [])

    last_rotation_check = time.time()

    while True:
        # ── Auto-redeem any resolved positions first ───────────────────────
        try:
            auto_redeem()
        except Exception as e:
            print(f"[redeem error] {e}")

        # ── Check trader activity every 24h and rotate if needed ───────────
        if time.time() - last_rotation_check >= TRADER_CHECK_SECS:
            print(f"[{now_iso()}] Running 24h trader activity check...")
            try:
                targets = check_and_rotate_traders(targets, seen)
            except Exception as e:
                print(f"[rotation error] {e}")
            last_rotation_check = time.time()

        for target in targets:
            addr  = target["address"]
            label = target["label"]
            try:
                trades = fetch_trades(addr)
            except Exception as e:
                print(f"[poll error] {label}: {e}")
                continue

            seen_set  = set(seen[addr])
            new_trades = [t for t in trades if t.get("transaction_hash") not in seen_set]

            if new_trades:
                print(f"[{now_iso()}] {label}: {len(new_trades)} new trade(s)")

            for trade in new_trades:
                txn     = trade.get("transaction_hash", "")
                slug    = trade.get("slug", "")
                outcome = trade.get("outcome", "")
                side    = trade.get("side", "").upper()

                if not slug or not outcome:
                    seen_set.add(txn)
                    continue

                # Skip long-term futures (World Cup winners etc)
                if any(kw in slug for kw in SKIP_SLUG_KEYWORDS):
                    print(f"  [skip] {side} {outcome} on {slug} — long-term future, skipping")
                    log_trade({
                        "ts": now_iso(), "action": f"{side}_SKIPPED",
                        "slug": slug, "outcome": outcome,
                        "copied_from": label, "copied_txn": txn,
                        "status": "long_term_future", "error": None,
                    })
                    seen_set.add(txn)
                    continue

                key = pos_key(slug, outcome)

                # Check daily trade limit per trader
                today = now_iso()[:10]
                trades_today = load_json(TRADE_LOG, [])
                buys_today_trader = [t for t in trades_today if t.get('action') == 'BUY' and t.get('status') == 'filled' and t.get('ts','')[:10] == today and t.get('copied_from') == label]
                if len(buys_today_trader) >= MAX_TRADES_PER_DAY:
                    print(f"  [limit] {label} daily limit of {MAX_TRADES_PER_DAY} reached — skipping {side} {outcome} on {slug}")
                    seen_set.add(txn)
                    continue

                if side == "BUY":
                    if held_positions.get(key):
                        # Already hold this — skip to avoid buying same market multiple times
                        print(f"  [skip] BUY {outcome} on {slug} — already holding this position")
                        log_trade({
                            "ts":          now_iso(),
                            "action":      "BUY_SKIPPED",
                            "slug":        slug,
                            "outcome":     outcome,
                            "copied_from": label,
                            "copied_txn":  txn,
                            "status":      "already_held",
                            "error":       None,
                        })
                    else:
                        copy_buy(slug, outcome, label, txn)

                elif side == "SELL":
                    if held_positions.get(key):
                        # ✅ We hold this — sell immediately
                        copy_sell(slug, outcome, label, txn)
                    else:
                        print(f"  [skip] SELL {outcome} on {slug} — not in our positions")
                        log_trade({
                            "ts":          now_iso(),
                            "action":      "SELL_SKIPPED",
                            "slug":        slug,
                            "outcome":     outcome,
                            "copied_from": label,
                            "copied_txn":  txn,
                            "status":      "no_position",
                            "error":       None,
                        })

                seen_set.add(txn)

            seen[addr] = list(seen_set)

        save_json(SEEN_LOG, seen)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot stopped.")
