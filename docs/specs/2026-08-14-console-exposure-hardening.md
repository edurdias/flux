# Console exposure hardening — the web console as a local client

Status: accepted (2026-08-14). Follows `docs/specs/2026-08-13-agent-console-spec.md`.

## Problem

The web console holds the operator's Flux token in its own process and applies it to
every call, so the browser never handles a credential. The cost of that choice is that
authorization is not "does this request carry a token" but "did this request reach the
port". Two gaps follow:

1. `flux agent start --mode web --host 0.0.0.0` — already available in released
   versions — publishes an unauthenticated console. Anyone who can reach the port acts
   as the operator: list every session, read every transcript, spawn agents, approve
   gated tasks (including standing `always` grants), cancel executions.
2. Bound to loopback, the console still answers requests whose `Host` header names an
   attacker-controlled domain. DNS rebinding therefore lets a visited page read console
   GETs — session lists and transcripts — from the victim's own machine.

The `X-Flux-Console` header already blocks classic cross-site POSTs, and the Origin
allowlist pins the frontend's origin on state-changing routes. Neither addresses the
two gaps above, which are about *reachability* rather than request forgery.

## Decisions

**No new credential.** The console gains no login flow and no session token. On a
single-operator machine the browser user is the OS user, so a browser login would
authenticate the same principal twice while placing a live credential in a page that
renders agent and tool output. Per-user identity becomes the right answer only when the
console is shared or exposed, and that is a different feature (authorization code +
PKCE, per-user sessions) — not something to approximate by handing a page the CLI's
token.

**Exposure becomes a deliberate act.** Web mode refuses a non-loopback bind unless the
operator passes `--allow-remote`, rather than warning after the fact.

## Mechanisms

### 1. Bind gate (CLI)

`flux agent start` gains `--allow-remote`. In **web mode only**, a non-loopback `--host`
without that flag exits 1 before anything binds:

```
web mode has no authentication of its own — binding 0.0.0.0 grants operator rights to
anyone who can reach port 8080. Pass --allow-remote to accept that, or run --mode api
behind a proxy that authenticates.
```

A host counts as loopback when it is `localhost` or a literal address whose
`ipaddress.ip_address(...).is_loopback` is true. Wildcards (`0.0.0.0`, `::`) and every
other hostname are treated as exposure, so an unfamiliar or unparseable value fails
closed.

Terminal mode never binds. **api mode is exempt**: it authenticates every request with a
Bearer token, which is the shape remote use should have.

### 2. Host allowlist (web mode)

When bound to loopback, `WebUI` installs a Host-header check over *every* request —
including GETs and `/static`, since rebinding is a read attack. Allowed hosts are the
bind address, `127.0.0.1`, `localhost`, and `::1`; a mismatch gets 400.

The check is a small local middleware rather than Starlette's `TrustedHostMiddleware`,
which splits the header on `:` and so mangles `[::1]:8080` — a legitimate configuration
here, because the bind gate accepts IPv6 loopback.

Under `--allow-remote` the check is not installed. That is deliberate, not an oversight:
rebinding is a technique for reaching a *loopback-only* service through a victim's
browser. Once the port is genuinely reachable, an attacker connects directly and a Host
allowlist buys nothing.

### 3. Startup line

`serve()` prints nothing today, so operators guess the URL. It now prints the bound URL
on start, and under `--allow-remote` a warning naming what is exposed and pointing at a
proxy.

## What this deliberately does not do

- **A malicious local process is still able to drive the console.** It can set any
  header, so neither the CSRF header nor the Origin allowlist constrains it. On a
  single-operator machine, local code already runs as the operator; closing this would
  require an OS-level boundary (peer-credential checks over a unix socket), which no
  browser can speak.
- **`X-Flux-Console` stays required in every mode.** It is the fail-closed control
  against cross-site POSTs — absent means reject — where the Origin check is enforced
  only when an Origin is present, because non-browser clients never send one. Keeping it
  in api mode also covers the deployment we recommend for multi-user: api mode behind a
  proxy that injects the Authorization header re-creates ambient authority for any
  browser that can reach the proxy.

## Testing

- CLI (CliRunner): `--mode web --host 0.0.0.0` exits 1 with the message; the same plus
  `--allow-remote` proceeds and threads the flag; `--mode api --host 0.0.0.0` is
  unaffected; a loopback bind is unaffected; `localhost` counts as loopback.
- WebUI (TestClient): a foreign `Host` gets 400 on both `/` and `/console/sessions`;
  `localhost:8080`, `127.0.0.1:8080` and `[::1]:8080` pass; with `allow_remote=True` a
  foreign Host passes.

No e2e change: the console e2e drives api mode.

## Out of scope (tracked in #245)

Unbounded per-token and title caches, the rail memo's serialization cost, and the
JS/Python truncation split.
