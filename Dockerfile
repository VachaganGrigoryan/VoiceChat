# ---- base ----
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

# System deps (build + runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 - \
    && ln -s /root/.local/bin/poetry /usr/local/bin/poetry

# Copy only dependency files first (better caching)
COPY pyproject.toml poetry.lock* /app/

# Install deps
RUN poetry install --no-interaction --no-ansi

# Copy application code
COPY . /app

# Create uploads dir (local storage mode)
RUN mkdir -p /app/uploads

EXPOSE 8000

# Default command
CMD ["uvicorn", "app.main:app", "--host=0.0.0.0", "--port=8000"]