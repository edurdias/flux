# Security Policy

## Supported Versions

Flux (`flux-core`) is pre-1.0 and ships from `main`. Security fixes are applied
to the latest released version on PyPI. Please always run the most recent
release.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, report them privately using GitHub's
[private vulnerability reporting](https://github.com/edurdias/flux/security/advisories/new)
("Report a vulnerability" under the repository's *Security* tab), or by emailing
the maintainer at edurdias@gmail.com.

Please include:

- A description of the vulnerability and its impact.
- Steps to reproduce (proof-of-concept if possible).
- Affected version(s) and configuration.

We aim to acknowledge reports within a few business days and will keep you
informed as we work on a fix.

## Security Model & Hardening Notes

Flux executes user-provided workflow code by design, so the security boundary is
*who is allowed to register and run workflows*. When deploying Flux, keep the
following in mind:

- **Enable authentication.** Authentication is **disabled by default** for local
  development. In any shared or networked deployment, set
  `[flux.security.auth] enabled = true` (or `FLUX_SECURITY__AUTH__ENABLED=true`)
  and configure a provider (OIDC or API keys). With auth disabled, all requests
  are treated as an anonymous admin.
- **Registration is privileged.** Registering a workflow causes its module code
  to be executed (for schema extraction and on workers). Restrict the
  `workflow:<namespace>:*:register` permission to trusted operators.
- **Set the encryption key.** The secrets store requires
  `FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY` (generate with
  `openssl rand -hex 32`). There is no default.
- **Lock down CORS.** The server defaults to `cors_allow_origins = ["*"]` with
  credentials disabled. If you serve Flux to browsers with credentialed auth,
  set explicit origins and enable `cors_allow_credentials`.
- **Sandbox untrusted agents.** The AI shell tool runs commands through a shell;
  treat its denylist as defense-in-depth, not a boundary, and isolate the worker
  (container/seccomp) if exposing it to untrusted prompts.
