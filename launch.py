#!/usr/bin/env python3
import os
import sys
import base64
import subprocess
import time
from pathlib import Path

print("=== Polymarket Bot Launcher ===", flush=True)

# Step 1: Find bullpen binary
bullpen_paths = [
    "/root/.bullpen/bin/bullpen",
    os.path.expanduser("~/.bullpen/bin/bullpen"),
    "/home/render/.bullpen/bin/bullpen",
]
bullpen_bin = None
for p in bullpen_paths:
    if os.path.exists(p):
        bullpen_bin = p
        print(f"Found bullpen at: {p}", flush=True)
        break

if not bullpen_bin:
    print("Bullpen not found — installing...", flush=True)
    os.system("curl -fsSL https://cli.bullpen.fi/install.sh | sh")
    time.sleep(3)
    for p in bullpen_paths:
        if os.path.exists(p):
            bullpen_bin = p
            break

if not bullpen_bin:
    print("ERROR: bullpen not found!", flush=True)
    sys.exit(1)

bin_dir = str(Path(bullpen_bin).parent)
os.environ["PATH"] = bin_dir + ":" + os.environ.get("PATH", "")
os.environ["BULLPEN_BIN"] = bullpen_bin

# Step 2: Write config
home = Path.home()
(home / ".bullpen" / "keys").mkdir(parents=True, exist_ok=True)
(home / ".bullpen" / "config.toml").write_text(
    'env = "production"\n'
    'usergate_url = "https://usergate.bullpen.fi"\n'
    'output_format = "table"\n'
    'credential_store = "auto"\n'
)

# Step 3: Restore credentials from env vars
print("Restoring credentials...", flush=True)
cred_files = {
    home / ".bullpen" / "credentials.json.enc":                  "BULLPEN_CREDENTIALS_ENC",
    home / ".bullpen" / "keys" / "wallet_signing_key.json.enc":  "BULLPEN_SIGNING_KEY",
    home / ".bullpen" / "keys" / "turnkey_p256.json.enc":        "BULLPEN_P256_KEY",
    home / ".bullpen" / "credential_salt.bin":                   "BULLPEN_SALT",
}
for path, env_var in cred_files.items():
    val = os.environ.get(env_var, "")
    if val:
        path.write_bytes(base64.b64decode(val))
        print(f"  Restored: {path.name}", flush=True)

# Step 4: ALWAYS delete the Mac-locked P256 key and re-login on Linux
p256_key = home / ".bullpen" / "keys" / "turnkey_p256.json.enc"
if p256_key.exists():
    p256_key.unlink()
    print("Removed Mac-locked P256 key — need fresh login for this server.", flush=True)

print("", flush=True)
print("=" * 60, flush=True)
print("OPEN THIS LINK IN YOUR BROWSER TO AUTHENTICATE:", flush=True)
print("=" * 60, flush=True)

login = subprocess.run([bullpen_bin, "login"])

if login.returncode != 0:
    print("Login failed. Retrying in 30s...", flush=True)
    time.sleep(30)
    login = subprocess.run([bullpen_bin, "login"])

if login.returncode != 0:
    print("Login failed twice. Check Bullpen credentials.", flush=True)
    sys.exit(1)

print("Login successful!", flush=True)

# Step 5: Save new keys as base64 (print to logs so you can update env vars)
print("", flush=True)
print("=" * 60, flush=True)
print("UPDATE THESE RENDER ENV VARS TO AVOID RE-LOGIN NEXT DEPLOY:", flush=True)
for path, env_var in cred_files.items():
    if path.exists():
        encoded = base64.b64encode(path.read_bytes()).decode()
        print(f"{env_var}={encoded}", flush=True)
print("=" * 60, flush=True)

# Step 6: Run copybot with auto-restart
print("Starting copybot...", flush=True)
os.chdir(Path(__file__).parent)

while True:
    result = subprocess.run([sys.executable, "-u", "copybot.py"])
    print(f"Copybot exited with code {result.returncode} — restarting in 10s...", flush=True)
    time.sleep(10)
