FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.12-slim

COPY --from=litestream/litestream:latest /usr/local/bin/litestream /usr/local/bin/litestream

WORKDIR /app

COPY --from=builder /install /usr/local
COPY . .

# /data is mounted as a Railway persistent volume at runtime.
# Litestream replicates /data/gtbot.db to B2 in the background,
# then the bot starts in the foreground.
CMD litestream replicate -config /app/litestream.yml & \
    sleep 2 && \
    python main.py
