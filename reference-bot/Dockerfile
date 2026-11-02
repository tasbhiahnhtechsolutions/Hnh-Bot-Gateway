FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 bot
USER bot

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 9000
# BOT_SECRET must be provided at run time (never baked into the image).
CMD ["python", "-m", "app"]
