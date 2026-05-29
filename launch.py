import os
import subprocess
import sys

# Install bullpen
os.system("curl -fsSL https://cli.bullpen.fi/install.sh | sh")

# Set path
bullpen_path = os.path.expanduser("~/.bullpen/bin")
os.environ["PATH"] = bullpen_path + ":" + os.environ.get("PATH", "")

# Restore credentials
os.makedirs(os.path.expanduser("~/.bullpen/keys"), exist_ok=True)

import base64
creds = {
    "~/.bullpen/credentials.json.enc":           "BULLPEN_CREDENTIALS_ENC",
    "~/.bullpen/keys/wallet_signing_key.json.enc": "BULLPEN_SIGNING_KEY",
    "~/.bullpen/keys/turnkey_p256.json.enc":      "BULLPEN_P256_KEY",
    "~/.bullpen/credential_salt.bin":             "BULLPEN_SALT",
}
for path, env_var in creds.items():
    val = os.environ.get(env_var, "")
    if val:
        with open(os.path.expanduser(path), "wb") as f:
            f.write(base64.b64decode(val))

config = """env = "production"
usergate_url = "https://usergate.bullpen.fi"
output_format = "table"
credential_store = "auto"
"""
with open(os.path.expanduser("~/.bullpen/config.toml"), "w") as f:
    f.write(config)

print("Credentials restored. Starting bot...")
os.system("bullpen status")

# Run the bot
os.execv(sys.executable, [sys.executable, "copybot.py"])
