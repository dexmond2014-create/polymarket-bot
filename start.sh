#!/bin/bash
set -e

# Install bullpen CLI if not present
if ! command -v bullpen &> /dev/null; then
    echo "Installing bullpen CLI..."
    curl -fsSL https://cli.bullpen.fi/install.sh | sh
    export PATH="$HOME/.bullpen/bin:$PATH"
fi

export PATH="$HOME/.bullpen/bin:$PATH"

# Copy credentials if they exist as env vars
if [ ! -z "$BULLPEN_CREDENTIALS" ]; then
    mkdir -p ~/.bullpen
    echo "$BULLPEN_CREDENTIALS" > ~/.bullpen/credentials.json
fi

if [ ! -z "$BULLPEN_SIGNING_KEY" ]; then
    mkdir -p ~/.bullpen/keys
    echo "$BULLPEN_SIGNING_KEY" > ~/.bullpen/keys/wallet_signing_key.json.enc
fi

python3 copybot.py
