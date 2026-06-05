# ──────────────────────────────────────────────────────────────────────────
# Python Engine — Dockerfile
# Multi-stage: builder installs deps, final image is lean
# ──────────────────────────────────────────────────────────────────────────

# Stage 1: builder — install all Python dependencies into a clean prefix
FROM python:3.11-slim AS builder

WORKDIR /install

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install/pkg -r requirements.txt



# Stage 2: final image — copy only what is needed
FROM python:3.11-slim

WORKDIR /engine

# Copy installed packages from builder
COPY --from=builder /install/pkg /usr/local

# Copy application source
COPY app/ ./app/

# Create a non-root user for security
RUN adduser --disabled-password --gecos "" engineuser \
    && chown -R engineuser:engineuser /engine
USER engineuser

# Expose FastAPI port
EXPOSE 8001

# Health check — hits the root endpoint every 30s
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/')" || exit 1

# Start uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
