# Security Policy

Patcha is a local-first tool: it continuously observes activity on your machine
(browser history, shell history, git, and on-screen text) and stores it in a
local database. Security issues here can expose sensitive personal data, so we
take them seriously.

## Supported Versions

Patcha is pre-1.0 and moves quickly. Only the latest release on the `main`
branch receives security fixes.

| Version        | Supported |
| -------------- | --------- |
| latest `main`  | ✅        |
| older releases | ❌        |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, report privately using
[GitHub Security Advisories](https://github.com/xtanion/patcha/security/advisories/new),
or email the maintainers at **anandshivam54321@gmail.com** with:

- a description of the issue and its impact,
- steps to reproduce (a proof of concept is ideal), and
- any affected versions or configuration.

You can expect an acknowledgement within **5 business days**. We will keep you
updated on our assessment and, once a fix is available, coordinate a disclosure
timeline with you. We are happy to credit reporters unless you prefer to remain
anonymous.

## Scope and data handling

Patcha stores all collected data locally under `~/.patcha` (a SQLite database
plus JSONL logs) and never uploads raw activity by default. When you sign in to
patcha-api or configure the Claude CLI backend, text prompts derived from your
activity are sent to that service for summarization — no login means everything
stays on device. Please keep this in mind when reporting: issues that leak the
local store, defeat the on-device boundary, or exfiltrate data are in scope.
