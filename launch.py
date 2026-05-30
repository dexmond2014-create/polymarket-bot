#!/usr/bin/env python3
import os
import sys
import base64
import subprocess
import time
from pathlib import Path

print("=== Polymarket Bot Launcher ===")

# Step 1: Find bullpen binary (installed at Docker build time)
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

if not bullpen_bin:
    print("Bullpen not found — installing...")
    os.system("curl -fsSL https://cli.bullpen.fi/install.sh | sh 2>&1")
    time.sleep(3)
    for p in bullpen_paths:
        if os.path.exists(p):
            bullpen_bin = p
            break

if not bullpen_bin:
    print("ERROR: bullpen not found!")
    sys.exit(1)

bin_dir = str(Path(bullpen_bin).parent)
os.environ["PATH"] = bin_dir + ":" + os.environ.get("PATH", "")
os.environ["BULLPEN_BIN"] = bullpen_bin

# Step 2: Restore credentials from env vars
print("Restoring credentials...")
home = Path.home()
(home / ".bullpen" / "keys").mkdir(parents=True, exist_ok=True)

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
        print(f"  Restored: {path.name}")

# Write config
(home / ".bullpen" / "config.toml").write_text(
    'env = "production"\n'
    'usergate_url = "https://usergate.bullpen.fi"\n'
    'output_format = "table"\n'
    'credential_store = "auto"\n'
)

# Step 3: Test if credentials work
print("Testing bullpen auth...")
result = subprocess.run(
    [bullpen_bin, "polymarket", "positions", "--output", "json"],
    capture_output=True, text=True, timeout=30
)

if result.returncode == 0:
    print("✅ Bullpen authenticated OK — starting bot!")
else:
    err = result.stderr + result.stdout
    print(f"Auth failed: {err[:300]}")

    # Check if it's a machine-lock / decrypt error
    if "decrypt" in err.lower() or "turnkey" in err.lower() or "machine" in err.lower() or "drifted" in err.lower():
        print()
        print("=" * 60)
        print("MACHINE KEY ERROR — need to re-login on this server.")
        print("Starting interactive login...")
        print("=" * 60)

        # Remove bad keys so login can generate fresh ones
        bad_key = home / ".bullpen" / "keys" / "turnkey_p256.json.enc"
        if bad_key.exists():
            bad_key.unlink()
            print("Removed old machine-locked key.")

        # Run bullpen login — this prints a magic link URL to stdout
        print("Open the link below in your browser to authenticate:")
        print("-" * 60)
        login = subprocess.run(
            [bullpen_bin, "login"],
            timeout=300  # 5 minutes to click the link
        )
        print("-" * 60)

        if login.returncode == 0:
            print("✅ Login successful!")
            # Export new keys so user can save them as env vars
            print()
            print("=" * 60)
            print("SAVE THESE AS RENDER ENV VARS to avoid re-login next time:")
            print("=" * 60)
            for path, env_var in cred_files.items():
                if path.exists():
                    encoded = base64.b64encode(path.read_bytes()).decode()
                    print(f"\n{env_var}=\n{encoded[:80]}...")
            print("=" * 60)
        else:
            print("Login failed or timed out. Bot cannot start.")
            sys.exit(1)
    else:
        print("Unknown auth error — attempting to start anyway...")

# Step 4: Run copybot with auto-restart
print("Starting copybot...")
os.chdir(Path(__file__).parent)

while True:
    result = subprocess.run([sys.executable, "copybot.py"])
    print(f"Copybot exited with code {result.returncode} — restarting in 10s...")
    time.sleep(10)
