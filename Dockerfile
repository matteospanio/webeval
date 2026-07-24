# syntax=docker/dockerfile:1
# Production image for PANEL. Build: docker build -t panel .
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DJANGO_SETTINGS_MODULE=core.settings \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# uv (pinned image) provides fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# Runtime libs: libpq for Postgres (psycopg). Build tools aren't needed —
# every locked dependency ships a wheel.
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for layer caching: project + production group,
# without the dev group. --frozen uses the committed uv.lock as-is.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group production

# Application code.
COPY . .

# Collect static files into STATIC_ROOT (served by WhiteNoise at runtime).
RUN SECRET_KEY=build-only USE_WHITENOISE=True \
    python manage.py collectstatic --noinput

EXPOSE 8000

# Apply migrations, then serve via gunicorn. Override the command to add a
# worker count, run a one-off management command, etc.
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn core.wsgi:application --bind 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-3} --timeout 120"]
