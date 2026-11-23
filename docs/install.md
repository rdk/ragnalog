# Installing and running ragnalog

Three ways to run it: the **Docker image** (the web app with nothing else installed), the
**`ragnalog` command** installed with uv (CLI and web app), or a **source checkout** (for
development and the demo logs). ragnalog is not on PyPI; releases are installed from
GitHub.

## Docker

The image `ghcr.io/rdk/ragnalog` is published with each release for linux/amd64 and
linux/arm64; pulling it needs no sign-in. It runs the web app on port 8321 and keeps the
log sets in `/data`, the data directory.

### Run the web app

Whoever reaches the port can read, upload and delete log sets, so publish it on this
machine only and give the app a token. Keep the token in an env file, so a later
`docker run` (an upgrade, a new shell) passes the same one:

```sh
(umask 077; echo "RAGNALOG_TOKEN=$(openssl rand -hex 16)" > ragnalog.env)
docker run -d --name ragnalog -p 127.0.0.1:8321:8321 -v ragnalog:/data \
  --env-file ragnalog.env ghcr.io/rdk/ragnalog:0.6.1
echo "http://localhost:8321/?token=$(cut -d= -f2 ragnalog.env)"
```

Open the printed address. The browser keeps the token in a cookie until it is closed;
after that, print the address again with the same `echo`. Without a token (no env file,
or an empty `RAGNALOG_TOKEN`) the app asks for none at all.

In PowerShell:

```powershell
"RAGNALOG_TOKEN=$([guid]::NewGuid().ToString('N'))" | Set-Content ragnalog.env
docker run -d --name ragnalog -p 127.0.0.1:8321:8321 -v ragnalog:/data --env-file ragnalog.env ghcr.io/rdk/ragnalog:0.6.1
"http://localhost:8321/?token=$((Get-Content ragnalog.env).Split('=')[1])"
```

To serve other machines, publish `-p 8321:8321`; the token then guards it alone.

### Add logs

Upload files on the start page: a support zip, a cluster log export or single log files.
Or ingest a folder with the CLI from the same image (any `ragnalog` command works this
way):

```sh
docker run --rm -v ragnalog:/data -v "$PWD/logs:/in:ro" \
  ghcr.io/rdk/ragnalog:0.6.1 ingest /in --name prod-1030
```

The container runs as user 10001. On Linux it can read only files that others may read:
logs taken from a Jira server are often readable by its own user alone, so copy them into
a folder of yours (or `chmod -R o+rX logs`).

### Upgrade

The log sets stay in the volume. Remove the container and run it again with the new tag:

```sh
docker rm -f ragnalog
docker run -d --name ragnalog -p 127.0.0.1:8321:8321 -v ragnalog:/data \
  --env-file ragnalog.env ghcr.io/rdk/ragnalog:NEW_VERSION
```

When the release notes say signatures changed, older sets show as stale; rebuild them with
`docker exec ragnalog ragnalog upgrade --all`.

### Docker Compose

```yaml
services:
  ragnalog:
    image: ghcr.io/rdk/ragnalog:0.6.1
    ports: ["127.0.0.1:8321:8321"]
    environment:
      RAGNALOG_TOKEN: ${RAGNALOG_TOKEN:?set a token}
    volumes: ["ragnalog:/data"]
    restart: unless-stopped
volumes:
  ragnalog:
```

Keep the token in a `.env` file beside it (`RAGNALOG_TOKEN=...`), start with
`docker compose up -d`, and ingest with
`docker compose run --rm -v "$PWD/logs:/in:ro" ragnalog ingest /in --name prod-1030`.
To upgrade, change the tag and run `docker compose up -d` again.

### Build the image yourself

```sh
docker build -t ragnalog .
```

## The `ragnalog` command

You need [uv](https://docs.astral.sh/uv/getting-started/installation/); it fetches
Python 3.14 itself. Install the wheel attached to a release (`[web]` adds the web app,
`[mine]` the optional Drain3 template mining):

```sh
uv tool install "ragnalog[web] @ https://github.com/rdk/ragnalog/releases/download/v0.6.1/ragnalog-0.6.1-py3-none-any.whl"
ragnalog --version
```

or build it from the release tag (needs git):

```sh
uv tool install "ragnalog[web] @ git+https://github.com/rdk/ragnalog.git@v0.6.1"
```

Then choose a data directory, the folder that keeps the log sets (outside any git
repository; the option and variable that name it are `--store` and `RAGNALOG_STORE`),
ingest some logs and open the web app:

```sh
export RAGNALOG_STORE=~/ragnalog-data
ragnalog ingest Jira_support_2026-10-30.zip --name prod-1030
ragnalog serve                                   # http://127.0.0.1:8321/
```

`serve` listens on 127.0.0.1 only. To serve a network, bind all interfaces and set a
token: `ragnalog serve --host 0.0.0.0 --token "$(openssl rand -hex 16)"` (see
[`webapp.md`](webapp.md)).

To upgrade, run `uv tool install` again with the new version and `--force`.

## From source

```sh
git clone https://github.com/rdk/ragnalog.git && cd ragnalog
uv sync --all-extras --dev                       # .venv with every extra and the dev tools
uv run ragnalog --help
```

### Demo logs

No logs at hand? A source checkout can generate the synthetic two-node cluster the README
screenshots use: two sets of three days each, a week apart, with invented hosts, users and
plugins. Keep the output outside the checkout:

```sh
uv run python scripts/demo_logs.py ../ragnalog-demo/logs
uv run ragnalog --store ../ragnalog-demo/data ingest ../ragnalog-demo/logs/prev --cluster demo-jira --name prev
uv run ragnalog --store ../ragnalog-demo/data ingest ../ragnalog-demo/logs/week --cluster demo-jira --name week
uv run ragnalog --store ../ragnalog-demo/data serve
```

Development, checks and how to report a parsing problem are in
[`CONTRIBUTING.md`](../CONTRIBUTING.md).
