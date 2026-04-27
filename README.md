# Memographix

[![CI](https://github.com/coderalnaim/memographix/actions/workflows/ci.yml/badge.svg)](https://github.com/coderalnaim/memographix/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/memographix.svg)](https://pypi.org/project/memographix/)
[![Python](https://img.shields.io/pypi/pyversions/memographix.svg)](https://pypi.org/project/memographix/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Memographix gives AI coding agents local memory for your repo.

Install it once, run setup once, and keep using Codex, Claude, Cursor, Copilot,
Gemini, OpenCode, Aider, Windsurf, or any MCP client normally. Memographix
retrieves fresh context before work and captures useful task memory after work.

## Quick Start

Run this inside the repo where you want AI-agent memory:

```bash
pip install memographix
mgx setup
mgx savings
```

The normal PyPI install includes the CLI, local indexer, MCP server, and agent
integration support. `mgx setup` creates local state in `.memographix/`, indexes
the repo, writes an MCP server config, and installs project agent rules.

The PyPI badge above always shows the latest published version. The README does
not hard-code a version number, so release updates do not require rewriting the
install command.

## Daily Use

Use your AI agent normally.

Memographix is designed to work in the background:

- Before work, the agent asks Memographix for a small context packet.
- After useful work, the agent captures the answer with changed files, commands,
  tests, and outcome.
- If old evidence changed, Memographix marks the memory stale instead of reusing
  it silently.

Check setup health:

```bash
mgx doctor
```

If `mgx doctor` says an agent needs manual MCP configuration, use the generated
file at `.memographix/mcp.json` in that agent's MCP settings.

Control it per repo:

```bash
mgx status
mgx disable --reason "not needed here"
mgx enable
```

Disabled repos keep existing memory but automatic agent calls return no context
and save nothing. Re-enabling refreshes the index before Memographix is used
again.

See the estimated token savings:

```bash
mgx savings --since 30d
```

## Advanced CLI

Manual commands are still available for debugging and non-MCP workflows:

```bash
mgx ask "how does request routing work?" --budget 800
mgx remember --question "how does routing work?" --answer "..." --evidence app/routes.py
```

Most developers should not need the manual memory command after `mgx setup`.

## Proof

On the pinned Kubernetes benchmark, Memographix wins the repeated-task metrics it
is designed for: lower repeated tokens, faster repeated recall, stale-evidence
safety, and the best deterministic quality score. See [Benchmarks](BENCHMARKS.md)
for the public corpus, exact commands, honest losses, and unavailable-tool
notes.

## Why Developers Use It

- Stop re-explaining the same codebase across chats.
- Keep memory tied to real evidence files.
- Avoid stale answers after files change.
- Send smaller context packets to AI agents.
- Run locally without a required LLM API or cloud service.

## What It Does Not Do

- It does not upload your code.
- It does not save full chat transcripts by default.
- It does not treat stale memory as correct.
- It does not install benchmark tools or competitors in the runtime package.

## Docs

- [Benchmarks](BENCHMARKS.md): public Kubernetes results and claim policy.
- [Security](SECURITY.md): local privacy, skipped secrets, and sandbox safety.
- [Agent integrations](docs/AGENT_INTEGRATIONS.md): MCP and agent setup.
- [Architecture](docs/ARCHITECTURE.md): Python/Rust design and storage model.
- [Repeat task memory](docs/REPEAT_TASK_MEMORY.md): capsules, freshness, and token reduction.
- [Contributing](docs/CONTRIBUTING.md): local development and test expectations.
- [PyPI release](docs/PYPI_RELEASE.md): trusted publishing and release checks.
