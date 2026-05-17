FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.12-slim

# Copy Litestream binary from official image
COPY --from=litestream/litestream:latest /usr/local/bin/litestream /usr/local/bin/litestream

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY . .

# Non-root user for least-privilege execution
RUN useradd --no-create-home --shell /bin/false botuser \
    && mkdir -p /data \
    && chown botuser:botuser /data

USER botuser

# /data is the Railway persistent volume mount point.
# Litestream replicates /data/gtbot.db to B2 in the background,
# then the bot process starts in the foreground.
CMD litestream replicate -config /app/litestream.yml & \
    sleep 2 && \
    python main.py
