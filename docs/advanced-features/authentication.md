# Authentication & Authorization

## Overview

Flux supports opt-in authentication and authorization. When no auth provider is enabled, all API requests succeed without credentials. When any provider is enabled, every request must carry valid credentials.

Two primitives underpin the system:

- **Principals registry** — unified store for users and service accounts with RBAC
- **Execution tokens** — server-minted, HMAC-signed, execution-bound JWTs for worker callbacks

Flux acts as a **resource server** only. It validates credentials from external IdPs but never issues long-lived user tokens.

## Principals

A **principal** is anything that can be an actor in Flux authorization. Users and service accounts are stored in one `principals` table keyed by `(subject, external_issuer)`.

```
principals:
  id              UUID PK
  type            user | service_account
  subject         TEXT  -- OIDC sub for users; chosen name for SAs
  external_issuer TEXT  -- OIDC issuer URL or sentinel "flux"
  display_name    TEXT
  enabled         BOOLEAN
  metadata        JSON  -- IdP claims (informational, refreshed on login)
  created_at      TIMESTAMP
  updated_at      TIMESTAMP
  last_seen_at    TIMESTAMP
  UNIQUE(subject, external_issuer)
```

**Key properties:**

- SAs use `external_issuer = "flux"`. Only SA principals can hold API keys.
- `enabled = false` provides soft revocation. Disabled principals cannot authenticate even if their external credentials are valid.
- `metadata` stores display-oriented claims (`name`, `given_name`, etc.). Email is not stored — the IdP remains the source of truth.
- `last_seen_at` is updated on each successful authentication.

### Role assignments

```
principal_roles:
  principal_id    FK → principals(id)
  role_name       FK → roles(name)
  assigned_at     TIMESTAMP
  assigned_by     TEXT  -- audit trail
  PRIMARY KEY(principal_id, role_name)
```

### API keys

```
api_keys:
  id              UUID PK
  principal_id    FK → principals(id)
  name            TEXT
  key_hash        TEXT  -- SHA-256
  key_prefix      TEXT
  expires_at      TIMESTAMP NULL
  UNIQUE(principal_id, name)
```

## Auto-provisioning

OIDC users are auto-provisioned on first login. When a valid JWT arrives and no matching principal exists, Flux creates one with `type=user` and assigns `default_user_roles` from config.

```toml
[flux.security.auth]
default_user_roles = ["viewer"]
```

Subsequent logins update `last_seen_at` and refresh `metadata` (display name, locale, etc.) but do not change roles. Roles are managed exclusively via the principals registry.

### Worker service principals

Workers are auto-provisioned as service account principals when they register. The registration flow:

1. Worker sends `POST /workers/register` with the bootstrap token
2. Server validates the bootstrap token and registers the worker
3. Server creates (or finds) a service account principal with `subject=<worker-name>` and `external_issuer="flux"`
4. Server assigns the `worker` role and generates an API key
5. Worker receives the API key as its `session_token` and uses it for all subsequent calls

The `worker` role grants:

| Permission | Purpose |
|-----------|---------|
| `worker:*:*` | All worker-specific endpoints (pong, connect, claim, checkpoint, progress) |
| `config:*:read` | Read agent configs at runtime |
| `admin:secrets:read` | Read secrets for MCP auth |
| `execution:*:read` | Read execution state |

**Name binding:** Each worker endpoint verifies that the authenticated principal's subject matches the worker name in the URL path. Worker A cannot access `/workers/worker-B/pong`.

**Eviction:** When the heartbeat reaper evicts a worker, its API key is revoked. When the worker reconnects and gets a 401, it re-registers with the bootstrap token, which provisions a fresh API key.

**Auth-disabled mode:** When auth is disabled, worker endpoints are unprotected (consistent with all other endpoints). The name-binding check is skipped.

## RBAC

Flux enforces RBAC at API and task level. Roles are collections of permissions.

### Built-in roles

| Role | Permissions |
|------|-------------|
| `admin` | `*` — full access |
| `operator` | Run and manage workflows, schedules, executions, agents, hooks |
| `viewer` | Read-only access (including hooks and their deliveries) |
| `worker` | Worker endpoints, read configs/secrets/executions |

### Permission format

Workflow permissions are 4-segment:

```
workflow:{namespace}:{name}:{action}
```

Other resources remain 3-segment (`resource:name:action`).

