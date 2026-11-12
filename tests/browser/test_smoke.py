"""Browser smoke tests of the web UI: headless Chromium driven by ``smoke.mjs`` over CDP.

Skipped when Node (>= 22, global WebSocket) or a Chromium-family browser is missing, or when
``ATLAS_LOG_NO_BROWSER`` is set; ``ATLAS_LOG_BROWSER`` names the browser executable.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from atlas_log_analyzer.cli.app import run

ROOT = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).with_name("smoke.mjs")
PAGES = ("index", "report", "triage", "cluster", "not-found", "report-dark")
DRIVER_TIMEOUT_S = 50

WINDOWS_BROWSERS = (
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
)
POSIX_BROWSERS = ("google-chrome", "chromium", "chromium-browser", "msedge", "microsoft-edge")


def find_browser() -> str | None:
    """Return the browser executable: ``ATLAS_LOG_BROWSER``, else a usual install, else None."""
    env = os.environ.get("ATLAS_LOG_BROWSER")
    if env:
        return env if Path(env).is_file() else None
    if sys.platform == "win32":
        for template in WINDOWS_BROWSERS:
            path = os.path.expandvars(template)
            if Path(path).is_file():
                return path
        return None
    for name in POSIX_BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def node_problem() -> str | None:
    """Return why Node cannot drive the browser, or None when it can."""
    node = shutil.which("node")
    if not node:
        return "node is not installed"
    probe = subprocess.run(
        [node, "-e", "process.exit(typeof WebSocket === 'function' ? 0 : 1)"],
        capture_output=True,
        timeout=30,
        check=False,
    )
    return None if probe.returncode == 0 else "node has no global WebSocket (needs Node >= 22)"


def free_port() -> int:
    """Return a TCP port nobody listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def kill_tree(proc: subprocess.Popen[Any]) -> None:
    """Kill a process and its children (Windows needs ``taskkill /T``)."""
    if proc.poll() is None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
            )
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def get_json(url: str, timeout: float = 5) -> Any:
    """GET ``url`` and decode its JSON body."""
    with urllib.request.urlopen(url, timeout=timeout) as res:
        return json.load(res)


def wait_for(url: str, what: str, proc: subprocess.Popen[Any], seconds: float = 25) -> None:
    """Poll ``url`` until it answers; fail with ``what`` when the process dies or time runs out."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if proc.poll() is not None:
            pytest.fail(f"{what} exited with code {proc.returncode}")
        try:
            get_json(url, timeout=1)
            return
        except OSError:
            time.sleep(0.1)
    pytest.fail(f"{what} did not answer at {url} within {seconds:.0f} s")


def demo_logs(out: Path) -> None:
    """Write the synthetic demo logs of ``scripts/demo_logs.py`` into ``out``."""
    spec = importlib.util.spec_from_file_location("demo_logs", ROOT / "scripts" / "demo_logs.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    saved = sys.argv
    sys.argv = ["demo_logs.py", str(out)]
    try:
        spec.loader.exec_module(module)
        module.main()
    finally:
        sys.argv = saved
        sys.modules.pop(spec.name, None)


@pytest.fixture(scope="module")
def smoke(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Serve a demo store, drive a headless browser over it and return the driver's result."""
    if os.environ.get("ATLAS_LOG_NO_BROWSER"):
        pytest.skip("ATLAS_LOG_NO_BROWSER is set")
    why = node_problem()
    if why is not None:
        pytest.skip(why)
    browser = find_browser()
    if browser is None:
        pytest.skip("no Chromium-family browser found (set ATLAS_LOG_BROWSER to its path)")

    tmp = tmp_path_factory.mktemp("browser")
    logs, store = tmp / "logs", tmp / "store"
    demo_logs(logs)
    for name in ("prev", "week"):
        args = ["--store", str(store), "ingest", str(logs / name)]
        code = run([*args, "--cluster", "demo-jira", "--name", name])
        assert code == 0, f"ingest of the demo {name} logs failed with exit code {code}"

    port, cdp_port = free_port(), free_port()
    base = f"http://127.0.0.1:{port}"
    posix = sys.platform != "win32"
    procs: list[subprocess.Popen[Any]] = []
    server_log = (tmp / "server.log").open("w")
    try:
        server = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from atlas_log_analyzer.cli.app import main; main()",
                "--store",
                str(store),
                "serve",
                "--port",
                str(port),
            ],
            stdout=server_log,
            stderr=subprocess.STDOUT,
            start_new_session=posix,
        )
        procs.append(server)
        wait_for(f"{base}/api/health", "atlas-log serve", server)

        chrome = subprocess.Popen(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                f"--remote-debugging-port={cdp_port}",
                f"--user-data-dir={tmp / 'profile'}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=posix,
        )
        procs.append(chrome)
        wait_for(f"http://127.0.0.1:{cdp_port}/json/version", "the browser", chrome)

        items = get_json(f"{base}/api/sets")["items"]
        config = {
            "base": base,
            "cdp": f"http://127.0.0.1:{cdp_port}",
            "sets": {s["name"]: s["id"] for s in items},
            "capture": next(s["capture"] for s in items if s.get("capture")),
        }
        done = subprocess.run(
            ["node", str(DRIVER), json.dumps(config)],
            capture_output=True,
            text=True,
            timeout=DRIVER_TIMEOUT_S,
            check=False,
        )
        if done.returncode != 0:
            pytest.fail(f"smoke.mjs exited with {done.returncode}: {done.stderr.strip()[-800:]}")
        result: dict[str, Any] = json.loads(done.stdout)
        return result
    finally:
        for proc in reversed(procs):
            kill_tree(proc)
        server_log.close()


@pytest.mark.parametrize("name", PAGES)
def test_page(smoke: dict[str, Any], name: str) -> None:
    """Every check of the page passed and it logged no JS error, console error or HTTP error."""
    page = next((p for p in smoke["pages"] if p["name"] == name), None)
    assert page is not None, f"{name}: the driver did not report this page"
    failed = [
        c["name"] + (f" [{c['detail']}]" if c["detail"] else "")
        for c in page["checks"]
        if not c["ok"]
    ]
    assert not failed, f"{name} page: failed checks: " + "; ".join(failed)
    assert not page["problems"], f"{name} page: browser problems: " + "; ".join(page["problems"])
