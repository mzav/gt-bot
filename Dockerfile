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
# Restore from B2 replica if one exists, then start the bot under litestream
# so replication runs continuously in the background.
RUN mkdir -p /data

CMD litestream restore -if-replica-exists -config /app/litestream.yml /data/gtbot.db \
 && litestream replicate -config /app/litestream.yml -exec "python main.py"
