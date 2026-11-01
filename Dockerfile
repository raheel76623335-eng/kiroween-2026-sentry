# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

# --- Environment -------------------------------------------------------------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    APP_ENV=production

WORKDIR /app

# --- System dependencies (curl is needed for HEALTHCHECK) --------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# --- Python dependencies (cached layer) --------------------------------------
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# --- Application code --------------------------------------------------------
COPY . .

# --- Non-root runtime user ---------------------------------------------------
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# --- Container healthcheck ---------------------------------------------------
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# --- Entrypoint --------------------------------------------------------------
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
