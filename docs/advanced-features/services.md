# Workflow Services

Workflow services expose Flux workflows as REST microservice endpoints. A service maps a name to a set of workflows — each workflow becomes an HTTP endpoint where the request body is the input and the response body is the output.

Services resolve their selectors dynamically. Registering a new workflow in a namespace that a service selects automatically makes it available as an endpoint — no restart or reconfiguration needed.

## Quick Start

```bash
# Register some billing workflows
flux workflow register billing_workflows.py

# Create a service that exposes all billing workflows
flux service create billing --namespace billing

# Call a workflow through the service
curl -X POST http://localhost:8000/services/billing/invoice \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "C-100", "amount": 99.50}'

# Response (raw workflow output):
# {"invoice_id": "INV-123", "total": 99.50}
```

## Service Definition

A service has:

| Field | Description |
|---|---|
| **name** | Unique identifier used in URL paths. Lowercase alphanumeric, hyphens, underscores. |
| **namespaces** | Include all workflows from these namespaces. |
| **workflows** | Include specific workflows by qualified ref (`namespace/name`). |
| **exclusions** | Exclude specific workflows. Applied after includes. |

### Selectors

Selectors determine which workflows are part of a service. There are three types:

**Namespace selectors** include all workflows registered in a namespace:

```bash
flux service create billing --namespace billing
# All workflows in the "billing" namespace become endpoints
```

**Workflow selectors** include specific workflows by qualified reference:

```bash
flux service create reporting \
  --workflow billing/monthly_report \
  --workflow analytics/dashboard
# Only these two workflows become endpoints
```

**Exclusions** remove specific workflows from the resolved set:

```bash
flux service create billing \
  --namespace billing \
  --exclude billing/internal_cleanup
# All billing workflows except internal_cleanup
```

Selectors can be combined. Resolution order: collect from namespaces, add individual workflows, remove exclusions.

### Dynamic Resolution

Selectors are resolved against the workflow catalog at request time. This means:

- **New workflows appear automatically.** Register a workflow in the `billing` namespace and it's immediately available through any service that selects `billing`.
- **Deleted workflows disappear.** Delete a workflow and requests to its endpoint return 404.
- **No restart needed.** The service definition stays the same; the catalog is the source of truth.

### Name Collisions

If two workflows from different namespaces share the same name (e.g. `billing/process` and `analytics/process`), the service detects the collision and returns **409 Conflict**. Resolve collisions by excluding one side:

```bash
flux service create combined \
  --namespace billing \
  --namespace analytics \
  --exclude analytics/process
```

## CLI Commands

### Create a service

```bash
flux service create <name> [--namespace NS]... [--workflow REF]... [--exclude REF]...

# Examples:
flux service create billing --namespace billing
flux service create billing --namespace billing --exclude billing/internal
flux service create reporting --workflow billing/report --workflow analytics/dash
```

### Modify selectors

```bash
# Add namespace or workflow selectors
flux service add billing --namespace payments
flux service add billing --workflow analytics/report

# Remove selectors
flux service remove billing --namespace payments

# Exclude a workflow
flux service exclude billing billing/debug_tool

# Un-exclude a workflow
flux service include billing billing/debug_tool
```

### Inspect

```bash
# List all services
flux service list

# Show service details and resolved endpoints
flux service show billing
```

`flux service show` displays the selectors and the currently resolved endpoints:

```
Service: billing
  Namespaces: billing, payments
  Exclusions: billing/internal

  Resolved endpoints (4):
    /invoice → billing/invoice (v3)
    /refund → billing/refund (v1)
    /process_payment → payments/process_payment (v2)
    /receipt → billing/receipt (v1)
```

### Delete

```bash
flux service delete billing --yes
```

### Start standalone process

```bash
flux service start billing --port 9000 --server-url http://localhost:8000
```

