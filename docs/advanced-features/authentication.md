# Authentication & Authorization

Flux supports opt-in authentication and authorization. When no auth provider is enabled, Flux operates with full access — all API requests succeed without credentials. This preserves backward compatibility for local development and trusted environments. When an auth provider is enabled, every API request must carry valid credentials.

## Overview

Flux acts as a **resource server** — it validates credentials and enforces access control but never issues tokens. Identity providers remain entirely external.

Two complementary auth mechanisms are supported:

- **OIDC/OAuth 2.0**: Validate JWTs from any standards-compliant identity provider
- **API Keys**: Service-to-machine authentication with hashed keys tied to service accounts

Both can be enabled simultaneously. A request authenticated by either mechanism receives the same RBAC treatment.

## Configuration

Add an `[flux.security.auth]` section to your `flux.toml`:

```toml
[flux.security.auth.oidc]
enabled = true
issuer = "https://auth.example.com"
audience = "flux-api"
roles_claim = "roles"
jwks_cache_ttl = 3600
clock_skew = 30

[flux.security.auth.api_keys]
enabled = true
```

Or use environment variables:

```bash
FLUX_SECURITY__AUTH__OIDC__ENABLED=true
FLUX_SECURITY__AUTH__OIDC__ISSUER=https://auth.example.com
FLUX_SECURITY__AUTH__OIDC__AUDIENCE=flux-api
FLUX_SECURITY__AUTH__API_KEYS__ENABLED=true
```

### OIDC Configuration Reference

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Enable OIDC/JWT validation |
| `issuer` | — | OIDC issuer URL; JWKS fetched from `{issuer}/.well-known/openid-configuration` |
| `audience` | — | Expected `aud` claim value |
| `roles_claim` | `"roles"` | JWT claim containing the user's roles |
| `jwks_cache_ttl` | `3600` | How long to cache JWKS keys (seconds) |
| `clock_skew` | `30` | Allowable clock skew for `exp`/`nbf` validation (seconds) |

### API Keys Configuration Reference

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Enable API key authentication |

### Identity Provider Examples

**Keycloak**

```toml
[flux.security.auth.oidc]
enabled = true
issuer = "https://keycloak.example.com/realms/flux"
audience = "flux-api"
roles_claim = "realm_access.roles"
```

**Auth0**

```toml
[flux.security.auth.oidc]
enabled = true
issuer = "https://your-tenant.auth0.com/"
audience = "https://flux.example.com/api"
roles_claim = "https://flux.example.com/roles"
```

**Okta**

```toml
[flux.security.auth.oidc]
enabled = true
issuer = "https://your-org.okta.com/oauth2/default"
audience = "api://default"
roles_claim = "groups"
```

**Microsoft Entra ID**

```toml
[flux.security.auth.oidc]
enabled = true
issuer = "https://login.microsoftonline.com/{tenant-id}/v2.0"
audience = "api://{client-id}"
roles_claim = "roles"
```

## Role-Based Access Control

Flux enforces RBAC at the API and task level. Roles are collections of permissions. Every authenticated principal must have at least one role.

### Built-in Roles

| Role | Permissions |
|------|-------------|
| `admin` | `*` — full access to everything |
| `operator` | Run and manage workflows, schedules, and executions |
| `viewer` | Read-only access to workflows, executions, and schedules |

### Permission Format

Permissions follow a colon-separated path format:

```
resource:name:action
```

Examples:

| Permission | Grants |
|-----------|--------|
| `workflow:*:run` | Run any workflow |
| `workflow:my-workflow:run` | Run `my-workflow` specifically |
| `workflow:*:read` | Read any workflow definition |
| `workflow:my-workflow:task:process:execute` | Execute task `process` in `my-workflow` |
| `schedule:*:manage` | Create, update, and delete any schedule |
| `admin:secrets:manage` | Create and delete secrets |
| `admin:roles:manage` | Create, update, and delete roles |
| `admin:service-accounts:manage` | Manage service accounts and their API keys |

**Wildcard matching** uses two distinct rules depending on position:

- **Terminal `*` (last segment)**: matches any number of remaining segments. Example: `workflow:report:*` matches `workflow:report:run`, `workflow:report:task:load:execute`, and any other path starting with `workflow:report:`. `*` alone grants unrestricted access (admin).
- **Non-terminal `*` (middle segment)**: matches exactly one segment. Example: `workflow:*:read` matches `workflow:report:read` but **not** `workflow:report:sub:read` (two segments after `workflow:`).

