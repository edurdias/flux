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
| `operator` | Run and manage workflows, schedules, executions |
| `viewer` | Read-only access |
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
| `admin:secrets:manage` | Create and delete secrets |
| `admin:roles:manage` | Manage roles |
| `admin:principals:manage` | Manage principals and API keys |

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
| `POST` | `/schedules` | `schedule:*:manage` |
| `PUT` | `/schedules/{name}` | `schedule:*:manage` |
| `DELETE` | `/schedules/{name}` | `schedule:*:manage` |
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
| `POST` | `/workers/register` | bootstrap_token |
| `POST` | `/workers/{name}/pong` | `worker:*:*` |
| `GET` | `/workers/{name}/connect` | `worker:*:*` |
| `POST` | `/workers/{name}/claim/{id}` | `worker:*:*` |
| `POST` | `/workers/{name}/checkpoint/{id}` | `worker:*:*` |
| `POST` | `/workers/{name}/progress/{id}` | `worker:*:*` |

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