See [Standalone Mode](#standalone-mode) below.

All commands accept `--format json` for machine-readable output and `--server-url` to target a specific Flux server.

## HTTP Endpoints

### Built-in endpoints (on the main Flux server)

```
POST /services/{service}/{workflow}          Run workflow (sync by default)
POST /services/{service}/{workflow}/{mode}   Run with explicit mode (sync/async/stream)
POST /services/{service}/{workflow}/resume/{execution_id}  Resume paused workflow
GET  /services/{service}/{workflow}/status/{execution_id}  Check execution status
```

### Request format

- **Method:** POST
- **Body:** JSON workflow input
- **Query parameters:**
  - `detailed=true` — include execution metadata in response
  - `version=N` — pin to a specific workflow version

### Response format

**Standard response** (sync run/resume, completed):

Returns the raw workflow output directly:

```json
{"invoice_id": "INV-123", "total": 99.50}
```

**Detailed response** (`?detailed=true`, or always for status/pause/async):

```json
{
  "execution_id": "abc123def",
  "state": "COMPLETED",
  "output": {"invoice_id": "INV-123", "total": 99.50},
  "namespace": "billing",
  "workflow": "invoice"
}
```

**Async response** (`POST /services/billing/invoice/async`):

```json
{
  "execution_id": "abc123def",
  "state": "CREATED",
  "output": null,
  "namespace": "billing",
  "workflow": "invoice",
  "status_url": "/services/billing/invoice/status/abc123def"
}
```

**Paused response** (workflow called `pause()`):

```json
{
  "execution_id": "abc123def",
  "state": "PAUSED",
  "output": null,
  "namespace": "billing",
  "workflow": "invoice",
  "resume_url": "/services/billing/invoice/resume/abc123def"
}
```

### HTTP status codes

| Scenario | Status | Description |
|---|---|---|
| Workflow completed | **200** | Output in body |
| Async accepted | **202** | Execution started, poll status_url |
| Workflow paused | **202** | Needs resume, use resume_url |
| Workflow failed | **500** | Error details in body |
| Workflow not in service | **404** | Endpoint doesn't match any selector |
| Service not found | **404** | No service with that name |
| Name collision | **409** | Ambiguous name across namespaces |

### Examples

```bash
# Sync execution (default)
curl -X POST http://localhost:8000/services/billing/invoice \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "C-100"}'

# Async execution
curl -X POST http://localhost:8000/services/billing/invoice/async \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "C-100"}'

# Check status
curl http://localhost:8000/services/billing/invoice/status/abc123def

# Resume paused workflow
curl -X POST http://localhost:8000/services/billing/invoice/resume/abc123def \
  -H "Content-Type: application/json" \
  -d '{"approved": true}'

# Detailed response
curl -X POST "http://localhost:8000/services/billing/invoice?detailed=true" \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "C-100"}'
```

## Standalone Mode

A standalone service process serves a single service at root-level URLs, independent of the main Flux server. The process forwards requests to the Flux server internally.

```bash
flux service start billing --port 9000 --server-url http://flux:8000
```

### Root-level endpoints

Since the process serves one service, the service name is omitted from URLs:

```
POST /{workflow}                    Run workflow
POST /{workflow}/resume/{exec_id}   Resume
GET  /{workflow}/status/{exec_id}   Status
GET  /health                        Health check
```

```bash
# Same workflow, cleaner URL:
curl -X POST http://localhost:9000/invoice \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "C-100"}'
```

### Endpoint discovery

The standalone process doesn't access the workflow catalog directly. Instead, it fetches the service's endpoint list from the Flux server and caches it:

- **Cache TTL** defaults to 60 seconds (configurable via `--cache-ttl`).
- **New workflows** are discovered on cache miss — if a request hits an unknown endpoint, the proxy forces a cache refresh.
- **Lock-protected refresh** prevents thundering herd under concurrent load.

### Health endpoint

```bash
curl http://localhost:9000/health
```

```json
{
  "status": "healthy",
  "service": "billing",
  "endpoints": 4,
  "cache_age_seconds": 23.1
}
```

### Options

```
flux service start <name> [options]

  --port PORT          Port to listen on (default: 9000)
  --host HOST          Host to bind to (default: 0.0.0.0)
  --server-url URL     Flux server URL (default: from config)
  --cache-ttl SECONDS  Endpoint cache TTL (default: 60)
```

## MCP Integration

Services can expose their workflows as [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) tools, allowing AI agents and LLM-based applications to discover and invoke workflows through the standard MCP interface.

### Enabling MCP

Enable MCP when creating or updating a service:

```bash
# On create
flux service create billing --namespace billing --mcp

# Toggle on an existing service
flux service update billing --mcp

# Disable
flux service update billing --no-mcp
```

### Built-in MCP endpoint

When MCP is enabled, the main Flux server exposes an info endpoint:

```
GET /services/{name}/mcp/tools
```

This returns the list of generated tools and the MCP connection URL. If MCP is not enabled for the service, this endpoint returns 404.

### Standalone MCP

When a standalone service process starts with MCP enabled, it mounts a full MCP server at `/mcp`:

```bash
flux service start billing --port 9000 --server-url http://flux:8000
# MCP available at http://localhost:9000/mcp
```

The `--mcp` / `--no-mcp` flags on `service start` override the stored setting. Without an explicit flag, the stored `mcp_enabled` value is used.

### MCP Authentication

Standalone MCP endpoints can validate bearer tokens from an external Identity Provider (IdP) and advertise the IdP via OAuth 2.0 Protected Resource Metadata ([RFC 9728](https://www.rfc-editor.org/rfc/rfc9728)). This enables MCP clients to discover the authorization server and obtain tokens through standard flows including Dynamic Client Registration (DCR) and PKCE.

**CLI flags:**

```bash
flux service start billing --port 9000 --mcp \
  --mcp-issuer https://idp.example.com/realms/my-realm \
  --mcp-audience billing-api \
  --mcp-jwks-uri https://idp.example.com/realms/my-realm/protocol/openid-connect/certs
```

| Flag | Description |
|---|---|
| `--mcp-issuer` | IdP issuer URL. Enables token validation and serves `/.well-known/oauth-protected-resource` pointing to this authorization server. |
| `--mcp-audience` | Expected `aud` claim in JWT tokens. Optional. |
| `--mcp-jwks-uri` | JWKS endpoint for token signature validation. Defaults to `{issuer}/.well-known/jwks.json` if omitted. |

**Fallback to Flux OIDC config:**

When no `--mcp-issuer` is provided, the service checks Flux's OIDC settings (`flux.security.auth.oidc`). If OIDC is enabled in the Flux config, the same issuer and audience are used for MCP auth automatically.

```toml
# flux.toml — MCP services inherit these settings when no --mcp-issuer is given
[flux.security.auth.oidc]
enabled = true
issuer = "https://idp.example.com/realms/my-realm"
audience = "flux-api"
```

**How it works:**

1. MCP client connects to `/mcp` without a token and receives a `401`.
2. Client fetches `/.well-known/oauth-protected-resource` from the service, which returns the `authorization_servers` list.
3. Client follows the IdP's `/.well-known/oauth-authorization-server` metadata to discover endpoints (including DCR if supported).
4. Client obtains an access token from the IdP and retries with `Authorization: Bearer <token>`.
5. The service validates the token via the IdP's JWKS and grants access.

**Supported IdPs** (any OAuth 2.0 / OIDC compliant provider):

| Provider | Example issuer |
|---|---|
| Keycloak | `https://keycloak.example.com/realms/my-realm` |
| Auth0 | `https://your-tenant.auth0.com/` |
| Okta | `https://your-org.okta.com/oauth2/default` |
| Entra ID | `https://login.microsoftonline.com/{tenant}/v2.0` |

### Tool generation

Each workflow in the service produces **5 MCP tools**:

| Tool | Description |
|---|---|
| `{name}` | Run the workflow synchronously |
| `{name}_async` | Run asynchronously (returns execution ID) |
| `resume_{name}` | Resume a paused execution synchronously |
| `resume_{name}_async` | Resume asynchronously |
| `status_{name}` | Check execution status |

### Typed parameters

If a workflow uses a Pydantic model as its input type, the generated MCP tool exposes individual parameters matching the model's fields. Workflows with untyped or `dict` inputs receive a single generic `input` parameter (JSON string).

```python
class InvoiceInput(BaseModel):
    customer_id: str
    amount: float

@workflow.with_options(namespace="billing")
async def invoice(ctx: ExecutionContext[InvoiceInput]):
    ...
```

The `invoice` tool will have `customer_id: str` and `amount: float` as explicit parameters, making it easier for AI agents to call correctly.

## Authentication

Services inherit the Flux auth system. When auth is enabled:

- Callers must authenticate (API key, OIDC token, etc.)
- The `workflow:{namespace}:{name}:run` permission is checked for the resolved workflow
- The standalone proxy forwards `Authorization` headers to the Flux server

When auth is disabled (default), service endpoints are fully open.

Service CRUD operations (`create`, `delete`, etc.) use the same permissions as other admin operations.

## Management API

The Flux server exposes REST endpoints for service management:

```
POST   /services                    Create a service
GET    /services                    List all services
GET    /services/{name}             Show service + resolved endpoints
PUT    /services/{name}             Update selectors
DELETE /services/{name}             Delete a service
```

### Create

```bash
curl -X POST http://localhost:8000/services \
  -H "Content-Type: application/json" \
  -d '{
    "name": "billing",
    "namespaces": ["billing"],
    "workflows": [],
    "exclusions": ["billing/internal"]
  }'
```

### Update

```bash
curl -X PUT http://localhost:8000/services/billing \
  -H "Content-Type: application/json" \
  -d '{
    "add_namespaces": ["payments"],
    "remove_exclusions": ["billing/internal"]
  }'
```

Update fields: `add_namespaces`, `add_workflows`, `add_exclusions`, `remove_namespaces`, `remove_workflows`, `remove_exclusions`.

## Use Cases

### Microservice facade

Expose a namespace as a REST API for external consumers:

```bash
flux service create billing-api --namespace billing
# External clients call /services/billing-api/invoice instead of learning Flux internals
```

### Cross-namespace composition

Cherry-pick workflows from multiple namespaces into one service:

```bash
flux service create reporting \
  --workflow billing/monthly_report \
  --workflow analytics/dashboard \
  --workflow data/export
```

### Independent deployment

Run a dedicated service process for isolation and independent scaling:

```bash
# On a dedicated host or container:
flux service start billing-api --port 80 --server-url http://flux-internal:8000
```

### Gradual rollout

Start with a subset of workflows, expand the service over time:

```bash
flux service create billing-api --workflow billing/invoice
# Later:
flux service add billing-api --workflow billing/refund
flux service add billing-api --workflow billing/receipt
```

## Upgrade Notes

- This release adds the `mcp_enabled` column to the `services` table. Existing deployments must recreate their database or manually add the column (`ALTER TABLE services ADD COLUMN mcp_enabled BOOLEAN NOT NULL DEFAULT 0`). There is no automatic migration.
