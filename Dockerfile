# ── Stage 1: builder ──────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# ── Stage 2: final ────────────────────────────────────────────────────────
FROM python:3.11-slim

# Non-root user
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /home/appuser/.local

# Copy application source
COPY --chown=appuser:appuser . .

# Persistent data directory
RUN mkdir -p /app/data && chown appuser:appuser /app/data

USER appuser

ENV PYTHONPATH=/app \
    DB_PATH=/app/data/reports.db \
    PATH=/home/appuser/.local/bin:$PATH

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health').raise_for_status()"

CMD ["uvicorn", "app.api.server:app", "--host", "0.0.0.0", "--port", "8080"]