| Permission | Grants |
|-----------|--------|
| `workflow:*:*:run` | Run any workflow in any namespace |
| `workflow:default:report:run` | Run `report` in the `default` namespace |
| `workflow:billing:invoice:run` | Run `invoice` in `billing` |
| `workflow:billing:*` | Any action on any workflow in `billing` |
| `schedule:*:manage` | Create, update, delete any schedule |
| `principal:payroll-sa:impersonate` | Bind a schedule to the `payroll-sa` service account |
| `principal:*:impersonate` | Bind a schedule to any service account |
| `hook:*:create` | Create any outbound hook |
| `hook:notify-approvals:update` | Update (and test-fire) the `notify-approvals` hook |
| `hook:deliveries:read` | Read any hook's delivery history |
| `hook:deliveries:retry` | Hand a dead-lettered delivery back to the drain |
| `admin:secrets:manage` | Create and delete secrets |
| `admin:roles:manage` | Manage roles |
| `admin:principals:manage` | Manage principals and API keys |

### Running a schedule as a service account

A schedule stores `run_as_service_account`, and at trigger time the workflow
runs with **that account's** roles rather than the creator's. Choosing it is
therefore impersonation, and needs `principal:{subject}:impersonate` in
addition to `schedule:*:manage`.

```bash
flux roles update release-manager \
  --add-permissions "principal:deploy-sa:impersonate"
```

Grant `principal:*:impersonate` to allow any service account. No built-in role
carries it except `admin` (through its `*`), so an `operator` upgrading from
before this was enforced must be granted it explicitly before it can create or
rebind schedules.

### Running a hook's target as a principal

An [outbound hook](hooks.md) stores the `principal` it starts its target
workflow as — a service account named by **subject**, exactly as a schedule
names `run_as_service_account`. That principal must hold run permission on
the target, and the check runs at create/update **and** again at fire time,
so a role revoked after the hook was created dead-letters its deliveries
rather than silently bypassing RBAC.

Acting through that principal is impersonation, so it needs
`principal:{subject}:impersonate` on top of the hook permission — the same
grant, and the same reasoning, as binding a schedule:

```bash
flux roles update incident-operator \
  --add-permissions "principal:ops-sa:impersonate"
```

The rule is one sentence — *anything that can cause an execution to run as
the hook's principal requires the right to borrow it; disabling and deleting
never can* — which sorts the routes like this:

| Action | Needs |
|---|---|
| Create a hook bound to `ops-sa` | `hook:*:create` + `principal:ops-sa:impersonate` |
| Rebind a hook to another principal | `hook:{name}:update` + impersonate on the **new** principal |
| Re-aim a hook (`--workflow`, `--on`) | `hook:{name}:update` + impersonate on the principal it already carries |
| Re-enable a disabled hook | `hook:{name}:update` + impersonate |
| Test-fire a hook | `hook:{name}:update` + impersonate |
| Retry a dead-lettered delivery | `hook:deliveries:retry` + impersonate |
| Disable a hook, change `--max-attempts` | `hook:{name}:update` alone |
| Read hooks and deliveries, delete a hook | the read/delete permission alone |

Re-aiming counts because pointing an existing hook at another workflow runs
that stored principal against a target the caller chose, and a retry counts
because the next scheduler tick fires it under that principal. Disabling and
deleting deliberately do not, so a misbehaving hook can always be stopped.

`hook:*:create` therefore sits above `workflow:*:register`: a hook feeds
engine events into another workflow under a stored principal, so
`workflow:register` alone must not mint one. `operator` carries the full
hook surface but no impersonate grant, so an operator must be granted one
explicitly before it can create, re-aim or fire a hook.

**Wildcard rules:**

- Terminal `*` (last segment): matches any number of remaining segments. `workflow:billing:*` matches `workflow:billing:invoice:run`, etc.
- Non-terminal `*` (middle segment): matches exactly one segment. `workflow:*:*:read` matches `workflow:billing:report:read` but not deeper paths.

**Namespace-wide grants** use `workflow:{namespace}:*` or `workflow:{namespace}:*:{action}`:

```bash
flux roles create billing-operator \
  --permissions "workflow:billing:*:run" \
  --permissions "workflow:billing:*:read"
```

### Custom roles

