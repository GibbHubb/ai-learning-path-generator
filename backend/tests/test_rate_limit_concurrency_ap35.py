"""AP35 criterion 4 — the concurrency guarantee.

10 requests fired SIMULTANEOUSLY (asyncio.gather over httpx.AsyncClient)
against a REAL uvicorn subprocess (not TestClient, which serves requests
synchronously one at a time and therefore can never exercise a race) must
yield exactly 5x200 and 5x429 — no 6th success from a lost update.

The server is a genuine separate OS process (see _ap35_concurrency_server.py)
so the count-and-insert really is racing across independent DB connections,
not just interleaved coroutines sharing one GIL. It is started/health-polled/
killed by PID, never by a fixed sleep or a shell kill — see the 2026-09-02
lesson this mirrors from other subprocess-driven tests in this project.
"""
import asyncio
import os
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(TESTS_DIR)
HARNESS = os.path.join(TESTS_DIR, "_ap35_concurrency_server.py")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_healthy(base_url: str, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_exc = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server process exited early with code {proc.returncode}")
        try:
            r = httpx.get(f"{base_url}/health", timeout=1.0)
            if r.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001 — still starting up
            last_exc = exc
        time.sleep(0.2)
    raise RuntimeError(f"server never became healthy within {timeout}s: {last_exc}")


@pytest.fixture
def live_server(tmp_path):
    port = _free_port()
    db_path = str(tmp_path / f"ap35_conc_{uuid.uuid4().hex}.db")
    log_path = tmp_path / "server.log"
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, HARNESS, str(port), db_path],
        cwd=BACKEND_DIR,
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_healthy(base_url, proc)
        yield base_url
    finally:
        # Kill by PID, never by killing the shell — the process was started
        # directly (no shell=True), so proc.pid IS the uvicorn process.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log_file.close()


async def _fire_ten(base_url: str) -> list[int]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        tasks = [
            client.post(f"{base_url}/api/generate", json={
                "goal": f"concurrency-{i}",
                "experience_level": "beginner",
                "time_commitment": "5-10 hours/week",
            })
            for i in range(10)
        ]
        responses = await asyncio.gather(*tasks)
        return [r.status_code for r in responses]


def test_ten_concurrent_requests_yield_exactly_five_ok_five_blocked(live_server):
    codes = asyncio.run(_fire_ten(live_server))
    assert len(codes) == 10, codes
    assert set(codes) <= {200, 429}, codes  # nothing else (no 500s from a crashed stub, etc.)
    assert codes.count(200) == 5, codes
    assert codes.count(429) == 5, codes
