# Workflow Namespaces

Namespaces organize workflows into logical groups. A workflow's full identity is `(namespace, name)`, so two workflows may share a short name if they live in different namespaces. Namespaces map naturally to teams, services, or domains — for example, `billing`, `reporting`, or `data-ingestion`.

## Declaring a namespace

Pass `namespace` to `@workflow.with_options`:

```python
from flux import workflow

@workflow.with_options(namespace="billing")
async def invoice(ctx):
    ...
```

Workflows without a declared namespace land in the `default` namespace.

## Namespace naming rules

- Lowercase letters, digits, `_`, and `-`
- Must start with a letter or digit
- Max 64 characters

## Referring to workflows

Qualified references use a `/` separator:

```bash
flux workflow run billing/invoice
```

The CLI, HTTP API, MCP server, and Python client all accept `namespace/name`. For backward compatibility, bare names are treated as `default/name`.

## Permissions

Permission strings are 4-segment: `workflow:{namespace}:{name}:{action}`.
Namespace-wide grants use wildcards:

- `workflow:billing:*:run` — run any workflow in `billing`
- `workflow:billing:*` — any action on any workflow in `billing`
- `workflow:*:*:read` — read any workflow in any namespace

Non-terminal `*` matches exactly one segment; terminal `*` matches any number of remaining segments.

## Upgrade notes

- This release requires dropping and recreating your Flux database. There is no automatic migration.
- Existing permission grants must use the 4-segment form. Grants such as `workflow:hello_world:run` must become `workflow:default:hello_world:run`.
- The built-in TUI console is disabled in this release.
