FROM python:3.11-slim

WORKDIR /app

# Install curl for bullpen install
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Copy bot files
COPY . .

# Install bullpen CLI
RUN curl -fsSL https://cli.bullpen.fi/install.sh | sh

ENV PATH="/root/.bullpen/bin:$PATH"

CMD ["python3", "launch.py"]
