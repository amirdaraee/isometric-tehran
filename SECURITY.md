# Security policy

Isometric Tehran is a static website (HTML, JavaScript and image tiles on GitHub Pages) and an offline data
pipeline. It has no server, no accounts and no database, and it collects nothing about visitors: no analytics and
no error reporting are configured.

## Reporting a vulnerability

Please report security problems privately, not in a public issue:

- use GitHub's **private vulnerability reporting** on this repository (Security tab → "Report a vulnerability").

Include what you found, how to reproduce it and what it could affect. You will get an answer within a week. Once a
fix is out, you are welcome to be credited in the release notes.

## In scope

- the viewer code in `web/` and what it loads;
- the GitHub Actions workflows in `.github/workflows/`;
- secrets committed by mistake (none should exist: the repository needs no keys).

## Secrets

The project needs no API keys. Never commit a `.env` file, a token or a key; the `repo-checks` CI job looks for
them. If a secret is ever pushed, rotate it first, then remove it from the history.
