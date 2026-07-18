"""T6 — Violence: cancel storm, worker kill -9, connection drop (PLAN.md §2).

Runs in its own module-scoped environment with tight heartbeat settings so
worker-death detection is measurable inside a CI window, and so killing
workers can't poison the shared session environment.

Hard assertions are correctness (terminal states, no protocol wedge);
timing figures are recorded, with the cancel-flush burst documented rather
than punished (PLAN.md changelog-7).
"""

from __future__ import annotations

import os
import time

import pytest

from fixtures.harness.profile import params, profile_name
from fixtures.harness.report import write_run
from fixtures.harness.scenario import start_stream
from fixtures.harness.tcpproxy import TcpProxy

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


@pytest.fixture(scope="module")
def violence_env(tmp_path_factory):
    from fixtures.harness.env import FluxPerfEnv

    env = FluxPerfEnv(
        tmp_path_factory.mktemp("perf-violence"),
        database_url=os.environ.get("FLUX_PERF_DATABASE_URL_VIOLENCE"),
        worker_name="violence-worker",
        env_overrides={
            "FLUX_WORKERS__HEARTBEAT_INTERVAL": "2",
            "FLUX_WORKERS__HEARTBEAT_TIMEOUT": "6",
            "FLUX_WORKERS__EVICTION_GRACE_PERIOD": "4",
        },
    ).start()
    yield env
    env.stop()


@pytest.fixture(scope="module")
def violence_workflow(violence_env, stream_workflow_source):
    result = violence_env.register(stream_workflow_source)
    entries = result if isinstance(result, list) else [result]
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name") == "perf_stream":
            return entry.get("namespace", "default")
    return "default"


def _state(env, namespace, execution_id) -> str:
    return env.status(namespace, "perf_stream", execution_id).get("state", "?")


def test_t6a_cancel_storm(violence_env, violence_workflow):
    namespace = violence_workflow
    p = params("t6a")
    frames = int(p["rate"] * (p["cancel_after_s"] + 60))
    launched = [
        start_stream(violence_env, namespace, "perf_stream", frames=frames, rate=p["rate"])
        for _ in range(p["streams"])
    ]
    # Pin the scenario: cancel *at full rate*, not during claim/startup —
    # every stream must be demonstrably producing first. (A cancel racing
    # child startup is a different scenario; one hung-cancel occurrence was
    # observed there during shakedown and is noted in the findings.)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if all(c.frames for _, c in launched):
            break
        time.sleep(0.5)
    assert all(c.frames for _, c in launched), "not all streams started producing"
    time.sleep(p["cancel_after_s"])
    t_cancel = time.time()
    for execution_id, _ in launched:
        violence_env.cancel(namespace, "perf_stream", execution_id)

    finals = [
        violence_env.wait_for_terminal(namespace, "perf_stream", e, timeout=120)
        for e, _ in launched
    ]
    time.sleep(2.0)  # allow any post-terminal flush burst to land
    for _, c in launched:
        c.stop()

    last_frame_after_cancel = [
        max((f.t_recv for f in c.frames), default=t_cancel) - t_cancel for _, c in launched
    ]
    burst_s = max(last_frame_after_cancel)
    states = [f["state"] for f in finals]
    assert all(s == "CANCELLED" for s in states), f"non-cancelled terminals: {states}"

    write_run(
        "T6a",
        f"cancel-storm-{profile_name()}",
        {
            "gate": "all executions CANCELLED (hard); flush-burst duration documented",
            "measured": (
                f"{p['streams']} streams cancelled at full rate; all CANCELLED; "
                f"last frame arrived {burst_s * 1000:.0f}ms after cancel "
                f"(cancel-flush burst, expected behavior)"
            ),
            "passed": True,
            "sealed": False,
            "database": violence_env.database_url.split(":", 1)[0],
            "profile": profile_name(),
            "flush_burst_s": burst_s,
            "per_stream_last_frame_after_cancel_s": last_frame_after_cancel,
        },
    )


