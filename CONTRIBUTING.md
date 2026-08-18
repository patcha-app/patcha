# Contributing to patcha

Thanks for your interest in contributing! Patcha is a local-first activity
observability tool for macOS, written in Rust (the daemon/CLI) with a native
Swift menu bar app. This guide covers how to get set up and what we expect from
contributions.

By participating, you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Ground rules

- **Never commit personal data.** Patcha collects browser history, shell
  history, git activity, and on-screen text. Your local store lives in
  `~/.patcha` and must never end up in a commit, issue, or PR. `.env` files are
  gitignored — keep it that way.
- **macOS only.** Features rely on Accessibility, Screen Recording, and Vision
  APIs. Test on macOS.
- **Discuss big changes first.** For anything beyond a bug fix, open an issue or
  discussion before writing a lot of code.

## Development setup

Prerequisites: [Rust](https://rustup.rs) (stable) and Xcode / Xcode Command Line
Tools.

```bash
git clone https://github.com/xtanion/patcha.git
cd patcha

# Build the Swift helper binaries (Accessibility, OCR, visual embedder, window)
bash build.sh

# Build and test the Rust core
cd rust
cargo build
cargo test

# Install git hooks (commit-msg lint + pre-commit test run)
cd ..
bash scripts/install-hooks.sh
```

Grant **Accessibility** and **Screen Recording** permissions to your terminal
(or the `patcha` binary) under System Settings → Privacy & Security, or the
screen collectors and their tests will not work.

## Before you open a PR

Run the same checks CI runs:

```bash
cd rust
cargo fmt --all
cargo clippy --all-targets -- -D warnings
cargo build --locked
cargo test          # some tests need a macOS GUI session + permissions
```

- `fmt`, `clippy`, and `build` run in CI on macOS.
- The full test suite runs locally via the pre-commit hook (it includes OCR /
  Accessibility tests that need a real GUI session, so it is not run headless in
  CI).

## Commit convention

Commit messages must follow `type: description`:

- `feat:` — new feature
- `fix:` — bug fix
- `chore:` — maintenance, dependencies, tooling

A scope is optional: `feat(cli): add --json flag`. The `commit-msg` hook
enforces this format.

## Pull requests

- Keep PRs focused; one logical change per PR.
- Fill out the PR template (what/why, how tested, checklist).
- Update docs under `docs/` and the README when you change behavior or
  configuration.
- Link the issue your PR addresses (`Closes #123`).

## Project layout

| Path                        | What it is                                     |
| --------------------------- | ---------------------------------------------- |
| `rust/patcha/src/collectors`| Activity collectors (browser, terminal, git, window, accessibility) |
| `rust/patcha/src/perception`| On-device screenshot captioning + visual prefilter |
| `rust/patcha/src/db`        | SQLite + sqlite-vec store, retrieval, graph    |
| `rust/patcha/src/mcp`       | MCP server and chat backends                   |
| `rust/patcha/src/cli`       | CLI subcommand implementations                 |
| `rust/patcha/src/llm`       | LLM backends (patcha-api and local Claude CLI) |
| `swift-xcode/`              | Native macOS menu bar app                      |
| `helpers/`                  | Swift helper sources compiled by `build.sh`    |
| `docs/`                     | Subsystem documentation                        |

## Reporting bugs and security issues

- **Bugs:** open a GitHub issue using the bug report template. Redact any
  personal activity data.
- **Security vulnerabilities:** do not open a public issue — follow
  [SECURITY.md](SECURITY.md).
