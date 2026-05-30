FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl bash && rm -rf /var/lib/apt/lists/*

COPY . .

RUN curl -fsSL https://cli.bullpen.fi/install.sh | sh

ENV PATH="/root/.bullpen/bin:${PATH}"
ENV BULLPEN_BIN="/root/.bullpen/bin/bullpen"
ENV PYTHONUNBUFFERED=1

CMD ["python3", "-u", "launch.py"]
