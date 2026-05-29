#!/bin/bash
set -e

export PATH="$HOME/.bullpen/bin:$PATH"

# Install bullpen CLI if not present
if ! command -v bullpen &> /dev/null; then
    echo "Installing bullpen CLI..."
    curl -fsSL https://cli.bullpen.fi/install.sh | sh
    export PATH="$HOME/.bullpen/bin:$PATH"
fi

# Restore encrypted credentials from environment variables
mkdir -p ~/.bullpen/keys

if [ ! -z "$BULLPEN_CREDENTIALS_ENC" ]; then
    echo "$BULLPEN_CREDENTIALS_ENC" | base64 -d > ~/.bullpen/credentials.json.enc
fi

if [ ! -z "$BULLPEN_SIGNING_KEY" ]; then
    echo "$BULLPEN_SIGNING_KEY" | base64 -d > ~/.bullpen/keys/wallet_signing_key.json.enc
fi

if [ ! -z "$BULLPEN_P256_KEY" ]; then
    echo "$BULLPEN_P256_KEY" | base64 -d > ~/.bullpen/keys/turnkey_p256.json.enc
fi

if [ ! -z "$BULLPEN_SALT" ]; then
    echo "$BULLPEN_SALT" | base64 -d > ~/.bullpen/credential_salt.bin
fi

# Write config
cat > ~/.bullpen/config.toml << 'TOML'
env = "production"
usergate_url = "https://usergate.bullpen.fi"
output_format = "table"
credential_store = "auto"
TOML

echo "Bullpen credentials restored."
bullpen status 2>&1 | head -5

echo "Starting copy bot..."
python3 copybot.py
