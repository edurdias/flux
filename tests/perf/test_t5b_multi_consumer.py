"""T5b — Multi-consumer semantics documentation test (PLAN.md §2).

Pre-registered expectation from code: `_progress_buffers` holds ONE queue per
execution, so two concurrent consumers compete for frames (each frame reaches
exactly one). Measurement CONFIRMED the competition (zero duplicate
sequences) but FALSIFIED the second half of the expectation — the survivor
was NOT starved after its sibling disconnected. The buffer-pop in the SSE
generator's ``finally`` (flux/server.py:605) evidently runs at async-generator
finalization, not at client-disconnect time, so post-disconnect behavior is
timing/GC-dependent rather than deterministic starvation. See
findings/T5b_multi_consumer.md.

Hard assertions pin only the deterministic part (competition, no
duplication); survivor behavior is recorded as an observation.
"""

from __future__ import annotations

import time

from fixtures.harness.consumer import StreamConsumer
from fixtures.harness.profile import params, profile_name
from fixtures.harness.report import write_run
from fixtures.harness.scenario import start_stream


def test_t5b_multi_consumer_semantics(perf_env, stream_workflow):
    namespace, name = stream_workflow
    p = params("t5b")
    frames = int(p["rate"] * p["seconds"])

    execution_id, consumer_a = start_stream(
        perf_env,
        namespace,
        name,
        frames=frames,
        rate=p["rate"],
        start_delay=2.0,
    )
    time.sleep(4.0)  # A alone for ~2s of frames
    consumer_b = StreamConsumer(perf_env.server_url, execution_id).start()
    time.sleep(5.0)  # A and B compete
    a_before_disconnect = len(consumer_a.frames)
    b_before_disconnect = len(consumer_b.frames)
    consumer_a.stop()
    t_disconnect = time.time()
    time.sleep(5.0)  # B alone — starved, per expectation
    b_after_disconnect = len([f for f in consumer_b.frames if f.t_recv > t_disconnect])
    consumer_b.stop()
    perf_env.wait_for_terminal(namespace, name, execution_id, timeout=180)

    competed = b_before_disconnect > 0
    survivor_starved = b_after_disconnect == 0
    total_unique = len(
        {f.seq for f in consumer_a.frames} | {f.seq for f in consumer_b.frames},
    )
    overlap = len(
        {f.seq for f in consumer_a.frames} & {f.seq for f in consumer_b.frames},
    )

    write_run(
        "T5b",
        f"multi-consumer-{profile_name()}",
        {
            "gate": "documentation only — records current semantics",
            "measured": (
                f"during overlap A got {a_before_disconnect}, B got "
                f"{b_before_disconnect} (competing, {overlap} duplicate seqs); "
                f"after A disconnected B received {b_after_disconnect} frames "
                f"({'starved' if survivor_starved else 'still receiving'})"
            ),
            "passed": True,
            "sealed": False,
            "database": perf_env.database_url.split(":", 1)[0],
            "profile": profile_name(),
            "frames_offered": frames,
            "unique_frames_seen": total_unique,
            "competition_observed": competed,
            "survivor_starved": survivor_starved,
        },
    )

    # Pin the deterministic semantics so a change (per-consumer fan-out
    # duplicating frames to every subscriber) flips this test loudly.
    assert competed, "expected both consumers to receive frames while overlapping"
    assert overlap == 0, (
        f"{overlap} frames reached BOTH consumers — the shared-queue "
        "competition model has changed; update PLAN.md §0b and the findings doc"
    )