```bash
flux roles create data-pipeline \
  --permissions "workflow:default:ingest:run" \
  --permissions "workflow:default:transform:run"

flux roles clone operator --name restricted-operator
flux roles update restricted-operator --remove-permissions "schedule:*:manage"
```

### Pre-flight authorization

Before any task executes, Flux resolves the full permission set for the caller across the entire workflow call tree (including nested workflows). If any permission is missing, the execution is rejected immediately.

### Task-level authorization

Workers call back to `/executions/{exec_id}/authorize/{task_name}` before executing each task. The server re-resolves permissions from current DB state on every callback — role changes take effect immediately, even for in-flight executions.

Auth-exempt tasks skip the runtime check:

```python
@task.with_options(auth_exempt=True)
async def format_output(data: dict) -> str:
    return json.dumps(data, indent=2)
```

`auth_exempt=True` is recorded in workflow metadata as `auth_exempt_tasks`. These tasks are excluded from both pre-flight and runtime permission checks.

## Execution Tokens

An **execution token** is a server-minted, HMAC-signed JWT bound to a single workflow execution. It is the only credential a worker holds during task execution.

```json
{
  "iss": "flux-server",
  "sub": "alice@acme.com",
  "principal_issuer": "https://auth.example.com/realms/flux",
  "exec_id": "7f3c...",
  "scope": "execution",
  "iat": 1234567890,
  "exp": 1234567890 + 604800,
  "jti": "a1b2c3d4"
}
```

The server mints execution tokens when a workflow run or resume is triggered. The token is persisted with the execution record. Workers receive the token via dispatch and present it when calling the authorize endpoint.

Workers never present user JWTs. User JWTs are consumed at the API boundary and never forwarded.

### Configuration

```toml
[flux.security]
execution_token_ttl = 604800
execution_token_secret = "<generate with: openssl rand -hex 32>"
```

`execution_token_secret` is required in production. If unset, a random secret is generated per process restart (tokens from previous restarts become invalid).

## Scheduled Workflows

When auth is enabled, every schedule must specify `--run-as <subject>`. The named principal must be a service account. The scheduler mints an execution token using the SA's identity.

```bash
flux principals create svc-reports --type service_account --role operator
flux schedule create my-workflow nightly-report \
  --cron "0 2 * * *" \
  --run-as svc-reports
```

If the principal is deleted or disabled between schedule creation and trigger time, that run is skipped.

## Configuration

```toml
[flux.security.auth.oidc]
enabled = true
issuer = "https://auth.example.com"
audience = "flux-api"
jwks_cache_ttl = 3600
clock_skew = 30

[flux.security.auth.api_keys]
enabled = true

[flux.security.auth]
default_user_roles = ["viewer"]

[flux.security]
execution_token_ttl = 604800
execution_token_secret = "<openssl rand -hex 32>"
```

Environment variable equivalents:

```bash
FLUX_SECURITY__AUTH__OIDC__ENABLED=true
FLUX_SECURITY__AUTH__OIDC__ISSUER=https://auth.example.com
FLUX_SECURITY__AUTH__API_KEYS__ENABLED=true
FLUX_SECURITY__EXECUTION_TOKEN_SECRET=<secret>
```

### OIDC config reference

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Enable OIDC/JWT validation |
| `issuer` | — | OIDC issuer URL |
| `audience` | — | Expected `aud` claim |
| `jwks_cache_ttl` | `3600` | JWKS cache TTL (seconds) |
| `clock_skew` | `30` | Leeway for `exp`/`nbf` (seconds) |

### Identity provider examples

**Keycloak**

```toml
[flux.security.auth.oidc]
enabled = true
issuer = "https://keycloak.example.com/realms/flux"
audience = "flux-api"
```

**Auth0**

```toml
[flux.security.auth.oidc]
enabled = true
issuer = "https://your-tenant.auth0.com/"
audience = "https://flux.example.com/api"
```

**Okta**

```toml
[flux.security.auth.oidc]
enabled = true
issuer = "https://your-org.okta.com/oauth2/default"
audience = "api://default"
```

**Microsoft Entra ID**

```toml
[flux.security.auth.oidc]
enabled = true
issuer = "https://login.microsoftonline.com/{tenant-id}/v2.0"
audience = "api://{client-id}"
```

## CLI Reference

### Authentication

```bash
flux auth login                        # Device Authorization Grant
flux auth status                       # Show current auth status
flux auth test-token <jwt>             # Decode and validate a JWT
flux auth permissions                  # List effective permissions
flux auth permissions --workflow report
flux auth logout
```

