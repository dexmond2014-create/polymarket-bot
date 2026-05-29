#!/usr/bin/env python3
import os
import sys
import base64
import subprocess
import time
from pathlib import Path

print("=== Polymarket Bot Launcher ===")

# Step 1: Find bullpen (Docker already installs it at build time)
bullpen_paths = [
    "/root/.bullpen/bin/bullpen",
    os.path.expanduser("~/.bullpen/bin/bullpen"),
    "/home/render/.bullpen/bin/bullpen",
]
bullpen_bin = None
for p in bullpen_paths:
    if os.path.exists(p):
        bullpen_bin = p
        print(f"Found bullpen at: {p}")
        break

# If not found from Docker build, try installing now
if not bullpen_bin:
    print("Bullpen not found — installing...")
    ret = os.system("curl -fsSL https://cli.bullpen.fi/install.sh | sh 2>&1")
    time.sleep(3)
    for p in bullpen_paths:
        if os.path.exists(p):
            bullpen_bin = p
            print(f"Found bullpen at: {p}")
            break

if not bullpen_bin:
    # Last resort: search
    try:
        result = subprocess.run(["find", "/root", "/home", "-name", "bullpen", "-type", "f"],
                                capture_output=True, text=True, timeout=15)
        for line in result.stdout.strip().split("\n"):
            if line.strip() and not line.endswith(".sh"):
                bullpen_bin = line.strip()
                print(f"Found bullpen at: {bullpen_bin}")
                break
    except Exception as e:
        print(f"Search failed: {e}")

if not bullpen_bin:
    print("ERROR: bullpen not found!")
    sys.exit(1)

# Add to PATH
bin_dir = str(Path(bullpen_bin).parent)
os.environ["PATH"] = bin_dir + ":" + os.environ.get("PATH", "")
os.environ["BULLPEN_BIN"] = bullpen_bin

# Step 2: Restore credentials
print("Restoring credentials...")
home = Path.home()
(home / ".bullpen" / "keys").mkdir(parents=True, exist_ok=True)

cred_files = {
    home / ".bullpen" / "credentials.json.enc":                  "BULLPEN_CREDENTIALS_ENC",
    home / ".bullpen" / "keys" / "wallet_signing_key.json.enc":  "BULLPEN_SIGNING_KEY",
    home / ".bullpen" / "keys" / "turnkey_p256.json.enc":        "BULLPEN_P256_KEY",
    home / ".bullpen" / "credential_salt.bin":                   "BULLPEN_SALT",
}

missing = []
for path, env_var in cred_files.items():
    val = os.environ.get(env_var, "")
    if val:
        path.write_bytes(base64.b64decode(val))
        print(f"  Restored: {path.name}")
    else:
        print(f"  WARNING: {env_var} not set!")
        missing.append(env_var)

# Write config
(home / ".bullpen" / "config.toml").write_text(
    'env = "production"\n'
    'usergate_url = "https://usergate.bullpen.fi"\n'
    'output_format = "table"\n'
    'credential_store = "auto"\n'
)

if missing:
    print(f"WARNING: {len(missing)} credential(s) missing — bot may fail to authenticate")

# Step 3: Test bullpen
print("Testing bullpen connection...")
result = subprocess.run([bullpen_bin, "status"], capture_output=True, text=True, timeout=30)
if result.returncode == 0:
    print("Bullpen OK:", result.stdout[:200].strip())
else:
    print("Bullpen status failed (may still work):", result.stderr[:200].strip())

# Step 4: Run copybot — restart it automatically if it ever crashes
print(f"Starting copybot...")
os.chdir(Path(__file__).parent)

while True:
    result = subprocess.run([sys.executable, "copybot.py"])
    print(f"Copybot exited with code {result.returncode} — restarting in 10s...")
    time.sleep(10)
