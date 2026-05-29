FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y curl bash && rm -rf /var/lib/apt/lists/*

# Copy bot files
COPY . .

# Install bullpen CLI at build time (cached in image — no network needed at runtime)
RUN curl -fsSL https://cli.bullpen.fi/install.sh | bash && \
    /root/.bullpen/bin/bullpen --version && \
    echo "Bullpen installed OK"

# Ensure bullpen is on PATH
ENV PATH="/root/.bullpen/bin:${PATH}"
ENV BULLPEN_BIN="/root/.bullpen/bin/bullpen"

CMD ["python3", "launch.py"]
