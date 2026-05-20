# Security Policy

## Supported Versions

memex follows semantic versioning. Security fixes are applied to the latest
released `0.3.x` line. Older minor versions are not maintained — please upgrade
to the latest release before reporting.

| Version | Supported |
| ------- | --------- |
| 0.3.x   | ✅        |
| < 0.3   | ❌        |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report privately via one of:

- GitHub's [private vulnerability reporting](https://github.com/STiFLeR7/memex/security/advisories/new)
  (preferred — Security tab → "Report a vulnerability"), or
- Email **hillaniljppatel@gmail.com** with the subject line `memex security`.

Please include:

- the affected version (`memex --version` or the PyPI/npm version),
- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- any suggested remediation.

You can expect an acknowledgement within **5 business days**. We will keep you
informed as we investigate, and will credit you in the release notes once a fix
ships, unless you prefer to remain anonymous.

## Scope and Handling of Secrets

memex connects to a Neo4j database and the Google Gemini API, and reads its
configuration from environment variables / a repo-local `.env`:

- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`
- `GEMINI_API_KEY`

These are **secrets**. When reporting an issue, never paste real credentials
into an issue, PR, log, or email. Redact them. memex itself must never log the
contents of `.env`; if you find a code path that does, that is a reportable
vulnerability.

Other areas of interest:

- the MCP HTTP/SSE transport and its bearer-token authentication,
- the watcher daemon's API-key registry,
- the memory-tool filesystem path validation (path-traversal / protection
  bypass),
- any Cypher query that interpolates untrusted input.

## Out of Scope

- Vulnerabilities in third-party dependencies (Neo4j, Graphiti, the Gemini SDK,
  tree-sitter) — report those to the respective projects. We will, however,
  bump or pin dependencies promptly once an upstream advisory is published.
- Issues that require a pre-compromised host or already-leaked credentials.
