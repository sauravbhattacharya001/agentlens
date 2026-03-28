# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| latest  | ✅ Yes             |
| older   | ❌ No              |

Only the latest release receives security updates. We recommend always running the most recent version.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them privately via one of the following:

1. **GitHub Security Advisories** — Use the "Report a vulnerability" button on the [Security tab](https://github.com/sauravbhattacharya001/agentlens/security/advisories/new) of this repository. This is the preferred method.

2. **Email** — If you cannot use GitHub advisories, email the maintainer directly with details of the vulnerability.

### What to include

- Description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Affected versions (if known)
- Suggested fix (if any)

### What to expect

- **Acknowledgment** within 48 hours of your report
- **Status update** within 7 days with an assessment and timeline
- **Fix timeline** — Critical vulnerabilities will be patched as soon as possible; non-critical issues will be addressed in the next release cycle

We appreciate responsible disclosure and will credit reporters (unless they prefer to remain anonymous) in the release notes.

## Security Best Practices for Deployment

AgentLens is an observability tool that stores session traces and analytics data. When deploying:

- **API key authentication** — Always configure API keys for production deployments. AgentLens supports `X-API-Key` header authentication.
- **Network isolation** — Run AgentLens behind a reverse proxy or within a private network. Do not expose it directly to the public internet without authentication.
- **Database security** — The SQLite database contains session traces that may include sensitive data (prompts, tool calls, outputs). Restrict file permissions appropriately.
- **HTTPS** — Use TLS termination at your reverse proxy to encrypt data in transit.
- **Rate limiting** — AgentLens includes built-in rate limiting, but configure your reverse proxy's rate limits as an additional layer.
- **CORS** — Configure the `CORS_ORIGIN` environment variable to restrict cross-origin access to trusted domains only.
- **Environment variables** — Never commit API keys or secrets. Use environment variables or a secrets manager.

## Dependencies

We monitor dependencies for known vulnerabilities using:
- GitHub Dependabot for automated dependency updates
- CodeQL for static analysis