def test_t6b_worker_kill9(violence_env, violence_workflow):
    namespace = violence_workflow
    p = params("t6b")
    execution_id, consumer = start_stream(
        violence_env,
        namespace,
        "perf_stream",
        frames=100_000,
        rate=100,
    )
    time.sleep(5.0)
    assert _state(violence_env, namespace, execution_id) in ("RUNNING", "CLAIMED")

    t_kill = time.time()
    violence_env.kill_worker(violence_env.worker_proc, force=True)

    # What the server does with the orphaned claim (fail terminally, or
    # release for re-dispatch) is the finding; the hard gate is that it does
    # SOMETHING observable within the tightened heartbeat budget.
    deadline = time.monotonic() + p["detect_timeout_s"]
    observed = None
    while time.monotonic() < deadline:
        state = _state(violence_env, namespace, execution_id)
        if state not in ("RUNNING", "CLAIMED"):
            observed = state
            break
        time.sleep(0.5)
    detection_s = time.time() - t_kill
    consumer.stop()
    assert observed is not None, (
        f"execution still {_state(violence_env, namespace, execution_id)} "
        f"{p['detect_timeout_s']}s after worker kill -9 — orphaned claim never detected"
    )

    write_run(
        "T6b",
        f"worker-kill-{profile_name()}",
        {
            "gate": "orphaned claim detected (hard); detection latency + policy documented",
            "measured": (
                f"state left RUNNING {detection_s:.1f}s after kill -9 "
                f"(heartbeat_timeout=6s, grace=4s); resulting state: {observed}"
            ),
            "passed": True,
            "sealed": False,
            "database": violence_env.database_url.split(":", 1)[0],
            "profile": profile_name(),
            "detection_s": detection_s,
            "resulting_state": observed,
        },
    )


def test_t6c_connection_drop(violence_env, violence_workflow):
    namespace = violence_workflow
    p = params("t6c")

    proxy = TcpProxy("127.0.0.1", violence_env.port).start()
    proc = violence_env.start_extra_worker(
        "proxied-worker",
        server_url=f"http://127.0.0.1:{proxy.port}",
    )
    try:
        frames = int(p["rate"] * p["seconds"])
        execution_id, consumer = start_stream(
            violence_env,
            namespace,
            "perf_stream",
            frames=frames,
            rate=p["rate"],
        )
        time.sleep(2.0 + p["drop_after_s"])
        t_drop = time.time()
        proxy.drop()
        time.sleep(p["drop_for_s"])
        proxy.restore()
        t_restore = time.time()

        consumer.wait(p["seconds"] + 180)
        consumer.stop()
        final = violence_env.wait_for_terminal(
            namespace,
            "perf_stream",
            execution_id,
            timeout=240,
        )

        seqs = [f.seq for f in consumer.frames if f.seq is not None]
        duplicates = len(seqs) - len(set(seqs))
        received_after_restore = sum(1 for f in consumer.frames if f.t_recv > t_restore)
        lost = frames - len(set(seqs))

        # No protocol wedge = the execution reaches a coherent terminal
        # state. Which one is the documented fate: an outage shorter than the
        # heartbeat timeout should resume streaming (frames after restore); a
        # longer one trips eviction and the claim is torn away — also
        # coherent, also recorded.
        assert final["state"] in TERMINAL, f"non-terminal state {final['state']} — wedged"
        fate = (
            "resumed streaming after restore"
            if received_after_restore
            else f"did not resume; terminal {final['state']} (eviction path)"
        )

        write_run(
            "T6c",
            f"conn-drop-{profile_name()}",
            {
                "gate": "no protocol wedge (hard); frame fate documented",
                "measured": (
                    f"dropped link for {t_restore - t_drop:.1f}s mid-stream; "
                    f"terminal {final['state']}; fate: {fate}; {lost} frames lost "
                    f"(batch drops during outage, per policy), {duplicates} duplicated"
                ),
                "passed": True,
                "sealed": False,
                "database": violence_env.database_url.split(":", 1)[0],
                "profile": profile_name(),
                "frames_offered": frames,
                "frames_lost": lost,
                "frames_duplicated": duplicates,
                "frames_after_restore": received_after_restore,
                "drop_window_s": t_restore - t_drop,
            },
        )
    finally:
        proxy.stop()
        violence_env.kill_worker(proc)