### Roles

```bash
flux roles list [--format json]
flux roles show <name>
flux roles create <name> --permissions "workflow:*:*:run"
flux roles clone <source> --name <new>
flux roles update <name> --add-permissions "x:y:z" --remove-permissions "a:b:c"
flux roles delete <name>
```

### Principals

```bash
# List all principals
flux principals list [--type user|service_account] [--format json]

# Show a principal (smart lookup: OIDC issuer first, then "flux")
flux principals show <subject> [--type <type>] [--issuer <url>]

# Create a principal
flux principals create <subject> --type user|service_account [--role <role>]... \
  [--issuer <url>] [--display-name <name>]

# Manage roles
flux principals grant <subject> --role <role>
flux principals revoke <subject> --role <role>

# Enable/disable
flux principals enable <subject>
flux principals disable <subject>

# Delete (--force cascades API keys and roles)
flux principals delete <subject> [--force] [--yes]

# API keys (service accounts only)
flux principals create-key <subject> --key-name <name> [--expires 90d]
flux principals list-keys <subject>
flux principals revoke-key <subject> --key-name <name>
```

## API Endpoints

| Method | Path | Required Permission |
|--------|------|---------------------|
| `GET` | `/workflows` | `workflow:*:*:read` |
| `GET` | `/workflows/{namespace}/{name}` | `workflow:{namespace}:{name}:read` |
| `POST` | `/workflows/{namespace}/{name}/run` | `workflow:{namespace}:{name}:run` |
| `GET` | `/executions` | `workflow:*:*:read` |
| `GET` | `/executions/{id}` | `workflow:*:*:read` |
| `POST` | `/executions/{id}/resume` | `workflow:*:*:run` |
| `POST` | `/executions/{id}/cancel` | `workflow:*:*:run` |
| `POST` | `/executions/{id}/authorize/{task}` | exec_token (internal) |
| `GET` | `/schedules` | `schedule:*:read` |
| `POST` | `/schedules` | `schedule:*:manage` + `principal:{sa}:impersonate` |
| `PUT` | `/schedules/{id_or_name}` | `schedule:*:manage` (+ `principal:{sa}:impersonate` to rebind) |
| `DELETE` | `/schedules/{id_or_name}` | `schedule:*:manage` |
| `POST` | `/hooks` | `hook:*:create` |
| `GET` | `/hooks` | `hook:*:read` |
| `GET` | `/hooks/{name}` | `hook:{name}:read` |
| `PUT` | `/hooks/{name}` | `hook:{name}:update` |
| `DELETE` | `/hooks/{name}` | `hook:{name}:delete` |
| `POST` | `/hooks/{name}/test` | `hook:{name}:update` |
| `GET` | `/hooks/{name}/deliveries` | `hook:deliveries:read` |
| `POST` | `/hooks/{name}/deliveries/{id}/retry` | `hook:deliveries:retry` |
| `GET` | `/admin/secrets` | `admin:secrets:manage` |
| `PUT` | `/admin/secrets/{name}` | `admin:secrets:manage` |
| `DELETE` | `/admin/secrets/{name}` | `admin:secrets:manage` |
| `GET` | `/admin/roles` | `admin:roles:manage` |
| `POST` | `/admin/roles` | `admin:roles:manage` |
| `PATCH` | `/admin/roles/{name}` | `admin:roles:manage` |
| `DELETE` | `/admin/roles/{name}` | `admin:roles:manage` |
| `GET` | `/admin/principals` | `admin:principals:manage` |
| `POST` | `/admin/principals` | `admin:principals:manage` |
| `GET` | `/admin/principals/{id}` | `admin:principals:manage` |
| `PATCH` | `/admin/principals/{id}` | `admin:principals:manage` |
| `DELETE` | `/admin/principals/{id}` | `admin:principals:manage` |
| `POST` | `/admin/principals/{id}/keys` | `admin:principals:manage` |
| `DELETE` | `/admin/principals/{id}/keys/{name}` | `admin:principals:manage` |
| `POST` | `/admin/workers/join-tokens` | `admin:workers:manage` |
| `GET` | `/admin/workers/join-tokens` | `admin:workers:manage` |
| `DELETE` | `/admin/workers/join-tokens/{id}` | `admin:workers:manage` |
| `DELETE` | `/admin/workers/join-tokens?subject=` | `admin:workers:manage` |
| `POST` | `/workers/register` | bootstrap_token (see below) |
| `POST` | `/workers/{name}/pong` | `worker:*:*` |
| `GET` | `/workers/{name}/connect` | `worker:*:*` |
| `POST` | `/workers/{name}/claim/{id}` | `worker:*:*` |
| `POST` | `/workers/{name}/checkpoint/{id}` | `worker:*:*` |
| `POST` | `/workers/{name}/progress/{id}` | `worker:*:*` |

