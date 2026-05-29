#!/usr/bin/env python3
"""
Local dashboard server for the Polymarket copy bot.
Run: python3 server.py
Opens http://localhost:7373 in your browser automatically.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path
from typing import Optional

BASE    = Path(__file__).parent
BOT     = BASE / "copybot.py"
TRADES  = BASE / "trades.json"
PYTHON  = sys.executable
PORT    = 7373

bot_process: Optional[subprocess.Popen] = None
bot_lock = threading.Lock()

# ── SSE client registry ───────────────────────────────────────────────────────
sse_clients: list = []
sse_lock = threading.Lock()


def broadcast(data: dict):
    """Push a JSON payload to all connected SSE clients."""
    msg = f"data: {json.dumps(data)}\n\n".encode()
    with sse_lock:
        dead = []
        for wfile in sse_clients:
            try:
                wfile.write(msg)
                wfile.flush()
            except Exception:
                dead.append(wfile)
        for wfile in dead:
            sse_clients.remove(wfile)


def watch_trades():
    """Background thread — watches trades.json mtime and broadcasts on change."""
    last_mtime = None
    last_bot   = None
    while True:
        try:
            mtime     = TRADES.stat().st_mtime if TRADES.exists() else 0
            bot_state = bot_running()
            if mtime != last_mtime or bot_state != last_bot:
                last_mtime = mtime
                last_bot   = bot_state
                trades = load_trades()
                broadcast({
                    "bot_running": bot_state,
                    "stats":       compute_stats(trades),
                    "trades":      list(reversed(trades[-200:])),
                })
        except Exception:
            pass
        time.sleep(1)


# ── Bot control ───────────────────────────────────────────────────────────────

def bot_running() -> bool:
    with bot_lock:
        if bot_process is not None and bot_process.poll() is None:
            return True
    # Also detect copybot started outside the dashboard
    result = subprocess.run(["pgrep", "-f", "copybot.py"], capture_output=True, text=True)
    return result.returncode == 0


def start_bot():
    global bot_process
    with bot_lock:
        if bot_process is not None and bot_process.poll() is None:
            return {"ok": False, "error": "already running"}
        env = {**os.environ, "PATH": os.path.expanduser("~/.bullpen/bin") + ":" + os.environ.get("PATH", "")}
        bot_process = subprocess.Popen(
            [PYTHON, str(BOT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    return {"ok": True, "pid": bot_process.pid}


def stop_bot():
    global bot_process
    with bot_lock:
        if bot_process is None or bot_process.poll() is not None:
            return {"ok": False, "error": "not running"}
        bot_process.send_signal(signal.SIGINT)
        try:
            bot_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bot_process.kill()
        bot_process = None
    return {"ok": True}


# ── Trade analytics ───────────────────────────────────────────────────────────

def load_trades() -> list:
    if not TRADES.exists():
        return []
    try:
        return json.loads(TRADES.read_text())
    except Exception:
        return []


def resolve_trades(buys, sells):
    """Return (winners, losers) count from matched buy/sell pairs."""
    open_pos = {}
    winners = losers = 0
    for t in sorted(buys + sells, key=lambda x: x.get("ts", "")):
        key = f"{t['slug']}::{t['outcome']}".lower()
        if t["action"] == "BUY":
            open_pos[key] = open_pos.get(key, 0) + t.get("amount_usd", 0)
        elif t["action"] == "SELL":
            cost = open_pos.pop(key, None)
            if cost is not None:
                resp = t.get("response") or {}
                proceeds = 0
                if isinstance(resp, dict):
                    proceeds = resp.get("proceeds", 0) or resp.get("usdc_size", 0) or 0
                if proceeds > cost:
                    winners += 1
                elif proceeds > 0:
                    losers += 1
    return winners, losers, open_pos


def today_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def compute_stats(trades: list) -> dict:
    buys   = [t for t in trades if t["action"] == "BUY"  and t["status"] == "filled"]
    sells  = [t for t in trades if t["action"] == "SELL" and t["status"] == "filled"]
    failed = [t for t in trades if t["status"] in ("failed", "exception")]

    total_spent    = sum(t.get("amount_usd", 0) for t in buys)
    total_received = sum(
        (t.get("response") or {}).get("proceeds", 0) or (t.get("response") or {}).get("usdc_size", 0) or 0
        for t in sells if isinstance(t.get("response"), dict)
    )

    winners, losers, open_positions = resolve_trades(buys, sells)
    win_rate = (winners / (winners + losers) * 100) if (winners + losers) > 0 else None

    # Daily stats — trades timestamped today (UTC)
    today = today_prefix()
    d_buys  = [t for t in buys  if t.get("ts", "").startswith(today)]
    d_sells = [t for t in sells if t.get("ts", "").startswith(today)]
    d_winners, d_losers, _ = resolve_trades(d_buys, d_sells)
    daily_win_rate = (d_winners / (d_winners + d_losers) * 100) if (d_winners + d_losers) > 0 else None
    daily_pnl = (
        sum((t.get("response") or {}).get("proceeds", 0) or (t.get("response") or {}).get("usdc_size", 0) or 0
            for t in d_sells if isinstance(t.get("response"), dict))
        - sum(t.get("amount_usd", 0) for t in d_buys)
    )

    # ── Trades per trader ─────────────────────────────────────────────────────
    per_trader = {}
    for t in trades:
        label = t.get("copied_from") or "unknown"
        if label not in per_trader:
            per_trader[label] = {"buys": 0, "sells": 0, "skipped": 0, "failed": 0}
        action = t.get("action", "")
        status = t.get("status", "")
        if action == "BUY"          and status == "filled":   per_trader[label]["buys"]    += 1
        elif action == "SELL"       and status == "filled":   per_trader[label]["sells"]   += 1
        elif action == "SELL_SKIPPED":                        per_trader[label]["skipped"] += 1
        elif status in ("failed", "exception"):               per_trader[label]["failed"]  += 1

    # ── Trades over time (cumulative per day per trader) ──────────────────────
    from collections import defaultdict
    daily_counts: dict = defaultdict(lambda: defaultdict(int))
    for t in trades:
        if t.get("action") == "BUY" and t.get("status") == "filled":
            day   = (t.get("ts") or "")[:10]
            label = t.get("copied_from") or "unknown"
            if day:
                daily_counts[day][label] += 1

    all_days    = sorted(daily_counts.keys())
    all_labels  = sorted({t.get("copied_from") or "unknown" for t in trades if t.get("copied_from")})
    overtime = {
        "days":    all_days,
        "traders": {
            label: [daily_counts[d].get(label, 0) for d in all_days]
            for label in all_labels
        },
    }

    return {
        "executed":        len(buys) + len(sells),
        "buys":            len(buys),
        "sells":           len(sells),
        "failed":          len(failed),
        "open":            len(open_positions),
        "winners":         winners,
        "losers":          losers,
        "win_rate":        round(win_rate, 1) if win_rate is not None else None,
        "total_spent":     round(total_spent, 2),
        "total_received":  round(total_received, 2),
        "net_pnl":         round(total_received - total_spent, 2),
        "daily_win_rate":  round(daily_win_rate, 1) if daily_win_rate is not None else None,
        "daily_winners":   d_winners,
        "daily_losers":    d_losers,
        "daily_trades":    len(d_buys) + len(d_sells),
        "daily_pnl":       round(daily_pnl, 2),
        "per_trader":      per_trader,
        "overtime":        overtime,
    }


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # silence request logs

    def send_json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send_html(DASHBOARD_HTML)
        elif self.path == "/api/status":
            trades = load_trades()
            self.send_json(200, {
                "bot_running": bot_running(),
                "stats": compute_stats(trades),
                "trades": list(reversed(trades[-200:])),
            })
        elif self.path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            # Send current state immediately on connect
            try:
                trades = load_trades()
                payload = json.dumps({
                    "bot_running": bot_running(),
                    "stats":       compute_stats(trades),
                    "trades":      list(reversed(trades[-200:])),
                })
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
            except Exception:
                return
            # Register and keep connection alive
            with sse_lock:
                sse_clients.append(self.wfile)
            try:
                while True:
                    time.sleep(30)
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except Exception:
                with sse_lock:
                    if self.wfile in sse_clients:
                        sse_clients.remove(self.wfile)
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/bot/start":
            self.send_json(200, start_bot())
        elif self.path == "/api/bot/stop":
            self.send_json(200, stop_bot())
        else:
            self.send_json(404, {"error": "not found"})


# ── Dashboard HTML ────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Polymarket Copy Bot</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:       #0d0d0d;
    --surface:  #161616;
    --border:   #2a2a2a;
    --text:     #e8e8e8;
    --muted:    #666;
    --green:    #22c55e;
    --red:      #ef4444;
    --yellow:   #eab308;
    --blue:     #3b82f6;
    --font:     'SF Mono', 'Fira Mono', 'Cascadia Code', monospace;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: 13px;
    min-height: 100vh;
    padding: 24px;
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 28px;
  }

  h1 { font-size: 15px; font-weight: 600; letter-spacing: 0.05em; color: var(--text); }
  .subtitle { color: var(--muted); font-size: 11px; margin-top: 3px; }

  .header-right { display: flex; align-items: center; gap: 16px; }

  .refresh-hint { color: var(--muted); font-size: 11px; }

  /* Bot toggle */
  .bot-btn {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 18px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text);
    font-family: var(--font);
    font-size: 12px;
    cursor: pointer;
    transition: all 0.15s;
    letter-spacing: 0.03em;
  }
  .bot-btn:hover { border-color: #444; background: #1e1e1e; }
  .bot-btn.running { border-color: var(--green); color: var(--green); }
  .bot-btn.running:hover { background: #0f2a1a; }
  .dot {
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--muted);
    transition: background 0.2s;
  }
  .bot-btn.running .dot { background: var(--green); box-shadow: 0 0 6px var(--green); animation: pulse 1.8s infinite; }
  @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.4 } }

  /* Stats grid */
  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 28px;
  }

  .stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
  }

  .stat-label { color: var(--muted); font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 8px; }
  .stat-value { font-size: 24px; font-weight: 700; line-height: 1; }
  .stat-sub   { color: var(--muted); font-size: 10px; margin-top: 5px; }

  .green  { color: var(--green); }
  .red    { color: var(--red); }
  .yellow { color: var(--yellow); }
  .blue   { color: var(--blue); }
  .muted  { color: var(--muted); }

  /* Trade table */
  .section-label {
    color: var(--muted);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-bottom: 10px;
  }

  .table-wrap {
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }

  table { width: 100%; border-collapse: collapse; }

  thead th {
    background: var(--surface);
    color: var(--muted);
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    font-weight: 500;
  }

  tbody tr {
    border-bottom: 1px solid var(--border);
    transition: background 0.1s;
  }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover { background: #1a1a1a; }

  tbody td {
    padding: 10px 14px;
    font-size: 12px;
    vertical-align: middle;
  }

  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .badge-buy    { background: #0f2a1a; color: var(--green);  border: 1px solid #1a4a2a; }
  .badge-sell   { background: #2a0f0f; color: var(--red);    border: 1px solid #4a1a1a; }
  .badge-redeem { background: #1a1a2a; color: var(--blue);   border: 1px solid #2a2a4a; }
  .badge-skip   { background: #1a1a1a; color: var(--muted);  border: 1px solid var(--border); }
  .badge-ok     { background: #0f1f2a; color: var(--blue);   border: 1px solid #1a3a4a; }
  .badge-err    { background: #2a1a0f; color: var(--yellow); border: 1px solid #4a2a1a; }

  .slug-cell    { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .outcome-cell { color: var(--text); }
  .ts-cell      { color: var(--muted); font-size: 11px; white-space: nowrap; }
  .from-cell    { color: var(--muted); font-size: 11px; }

  .empty-state { padding: 40px; text-align: center; color: var(--muted); font-size: 12px; }

  /* Two-column row for charts */
  .charts-row {
    display: grid;
    grid-template-columns: 1fr 2fr;
    gap: 16px;
    margin-top: 28px;
  }

  .chart-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
  }

  .chart-title {
    color: var(--muted);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-bottom: 16px;
  }

  /* Trader bar rows */
  .trader-row { margin-bottom: 14px; }
  .trader-row:last-child { margin-bottom: 0; }
  .trader-name { color: var(--text); font-size: 11px; margin-bottom: 5px; display: flex; justify-content: space-between; }
  .trader-name span { color: var(--muted); }
  .bar-track { background: #222; border-radius: 3px; height: 6px; width: 100%; }
  .bar-fill  { height: 6px; border-radius: 3px; transition: width 0.4s ease; }

  /* Overtime chart (pure CSS bars grouped by day) */
  .ot-wrap { overflow-x: auto; }
  .ot-chart { display: flex; align-items: flex-end; gap: 10px; min-height: 100px; padding-bottom: 24px; position: relative; }
  .ot-day { display: flex; flex-direction: column; align-items: center; gap: 2px; flex: 1; min-width: 36px; }
  .ot-bars { display: flex; gap: 2px; align-items: flex-end; }
  .ot-bar  { width: 10px; border-radius: 2px 2px 0 0; transition: height 0.3s ease; }
  .ot-label { color: var(--muted); font-size: 9px; margin-top: 6px; white-space: nowrap; }
  .ot-legend { display: flex; gap: 14px; margin-top: 12px; flex-wrap: wrap; }
  .ot-legend-item { display: flex; align-items: center; gap: 5px; font-size: 10px; color: var(--muted); }
  .ot-legend-dot { width: 8px; height: 8px; border-radius: 2px; }
  .ot-empty { color: var(--muted); font-size: 12px; padding: 30px 0; text-align: center; }

  .error-banner {
    display: none;
    background: #2a0f0f;
    border: 1px solid #4a1a1a;
    border-radius: 6px;
    padding: 10px 14px;
    color: var(--red);
    font-size: 12px;
    margin-bottom: 16px;
  }
</style>
</head>
<body>

<header>
  <div>
    <h1>POLYMARKET COPY BOT</h1>
    <div class="subtitle" id="last-updated">loading…</div>
  </div>
  <div class="header-right">
    <span class="refresh-hint">⬤ live</span>
    <button class="bot-btn" id="bot-btn" onclick="toggleBot()">
      <span class="dot"></span>
      <span id="bot-label">—</span>
    </button>
  </div>
</header>

<div class="error-banner" id="error-banner"></div>

<div class="stats" id="stats-grid">
  <!-- populated by JS -->
</div>

<div class="section-label">Copied Trades</div>
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Action</th>
        <th>Market</th>
        <th>Outcome</th>
        <th>Amount</th>
        <th>Status</th>
        <th>Copied From</th>
      </tr>
    </thead>
    <tbody id="trades-tbody">
      <tr><td colspan="7" class="empty-state">waiting for data…</td></tr>
    </tbody>
  </table>
</div>

<div class="charts-row">
  <div class="chart-card">
    <div class="chart-title">Trades per Trader</div>
    <div id="per-trader-chart"><div class="ot-empty">no data yet</div></div>
  </div>
  <div class="chart-card">
    <div class="chart-title">Trades Over Time</div>
    <div class="ot-wrap">
      <div class="ot-chart" id="overtime-chart"></div>
      <div class="ot-legend" id="overtime-legend"></div>
    </div>
  </div>
</div>

<script>
let botRunning = false;

function fmt(v, decimals=2) {
  if (v === null || v === undefined) return '—';
  return Number(v).toFixed(decimals);
}

function fmtPnl(v) {
  if (v === null || v === undefined) return '<span class="muted">—</span>';
  const cls = v > 0 ? 'green' : v < 0 ? 'red' : 'muted';
  const sign = v > 0 ? '+' : '';
  return `<span class="${cls}">${sign}$${fmt(v)}</span>`;
}

function fmtWr(v) {
  if (v === null || v === undefined) return '<span class="muted">—</span>';
  const cls = v >= 50 ? 'green' : v >= 35 ? 'yellow' : 'red';
  return `<span class="${cls}">${fmt(v, 1)}%</span>`;
}

function renderStats(s, botOn) {
  const grid = document.getElementById('stats-grid');
  grid.innerHTML = `
    <div class="stat-card">
      <div class="stat-label">Bot Status</div>
      <div class="stat-value ${botOn ? 'green' : 'muted'}">${botOn ? 'LIVE' : 'OFF'}</div>
      <div class="stat-sub">${botOn ? 'watching 3 traders' : 'stopped'}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Net PnL</div>
      <div class="stat-value">${fmtPnl(s.net_pnl)}</div>
      <div class="stat-sub">$${fmt(s.total_spent)} spent · $${fmt(s.total_received)} received</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Win Rate (All Time)</div>
      <div class="stat-value">${fmtWr(s.win_rate)}</div>
      <div class="stat-sub">${s.winners}W · ${s.losers}L closed</div>
    </div>
    <div class="stat-card" style="border-color: #2a3a2a;">
      <div class="stat-label">Win Rate (Today)</div>
      <div class="stat-value">${fmtWr(s.daily_win_rate)}</div>
      <div class="stat-sub">${s.daily_winners}W · ${s.daily_losers}L · ${s.daily_trades} trades · ${fmtPnl(s.daily_pnl)}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Trades Executed</div>
      <div class="stat-value blue">${s.executed}</div>
      <div class="stat-sub">${s.buys} buys · ${s.sells} sells</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Open Positions</div>
      <div class="stat-value yellow">${s.open}</div>
      <div class="stat-sub">unrealized</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Failed</div>
      <div class="stat-value ${s.failed > 0 ? 'red' : 'muted'}">${s.failed}</div>
      <div class="stat-sub">errors logged</div>
    </div>
  `;
}

function actionBadge(t) {
  if (t.action === 'BUY')          return '<span class="badge badge-buy">BUY</span>';
  if (t.action === 'SELL')         return '<span class="badge badge-sell">SELL</span>';
  if (t.action === 'REDEEM')       return '<span class="badge badge-redeem">REDEEM</span>';
  if (t.action === 'SELL_SKIPPED') return '<span class="badge badge-skip">SKIP</span>';
  return `<span class="badge badge-skip">${t.action}</span>`;
}

function statusBadge(t) {
  if (t.status === 'filled')      return '<span class="badge badge-ok">filled</span>';
  if (t.status === 'no_position') return '<span class="badge badge-skip">no pos</span>';
  if (t.status === 'failed')      return '<span class="badge badge-err">failed</span>';
  if (t.status === 'exception')   return '<span class="badge badge-err">error</span>';
  return `<span class="badge badge-skip">${t.status || '—'}</span>`;
}

function fmtTs(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) +
         ' ' + d.toLocaleDateString([], {month:'short', day:'numeric'});
}

function renderTrades(trades) {
  const tbody = document.getElementById('trades-tbody');
  if (!trades || trades.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">no trades yet — start the bot to begin copying</td></tr>';
    return;
  }
  tbody.innerHTML = trades.map(t => `
    <tr>
      <td class="ts-cell">${fmtTs(t.ts)}</td>
      <td>${actionBadge(t)}</td>
      <td class="slug-cell" title="${t.slug || ''}">${t.slug || '—'}</td>
      <td class="outcome-cell">${t.outcome || '—'}</td>
      <td>${t.amount_usd != null ? '$'+fmt(t.amount_usd) : '—'}</td>
      <td>${statusBadge(t)}</td>
      <td class="from-cell">${(t.copied_from || '—').replace(/_/g, ' ')}</td>
    </tr>
  `).join('');
}

function updateBotBtn(running) {
  const btn = document.getElementById('bot-btn');
  const lbl = document.getElementById('bot-label');
  botRunning = running;
  if (running) {
    btn.classList.add('running');
    lbl.textContent = 'BOT RUNNING';
  } else {
    btn.classList.remove('running');
    lbl.textContent = 'START BOT';
  }
}

const TRADER_COLORS = ['#3b82f6', '#22c55e', '#eab308', '#a78bfa', '#f97316'];

function shortLabel(label) {
  return label.replace(/_/g, ' ').replace(/^(0x[a-f0-9]{4})[a-f0-9]+/i, '$1…');
}

function renderPerTrader(perTrader) {
  const el = document.getElementById('per-trader-chart');
  const entries = Object.entries(perTrader);
  if (!entries.length) { el.innerHTML = '<div class="ot-empty">no data yet</div>'; return; }

  const maxTotal = Math.max(...entries.map(([,v]) => v.buys + v.sells));

  el.innerHTML = entries.map(([label, v], i) => {
    const total = v.buys + v.sells;
    const pct   = maxTotal > 0 ? (total / maxTotal * 100) : 0;
    const color = TRADER_COLORS[i % TRADER_COLORS.length];
    return `
      <div class="trader-row">
        <div class="trader-name">${shortLabel(label)} <span>${total} trades (${v.buys}B · ${v.sells}S · ${v.failed}F)</span></div>
        <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
      </div>`;
  }).join('');
}

function renderOvertime(overtime) {
  const chart  = document.getElementById('overtime-chart');
  const legend = document.getElementById('overtime-legend');
  const days    = overtime.days || [];
  const traders = overtime.traders || {};
  const labels  = Object.keys(traders);

  if (!days.length) {
    chart.innerHTML = '<div class="ot-empty">no data yet — trades will appear here once the bot runs</div>';
    legend.innerHTML = '';
    return;
  }

  const allVals = days.flatMap((_,di) => labels.map(l => (traders[l] || [])[di] || 0));
  const maxVal  = Math.max(...allVals, 1);
  const maxPx   = 90;

  chart.innerHTML = days.map((day, di) => {
    const bars = labels.map((label, li) => {
      const v   = (traders[label] || [])[di] || 0;
      const h   = Math.max(v > 0 ? 4 : 0, Math.round(v / maxVal * maxPx));
      const col = TRADER_COLORS[li % TRADER_COLORS.length];
      return `<div class="ot-bar" style="height:${h}px;background:${col}" title="${shortLabel(label)}: ${v}"></div>`;
    }).join('');
    const d = day.slice(5); // MM-DD
    return `<div class="ot-day"><div class="ot-bars">${bars}</div><div class="ot-label">${d}</div></div>`;
  }).join('');

  legend.innerHTML = labels.map((label, i) =>
    `<div class="ot-legend-item"><div class="ot-legend-dot" style="background:${TRADER_COLORS[i % TRADER_COLORS.length]}"></div>${shortLabel(label)}</div>`
  ).join('');
}

function applyUpdate(d) {
  document.getElementById('error-banner').style.display = 'none';
  renderStats(d.stats, d.bot_running);
  renderTrades(d.trades);
  renderPerTrader(d.stats.per_trader || {});
  renderOvertime(d.stats.overtime || {});
  updateBotBtn(d.bot_running);
  document.getElementById('last-updated').textContent =
    'live · ' + new Date().toLocaleTimeString();
}

const API = 'http://localhost:7373';

function connectSSE() {
  const es = new EventSource(API + '/api/stream');
  es.onmessage = e => {
    try { applyUpdate(JSON.parse(e.data)); } catch(_) {}
  };
  es.onerror = () => {
    const b = document.getElementById('error-banner');
    b.style.display = 'block';
    b.textContent = 'Connection lost — reconnecting…';
    es.close();
    setTimeout(connectSSE, 3000);
  };
}

async function toggleBot() {
  const endpoint = botRunning ? '/api/bot/stop' : '/api/bot/start';
  try { await fetch(API + endpoint, { method: 'POST' }); } catch(e) { console.error(e); }
}

connectSSE();
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = ThreadedHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Dashboard running at {url}")
    print("Press Ctrl+C to stop.\n")
    # Start file watcher for real-time SSE push
    t = threading.Thread(target=watch_trades, daemon=True)
    t.start()
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        stop_bot()


if __name__ == "__main__":
    main()
