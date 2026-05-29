#!/usr/bin/env python3
import os
import sys
import base64
import subprocess
from pathlib import Path

print("=== Polymarket Bot Launcher ===")

# Step 1: Install bullpen
print("Installing bullpen CLI...")
os.system("curl -fsSL https://cli.bullpen.fi/install.sh | sh 2>&1")

# Step 2: Find bullpen binary
bullpen_paths = [
    os.path.expanduser("~/.bullpen/bin/bullpen"),
    "/root/.bullpen/bin/bullpen",
    "/home/render/.bullpen/bin/bullpen",
]
bullpen_bin = None
for p in bullpen_paths:
    if os.path.exists(p):
        bullpen_bin = p
        print(f"Found bullpen at: {p}")
        break

if not bullpen_bin:
    # Search for it
    result = subprocess.run(["find", "/", "-name", "bullpen", "-type", "f"], 
                          capture_output=True, text=True, timeout=10)
    for line in result.stdout.strip().split("\n"):
        if line and "bullpen" in line and not line.endswith(".sh"):
            bullpen_bin = line.strip()
            print(f"Found bullpen at: {bullpen_bin}")
            break

if not bullpen_bin:
    print("ERROR: bullpen not found after install!")
    sys.exit(1)

# Add to PATH
bin_dir = str(Path(bullpen_bin).parent)
os.environ["PATH"] = bin_dir + ":" + os.environ.get("PATH", "")

# Step 3: Restore credentials
print("Restoring credentials...")
home = Path.home()
(home / ".bullpen" / "keys").mkdir(parents=True, exist_ok=True)

files = {
    home / ".bullpen" / "credentials.json.enc":             "BULLPEN_CREDENTIALS_ENC",
    home / ".bullpen" / "keys" / "wallet_signing_key.json.enc": "BULLPEN_SIGNING_KEY",
    home / ".bullpen" / "keys" / "turnkey_p256.json.enc":    "BULLPEN_P256_KEY",
    home / ".bullpen" / "credential_salt.bin":               "BULLPEN_SALT",
}

for path, env_var in files.items():
    val = os.environ.get(env_var, "")
    if val:
        path.write_bytes(base64.b64decode(val))
        print(f"Restored: {path.name}")
    else:
        print(f"WARNING: {env_var} not set!")

# Write config
config = '''env = "production"
usergate_url = "https://usergate.bullpen.fi"
output_format = "table"
credential_store = "auto"
'''
(home / ".bullpen" / "config.toml").write_text(config)

# Step 4: Test bullpen
print("Testing bullpen...")
result = subprocess.run([bullpen_bin, "status"], capture_output=True, text=True)
print(result.stdout[:500])
if result.returncode != 0:
    print("STDERR:", result.stderr[:300])

# Step 5: Patch copybot to use full bullpen path
print(f"Starting bot with bullpen at: {bullpen_bin}")
os.environ["BULLPEN_BIN"] = bullpen_bin

# Run copybot
os.chdir(Path(__file__).parent)
os.execv(sys.executable, [sys.executable, "copybot.py"])