Binding an execution to a named worker with the `X-Flux-Require-Worker`
header additionally requires `worker:{name}:target` on top of the workflow's
run permission — it concentrates load on one node and compels that node to
run the code, which running the workflow alone does not authorize. The grant
is per worker, so `worker:build-7:target` does not permit binding to another.
`worker:*:*` (held by the `worker` role, and by `admin` via `*`) satisfies it
for any worker. The advisory `X-Flux-Preferred-Worker` needs no grant: it
cannot force placement. See
[Dynamic Routing](dynamic-routing.md#binding-an-execution-to-one-worker).

### Revoking join tokens

Minting is additive: each call produces another independently valid token, and
before revocation existed a token left live by a failed bring-up stayed
claimable until its TTL with no way to see or retire it (issue #197).

```bash
flux server join-tokens                          # what is outstanding
flux server revoke-join-token --id <id>          # retire one
flux server revoke-join-token --subject worker-7 # retire every token for a worker
```

Revoking by subject is the shape that pairs with `flux principals ban`: the
caller already knows the worker name and does not track token ids. **Unbound
tokens are never matched by `--subject`** — they carry no subject, so retiring
them under one worker's name would take out credentials meant for others.

Revocation is a soft delete, so the row keeps who minted it and when. Dead
rows — used, revoked, or never claimed — are purged hourly by the scheduler
once `[flux.workers] join_token_retention` (default one day) has passed since
expiry; set it to 0 to keep every row forever. The listing never returns the
token or its hash: the plaintext is unrecoverable by design, and the hash is
credential-equivalent to an offline guesser.

Note that banning a principal is already a complete control on its own: worker
registration refuses a banned principal regardless of credential, so a token
minted before the ban cannot register. Revocation is for the operational case —
retiring credentials you no longer intend to use.

### Worker bootstrap token

`POST /workers/register` is gated by a long-lived shared secret rather than the auth-service permission system. Resolution order on the server:

1. `FLUX_WORKERS__BOOTSTRAP_TOKEN` env var, or `[flux.workers] bootstrap_token` in flux.toml.
2. Persisted file at `<home>/bootstrap-token` (mode 0600).
3. Auto-generated on first server start, persisted to the path above, and logged at WARNING level.

Retrieve the active token with `flux server bootstrap-token` (run on the server host). Force regeneration of the persisted file-backed token with `flux server bootstrap-token --rotate`; the running server reads the token once at startup, so you must **restart the server** for the rotated value to take effect, and existing workers will need to re-register with the new token. If `FLUX_WORKERS__BOOTSTRAP_TOKEN` or `[flux.workers] bootstrap_token` is set, that configured value still wins over the rotated file until removed. Workers must be supplied an explicit token via env var, config, or CLI flag; auto-generation is server-only because workers typically run on different hosts. The server compares submitted tokens with `hmac.compare_digest`.

## Dev Environment

The Docker Compose setup includes a pre-configured Keycloak instance. See [DOCKER.md](../../DOCKER.md#authentication-dev-environment) for setup.

### Pre-seeded users

| User | Password | Role |
|------|----------|------|
| `admin@local` | `admin` | admin |
| `operator@local` | `operator` | operator |
| `viewer@local` | `viewer` | viewer |

### Getting a test token

```bash
TOKEN=$(curl -s -X POST \
  http://localhost:8080/realms/flux/protocol/openid-connect/token \
  -d "grant_type=password&client_id=flux-api&username=admin@local&password=admin" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

flux auth test-token "$TOKEN"
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/workflows
```

### Flux config for dev

```toml
[flux.security.auth.oidc]
enabled = true
issuer = "http://localhost:8080/realms/flux"
audience = "flux-api"
clock_skew = 60

[flux.security.auth]
default_user_roles = ["viewer"]
```
