"""Dispatch-plane stress test: poll mode vs event mode on PostgreSQL.

Spawns a real Flux server (subprocess) and N protocol-level simulated workers
in-process (register -> SSE connect -> pong -> claim -> checkpoint COMPLETED).
No workflow code executes on the workers; this isolates the dispatch plane.

Measures, per mode:
  1. Idle DB load: PostgreSQL transactions/sec with N connected, idle workers.
  2. Dispatch throughput: time to drain M executions across the fleet.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
from httpx_sse import aconnect_sse

PORT = int(os.environ.get("STRESS_PORT", "8022"))
SERVER = f"http://localhost:{PORT}"
BOOTSTRAP = "stress-bootstrap-token"
# Admin DSN for creating scratch databases and reading pg_stat_database.
PG_ADMIN = os.environ.get(
    "STRESS_PG_ADMIN_URL",
    "postgresql://flux_test_user:flux_test_password@localhost:5433/postgres",
)
PG_HOSTPORT = PG_ADMIN.rsplit("@", 1)[1].split("/", 1)[0]
PG_CREDS = PG_ADMIN.split("//", 1)[1].rsplit("@", 1)[0]
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 200
EXECUTIONS = int(sys.argv[3]) if len(sys.argv) > 3 else 400
IDLE_SECONDS = 20

WORKFLOW_SRC = b"""
from flux import ExecutionContext
from flux.workflow import workflow


@workflow
async def stress_noop(ctx: ExecutionContext[str]):
    return "done"
