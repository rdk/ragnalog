# atlas-log with the web app: `docker run -p 8321:8321 -v atlas-log:/data -e ATLAS_LOG_TOKEN=... IMAGE`
# Any other command: `docker run --rm -v atlas-log:/data IMAGE groups SET --brief`.
# The build stage and the runtime share one Python (python:3.14-slim-bookworm) and the
# virtual environment's path (/app/.venv: its scripts name their interpreter by it), so
# the copied environment runs as built.

FROM ghcr.io/astral-sh/uv:0.9-python3.14-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --extra web --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --extra web --no-editable

FROM python:3.14-slim-bookworm
RUN useradd --create-home --uid 10001 atlas && mkdir /data && chown atlas /data
COPY --from=build --chown=atlas /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    ATLAS_LOG_STORE=/data \
    PYTHONUNBUFFERED=1
USER atlas
WORKDIR /home/atlas
VOLUME /data
EXPOSE 8321
# /static is answered without the token
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8321/static/webapp.js', timeout=4)"]
ENTRYPOINT ["atlas-log"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8321"]
