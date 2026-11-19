# The demo web app (FastAPI). Data, readings cache and uploads live on a host mount at /data.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS build
COPY --from=ghcr.io/astral-sh/uv@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY wells.py fields.py checks.py read.py pipeline.py invoice.py world.py score.py ./
COPY app ./app
ENV PATH=/app/.venv/bin:$PATH TICKETS_DATA=/data PYTHONUNBUFFERED=1
RUN useradd -u 10001 -m web && mkdir -p /data && chown web /data
USER web
EXPOSE 8220
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8220", "--proxy-headers"]
