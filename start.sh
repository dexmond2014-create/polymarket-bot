#!/bin/bash
set -e

export HOME=/root
export PATH="$HOME/.bullpen/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# Install bullpen CLI
echo "Installing bullpen CLI..."
curl -fsSL https://cli.bullpen.fi/install.sh | sh
export PATH="$HOME/.bullpen/bin:$PATH"

# Restore credentials
mkdir -p ~/.bullpen/keys

echo "$BULLPEN_CREDENTIALS_ENC" | base64 -d > ~/.bullpen/credentials.json.enc
echo "$BULLPEN_SIGNING_KEY"     | base64 -d > ~/.bullpen/keys/wallet_signing_key.json.enc
echo "$BULLPEN_P256_KEY"        | base64 -d > ~/.bullpen/keys/turnkey_p256.json.enc
echo "$BULLPEN_SALT"            | base64 -d > ~/.bullpen/credential_salt.bin

cat > ~/.bullpen/config.toml << 'TOML'
env = "production"
usergate_url = "https://usergate.bullpen.fi"
output_format = "table"
credential_store = "auto"
TOML

echo "Checking bullpen status..."
bullpen status 2>&1 || true

echo "Starting bot..."
python3 copybot.py
