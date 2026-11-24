# ragnalog with the web app:
#   docker run -p 127.0.0.1:8321:8321 -v ragnalog:/data -e RAGNALOG_TOKEN=... IMAGE
# Any other command: `docker run --rm -v ragnalog:/data IMAGE groups SET --brief`.
# The build stage and the runtime share one Python (python:3.14-slim-trixie) and the
# virtual environment's path (/app/.venv: its scripts name their interpreter by it), so
# the copied environment runs as built. Releases build it for linux/amd64 and linux/arm64
# and add the OCI labels (release.yml). The build arguments RAGNALOG_BUILD_NUMBER and
# RAGNALOG_BUILD_GIT record the build (docs/install.md).

FROM ghcr.io/astral-sh/uv:0.9-python3.14-trixie-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --extra web --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --extra web --no-editable

FROM python:3.14-slim-trixie
RUN useradd --create-home --uid 10001 ragnalog && mkdir /data && chown ragnalog /data
COPY --from=build --chown=ragnalog /app/.venv /app/.venv
# The build (version.v1 "build", the pages' footers): its number (the commit count of the
# built commit) and git hash from the build arguments, its time now. CI and releases pass
#   --build-arg RAGNALOG_BUILD_NUMBER=$(git rev-list --count HEAD)
#   --build-arg RAGNALOG_BUILD_GIT=$(git rev-parse --short=7 HEAD)
ARG RAGNALOG_BUILD_NUMBER=""
ARG RAGNALOG_BUILD_GIT=""
RUN printf '{"number": "%s", "git": "%s", "time": "%s"}\n' "$RAGNALOG_BUILD_NUMBER" \
        "$RAGNALOG_BUILD_GIT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > /app/build.json
ENV PATH=/app/.venv/bin:$PATH \
    RAGNALOG_STORE=/data \
    RAGNALOG_BUILD_FILE=/app/build.json \
    PYTHONUNBUFFERED=1
USER ragnalog
WORKDIR /home/ragnalog
VOLUME /data
EXPOSE 8321
# /static is answered without the token
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8321/static/webapp.js', timeout=4)"]
ENTRYPOINT ["ragnalog"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8321"]
