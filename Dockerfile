# syntax=docker/dockerfile:1

FROM python:3.13-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim-bookworm AS runtime

RUN groupadd --system --gid 1000 skillhub \
    && useradd --system --uid 1000 --gid skillhub --home /app \
        --shell /usr/sbin/nologin skillhub

WORKDIR /app

COPY --from=builder --chown=skillhub:skillhub /app/.venv /app/.venv
COPY --from=builder --chown=skillhub:skillhub /app/skills /app/skills
COPY --from=builder --chown=skillhub:skillhub /app/config /app/config

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER skillhub

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "skillhub.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
