"""HTTP integration checks against a real Streamlit server.

Regression for the Streamlit 1.60 + starlette>=1.8 gzip incompatibility:
responses with a content type excluded from compression (e.g. /favicon.png)
failed with HTTP 500 and "Expected ASGI message 'http.response.start'".
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = _free_port()
    log = tmp_path_factory.mktemp("srv") / "server.log"
    env = dict(os.environ, STREAMLIT_BROWSER_GATHER_USAGE_STATS="false")
    with log.open("w") as fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "app/streamlit_app.py", f"--server.port={port}",
             "--server.headless=true", "--server.address=127.0.0.1"],
            cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT, env=env)
        base = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                if urllib.request.urlopen(base + "/_stcore/health", timeout=1).status == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            proc.terminate()
            pytest.fail("Streamlit server did not become healthy:\n" + log.read_text())
        yield base, log
        proc.terminate()
        proc.wait(timeout=20)


def _get(url: str, gzip: bool) -> tuple[int, bytes]:
    headers = {"Accept-Encoding": "gzip"} if gzip else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


@pytest.mark.parametrize("gzip", [True, False])
@pytest.mark.parametrize("path", ["/", "/_stcore/health", "/favicon.png", "/manifest.json"])
def test_endpoints_ok(server, path, gzip):
    base, _ = server
    status, _ = _get(base + path, gzip)
    assert status == 200, f"{path} gzip={gzip} -> {status}"


def test_static_assets_and_concurrency(server):
    base, log = server
    _, html = _get(base + "/", gzip=True)
    assets = sorted(set(re.findall(rb'(?:src|href)="\.?/?(static/[^"]+)"', html)))
    assert assets, "index.html references no static assets"
    urls = [f"{base}/{a.decode()}" for a in assets] + [base + p for p in ("/", "/favicon.png", "/_stcore/health")]
    jobs = [(u, g) for u in urls for g in (True, False)] * 5
    with ThreadPoolExecutor(max_workers=16) as pool:
        statuses = list(pool.map(lambda j: _get(*j)[0], jobs))
    assert all(s == 200 for s in statuses), sorted(set(statuses))
    text = log.read_text()
    assert "Traceback" not in text and "Exception in ASGI" not in text, text[-3000:]