### Custom Roles

Roles beyond the three built-in ones can be created via the CLI or API:

```bash
flux roles create data-pipeline \
  --permissions "workflow:ingest:run" \
  --permissions "workflow:transform:run" \
  --permissions "workflow:ingest:read" \
  --permissions "workflow:transform:read"
```

Roles can be cloned from an existing role and modified:

```bash
flux roles clone operator --name restricted-operator
flux roles update restricted-operator \
  --remove-permissions "schedule:*:manage"
```

## Name-Derived Permissions

Flux automatically derives the required permissions from workflow and task names. No manual annotation is needed in workflow code — permission names come directly from the `@workflow` decorator name and the `@task` decorator name.

A workflow named `process-orders` with a task named `validate` produces these permissions:

- `workflow:process-orders:run` — required to start an execution
- `workflow:process-orders:read` — required to read the workflow definition or execution events
- `workflow:process-orders:task:validate:execute` — checked at runtime before `validate` runs

### Nested Workflow Resolution

When a workflow calls sub-workflows, Flux resolves permissions for the entire call tree before execution begins. The caller must hold `run` permission for the top-level workflow and each nested workflow, plus `execute` permission for every task in each workflow.

```python
@workflow
async def pipeline(ctx: ExecutionContext):
    await ingest(ctx)        # requires workflow:ingest:run
    await transform(ctx)     # requires workflow:transform:run
```

The caller of `pipeline` must have:

- `workflow:pipeline:run`
- `workflow:ingest:run`
- `workflow:transform:run`
- All task-level permissions within each workflow

### Pre-flight Authorization

Flux validates the full permission set before any task executes. If the caller lacks any permission in the call tree, the execution is rejected immediately with an authorization error rather than failing mid-run.

## Task-Level Authorization

Every task is also authorized at runtime when the worker is about to execute it. This provides defense in depth — the pre-flight check catches most cases, but per-task checks ensure that dynamic or conditionally executed tasks are also protected.

### Exempting Utility Tasks

Tasks that perform purely internal work (logging helpers, formatters, in-process computations) can opt out of authorization checks:

```python
@task(auth_exempt=True)
async def format_output(data: dict) -> str:
    return json.dumps(data, indent=2)
```

`auth_exempt=True` removes the runtime authorization check for that task. Pre-flight authorization is not affected — the task simply will not require an `execute` permission entry.

### Worker Authorization Callback

Workers call back to the Flux server to authorize each task before executing it. The server evaluates the stored identity of the execution (captured at run time) against the task's derived permission. Workers never perform local authorization decisions.

## Service Accounts & API Keys

Service accounts represent machine identities — CI pipelines, workers, external integrations. Each service account holds one or more roles and can generate multiple API keys.

### Creating a Service Account

```bash
flux service-accounts create ci-pipeline --roles operator
```

### Generating an API Key

```bash
flux service-accounts create-key ci-pipeline \
  --key-name "github-actions" \
  --expires 90d
```

The key is displayed **once** and never stored in plaintext. Flux stores a SHA-256 hash of the key. Copy the key immediately — it cannot be retrieved later.

```
Key created: flux_sk_a3f9d2e1b4c8...
Store this key securely. It will not be shown again.
```

### Key Expiry

Keys accept a `--expires` duration in the `Nd` format (`30d`, `90d`, `365d`). Keys without an expiry are valid indefinitely. Expired keys are rejected at validation time and can be pruned with `revoke-key`.

### Using an API Key

Include the key in the `Authorization` header with the `Bearer` scheme:

```bash
curl -H "Authorization: Bearer flux_sk_a3f9d2e1b4c8..." \
  http://localhost:8000/workflows
```

### Listing and Revoking Keys

```bash
flux service-accounts list-keys ci-pipeline
flux service-accounts revoke-key ci-pipeline --key-name "github-actions"
```

Revoked keys are deleted immediately. In-flight requests using the revoked key will fail at their next server interaction.

## Identity on Events

Workflow-level lifecycle events (scheduled, claimed, started, completed, failed, paused, resumed, cancelled) carry the subject identifier of the principal who triggered that event. This means different users can act on different lifecycle events of the same execution. Individual task events do not carry a subject.

```
execution started by: alice@example.com
execution resumed by: bob@example.com
execution cancelled by: alice@example.com
```

The event log provides a full audit trail: who ran, paused, resumed, or cancelled each execution, and when.

## API Endpoints Reference

