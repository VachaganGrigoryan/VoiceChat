# syntax=docker/dockerfile:1.7

# =========================
# Base image
# =========================
FROM python:3.12-slim AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_CREATE=false \
    PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Common system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://install.python-poetry.org | python3 -

# =========================
# Builder
# =========================
FROM python-base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock* /app/

# Install all deps into system site-packages
RUN poetry install --no-interaction --no-ansi

COPY . /app

# =========================
# Production
# =========================
FROM python-base AS production

ENV APP_ENV=production

# Create non-root user
RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app

# Copy installed python packages and app
COPY --from=builder /usr/local /usr/local
COPY --from=builder /root/.local /root/.local
COPY --from=builder /app /app

RUN mkdir -p /app/uploads && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:asgi_app", "--host", "0.0.0.0", "--port", "8000"]

# =========================
# Development
# =========================
FROM builder AS development

ENV APP_ENV=development

RUN mkdir -p /app/uploads

EXPOSE 8000

CMD ["uvicorn", "app.main:asgi_app", "--host", "0.0.0.0", "--port", "8000", "--reload"]