"""


def pg_xacts(dbname: str) -> int:
    import psycopg

    with psycopg.connect(PG_ADMIN, autocommit=True) as conn:
        row = conn.execute(
            "SELECT xact_commit + xact_rollback FROM pg_stat_database WHERE datname = %s",
            (dbname,),
        ).fetchone()
        return row[0] if row else 0


class Fleet:
    def __init__(self, n: int):
        self.n = n
        self.connected = 0
        self.completed = 0
        self.dispatch_latencies: list[float] = []
        self.enqueue_time: float | None = None
        self.all_done = asyncio.Event()
        self.stop = asyncio.Event()
        self._clients: list[httpx.AsyncClient] = []

    def client(self, i: int) -> httpx.AsyncClient:
        # Spread workers over a few clients; unlimited pool per client.
        idx = i % 8
        while len(self._clients) <= idx:
            self._clients.append(
                httpx.AsyncClient(
                    timeout=httpx.Timeout(30, read=None),
                    limits=httpx.Limits(max_connections=None),
                ),
            )
        return self._clients[idx]

    async def close(self):
        for c in self._clients:
            await c.aclose()

    async def worker(self, i: int):
        from flux.domain.execution_context import ExecutionContext

        name = f"sim-{i:04d}"
        client = self.client(i)
        reg = {
            "name": name,
            "runtime": {"os_name": "linux", "os_version": "1", "python_version": "3.12"},
            "packages": [],
            "resources": {
                "cpu_total": 1,
                "cpu_available": 1,
                "memory_total": 1,
                "memory_available": 1,
                "disk_total": 1,
                "disk_free": 1,
                "gpus": [],
            },
        }
        r = await client.post(
            f"{SERVER}/workers/register",
            json=reg,
            headers={"Authorization": f"Bearer {BOOTSTRAP}"},
        )
        r.raise_for_status()
        headers = {"Authorization": f"Bearer {r.json()['session_token']}"}

        async def handle(evt_data: str):
            data = json.loads(evt_data)
            eid = data["context"]["execution_id"]
            cr = await client.post(f"{SERVER}/workers/{name}/claim/{eid}", headers=headers)
            if cr.status_code != 200:
                return
            if self.enqueue_time is not None:
                self.dispatch_latencies.append(time.monotonic() - self.enqueue_time)
            ctx = ExecutionContext.from_json(cr.json())
            ctx.start(name).complete(name, "done")
            ck = await client.post(
                f"{SERVER}/workers/{name}/checkpoint/{eid}",
                json=ctx.to_dict(),
                headers=headers,
            )
            if ck.status_code == 200:
                self.completed += 1
                if self.completed >= EXECUTIONS:
                    self.all_done.set()

        while not self.stop.is_set():
            try:
                async with aconnect_sse(
                    client,
                    "GET",
                    f"{SERVER}/workers/{name}/connect",
                    headers=headers,
                ) as es:
                    self.connected += 1
                    try:
                        async for evt in es.aiter_sse():
                            if self.stop.is_set():
                                return
                            if evt.event == "ping":
                                asyncio.ensure_future(
                                    client.post(f"{SERVER}/workers/{name}/pong", headers=headers),
                                )
                            elif evt.event == "execution_scheduled":
                                asyncio.ensure_future(handle(evt.data))
                    finally:
                        self.connected -= 1
            except Exception:
                if self.stop.is_set():
                    return
                await asyncio.sleep(1)


async def run_mode(mode: str) -> dict:
    dbname = f"flux_stress_{mode}"
    import psycopg

    with psycopg.connect(PG_ADMIN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {dbname}")
        admin.execute(f"CREATE DATABASE {dbname}")

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/root",
        "FLUX_SERVER_PORT": str(PORT),
        "FLUX_DATABASE_URL": f"postgresql://{PG_CREDS}@{PG_HOSTPORT}/{dbname}",
        "FLUX_DISPATCH__MODE": mode,
        "FLUX_WORKERS__BOOTSTRAP_TOKEN": BOOTSTRAP,
        "FLUX_WORKERS__REGISTER_RATE_LIMIT": "",
        "FLUX_SECURITY__AUTH__ENABLED": "false",
        "FLUX_SECURITY__AUTH__ALLOW_ANONYMOUS": "true",
        "FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY": "stress-key",
        "FLUX_HOME": f"/tmp/flux-stress-{mode}",
    }
    log = open(f"/tmp/flux-stress-server-{mode}.log", "w")
    srv = subprocess.Popen(
        [shutil.which("flux") or "flux", "start", "server", "--port", str(PORT)],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
    )
    result: dict = {"mode": mode, "workers": WORKERS, "executions": EXECUTIONS}
    fleet = Fleet(WORKERS)
    tasks: list[asyncio.Task] = []
    try:
        async with httpx.AsyncClient(timeout=5) as probe:
            for _ in range(120):
                try:
                    if (await probe.get(f"{SERVER}/health")).status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("server never became healthy")

            files = {"file": ("stress_noop.py", WORKFLOW_SRC, "text/x-python")}
            (await probe.post(f"{SERVER}/workflows", files=files)).raise_for_status()

        # -- connect fleet -------------------------------------------------
        t0 = time.monotonic()
        tasks = [asyncio.create_task(fleet.worker(i)) for i in range(WORKERS)]
        while fleet.connected < WORKERS:
            if time.monotonic() - t0 > 120:
                raise RuntimeError(f"only {fleet.connected}/{WORKERS} workers connected")
            await asyncio.sleep(0.25)
        result["connect_seconds"] = round(time.monotonic() - t0, 1)

        # -- idle load sample ----------------------------------------------
        await asyncio.sleep(3)  # settle
        x0 = pg_xacts(dbname)
        await asyncio.sleep(IDLE_SECONDS)
        x1 = pg_xacts(dbname)
        result["idle_db_xacts_per_sec"] = round((x1 - x0) / IDLE_SECONDS, 1)

        # -- throughput ----------------------------------------------------
        async with httpx.AsyncClient(timeout=30) as submit:
            sem = asyncio.Semaphore(25)

            async def enqueue(_):
                async with sem:
                    r = await submit.post(
                        f"{SERVER}/workflows/default/stress_noop/run/async",
                        json="x",
                    )
                    r.raise_for_status()

            fleet.enqueue_time = time.monotonic()
            outcomes = await asyncio.gather(
                *[enqueue(i) for i in range(EXECUTIONS)],
                return_exceptions=True,
            )
            failures = [o for o in outcomes if isinstance(o, Exception)]
            result["enqueue_seconds"] = round(time.monotonic() - fleet.enqueue_time, 1)
            if failures:
                result["enqueue_failures"] = len(failures)
                result["control_plane_error"] = type(failures[0]).__name__

        submitted = EXECUTIONS - result.get("enqueue_failures", 0)
        if submitted <= 0:
            return result
        if result.get("enqueue_failures"):
            # Only wait for what was actually accepted.
            while fleet.completed < submitted:
                await asyncio.sleep(1)
                if time.monotonic() - fleet.enqueue_time > 600:
                    result["drain_timeout"] = True
                    return result
        else:
            await asyncio.wait_for(fleet.all_done.wait(), timeout=600)
        drain = time.monotonic() - fleet.enqueue_time
        result["drain_seconds"] = round(drain, 1)
        result["throughput_per_sec"] = round(EXECUTIONS / drain, 1)
        if fleet.dispatch_latencies:
            lats = sorted(fleet.dispatch_latencies)
            result["first_dispatch_ms"] = round(lats[0] * 1000)
            result["median_dispatch_s"] = round(statistics.median(lats), 2)
    finally:
        fleet.stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await fleet.close()
        srv.terminate()
        try:
            srv.wait(timeout=15)
        except subprocess.TimeoutExpired:
            srv.kill()
        log.close()
    return result


async def main():
    mode = sys.argv[1]
    result = await run_mode(mode)
    print(json.dumps(result, indent=2))
    Path(f"/tmp/stress-result-{mode}.json").write_text(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
