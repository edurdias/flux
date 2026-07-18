"""Perf harness — self-contained measurement infrastructure for tests/perf.

Deliberately independent of tests/e2e (liftable as a unit, per PLAN.md §3.4).
Lives under fixtures/ because the pre-commit ``name-tests-test`` hook already
excludes ``tests/*/fixtures/`` — non-test helper modules anywhere else under
tests/ fail its naming check.
"""