| Method | Path | Required Permission |
|--------|------|---------------------|
| `GET` | `/workflows` | `workflow:*:read` |
| `GET` | `/workflows/{name}` | `workflow:{name}:read` |
| `POST` | `/workflows/{name}/run` | `workflow:{name}:run` |
| `POST` | `/workflows/{name}/run/async` | `workflow:{name}:run` |
| `GET` | `/executions` | `workflow:*:read` |
| `GET` | `/executions/{id}` | `workflow:*:read` |
| `GET` | `/executions/{id}/events` | `workflow:*:read` |
| `POST` | `/executions/{id}/resume` | `workflow:*:run` |
| `POST` | `/executions/{id}/cancel` | `workflow:*:run` |
| `GET` | `/schedules` | `schedule:*:read` |
| `POST` | `/schedules` | `schedule:*:manage` |
| `PUT` | `/schedules/{name}` | `schedule:*:manage` |
| `DELETE` | `/schedules/{name}` | `schedule:*:manage` |
| `GET` | `/admin/secrets` | `admin:secrets:manage` |
| `PUT` | `/admin/secrets/{name}` | `admin:secrets:manage` |
| `DELETE` | `/admin/secrets/{name}` | `admin:secrets:manage` |
| `GET` | `/admin/roles` | `admin:roles:manage` |
| `POST` | `/admin/roles` | `admin:roles:manage` |
| `PUT` | `/admin/roles/{name}` | `admin:roles:manage` |
| `DELETE` | `/admin/roles/{name}` | `admin:roles:manage` |
| `GET` | `/admin/service-accounts` | `admin:service-accounts:manage` |
| `POST` | `/admin/service-accounts` | `admin:service-accounts:manage` |
| `POST` | `/admin/service-accounts/{name}/keys` | `admin:service-accounts:manage` |
| `DELETE` | `/admin/service-accounts/{name}/keys/{key}` | `admin:service-accounts:manage` |

## CLI Reference

### Authentication

```bash
# Check current auth status (token, expiry, identity)
flux auth status

# Validate a JWT against the configured OIDC provider
flux auth test-token <jwt>

# List effective permissions for the current identity
flux auth permissions

# Filter permissions to a specific workflow
flux auth permissions --workflow my-workflow

# Output permissions as JSON
flux auth permissions --format json

# Log out (clear stored credentials)
flux auth logout
```

`flux auth login` supports the Device Authorization Grant for CLI authentication.

### Roles

```bash
# List all roles
flux roles list
flux roles list --format json

# Show a role and its permissions
flux roles show operator

# Create a custom role
flux roles create data-engineer \
  --permissions "workflow:*:run" \
  --permissions "workflow:*:read" \
  --permissions "schedule:*:read"

# Clone a role and modify it
flux roles clone operator --name limited-operator
flux roles update limited-operator \
  --add-permissions "workflow:reports:run" \
  --remove-permissions "schedule:*:manage"

# Delete a role (fails if any principal still holds the role)
flux roles delete limited-operator
```

### Service Accounts

```bash
# List service accounts
flux service-accounts list

# Create a service account with a role
flux service-accounts create ci-pipeline --roles operator

# Generate an API key (shown once)
flux service-accounts create-key ci-pipeline \
  --key-name "github-actions" \
  --expires 90d

# List keys for a service account (names and expiry only; values never shown)
flux service-accounts list-keys ci-pipeline

# Revoke a key
flux service-accounts revoke-key ci-pipeline --key-name "github-actions"
```

## Dev Environment

The Docker Compose setup includes a pre-configured Keycloak instance for local OIDC development. See [Authentication Dev Environment](../../DOCKER.md#authentication-dev-environment) in the Docker documentation for setup instructions.

### Pre-seeded Users

The `flux` realm ships with three users covering each built-in role:

| User | Password | Role |
|------|----------|------|
| `admin@local` | `admin` | admin |
| `operator@local` | `operator` | operator |
| `viewer@local` | `viewer` | viewer |

### Getting a Test Token

```bash
TOKEN=$(curl -s -X POST \
  http://localhost:8080/realms/flux/protocol/openid-connect/token \
  -d "grant_type=password&client_id=flux-api&username=admin@local&password=admin" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Inspect the token
flux auth test-token "$TOKEN"

# Use the token with Flux
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/workflows
```

### Flux Configuration for Dev

```toml
[flux.security.auth.oidc]
enabled = true
issuer = "http://localhost:8080/realms/flux"
audience = "flux-api"
roles_claim = "realm_access.roles"
clock_skew = 60
```
