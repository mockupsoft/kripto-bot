FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Install dependencies only (non-editable) to cache this layer
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.32.0" \
    "sqlalchemy[asyncio]>=2.0.36" \
    "asyncpg>=0.30.0" \
    "psycopg2-binary>=2.9.10" \
    "alembic>=1.14.0" \
    "redis>=5.2.0" \
    "arq>=0.26.1" \
    "httpx>=0.28.0" \
    "websockets>=14.1" \
    "pydantic>=2.10.0" \
    "pydantic-settings>=2.7.0" \
    "numpy>=2.1.0" \
    "scipy>=1.14.0" \
    "pandas>=2.2.0" \
    "structlog>=24.4.0" \
    "orjson>=3.10.0" \
    "pytest>=8.3.0" \
    "pytest-asyncio>=0.24.0" \
    "pytest-cov>=6.0.0"

COPY . .

# Add /app to PYTHONPATH so `app` package is importable without editable install
ENV PYTHONPATH=/app

EXPOSE 8000
