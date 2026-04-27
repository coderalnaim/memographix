# Memographix

Memographix gives AI coding agents local memory for your repo.

Install it once, run setup once, and keep using Codex, Claude, Cursor, Copilot,
Gemini, OpenCode, Aider, Windsurf, or any MCP client normally. Memographix
retrieves fresh context before work and captures useful task memory after work.

## Quick Start

Run this inside the repo where you want AI-agent memory:

```bash
pipx install memographix
mgx setup
mgx doctor --live
mgx verify-agent
mgx savings
```

`pipx` avoids system Python conflicts on macOS and Linux. The PyPI package
includes the CLI, local indexer, MCP server, and agent integration support.
`mgx setup` creates `.memographix/`, indexes the repo, writes MCP config for
supported agents, registers the repo for global MCP routing, and installs
project agent rules.

If you are already inside a virtual environment or CI job, this also works:

```bash
python -m pip install memographix
```

PyPI resolves the latest release automatically, so install commands stay
versionless.

## Daily Use
Use your AI agent normally. In strict mode the agent asks Memographix for fresh
context before repo work, captures useful results after work, and marks old
memory stale when evidence files change.

```bash
mgx doctor --live
mgx verify-agent
mgx guard
mgx savings --since 30d
```

`mgx doctor --live` verifies that the MCP server starts, expected tools are
available, and the router can resolve this repo. It does not prove your active
agent has actually called Memographix yet. `mgx verify-agent` gives you a short
prompt to paste into the agent and passes only after that agent performs a real
`resolve_task` and `capture_task`. Restart agents after setup if they were
already open.

Control it per repo:

```bash
mgx status
mgx disable --reason "not needed here"
mgx enable
```

Disabled repos keep existing memory but automatic agent calls return no context
and save nothing. Re-enabling refreshes the index before Memographix is used
again.

If savings are all zero, Memographix now tells you whether no agent tool calls
have been recorded yet. Run `mgx doctor --live`, `mgx verify-agent`, restart the
agent, and either open the chat from the repo or mention a registered repo name.
`mgx guard` warns when Memographix saw no MCP usage, saw context retrieval
without later capture, or sees modified files without any capture event.

Other useful commands:

```bash
mgx repos
mgx repair --mcp
```

## Proof

On the pinned Kubernetes benchmark, Memographix wins the repeated-task metrics it
is designed for: lower repeated tokens, faster repeated recall, stale-evidence
safety, and the best deterministic quality score. See [Benchmarks](BENCHMARKS.md)
for the public corpus, exact commands, honest losses, and unavailable-tool
notes.

## Boundaries

Memographix does not upload your code, save full chat transcripts by default,
treat stale memory as correct, or install benchmark tools in the runtime package.

## Docs

- [Benchmarks](BENCHMARKS.md): public Kubernetes results and claim policy.
- [Security](SECURITY.md): local privacy, skipped secrets, and sandbox safety.
- [Agent integrations](docs/AGENT_INTEGRATIONS.md): MCP and agent setup.
- [Architecture](docs/ARCHITECTURE.md): Python/Rust design and storage model.
- [Repeat task memory](docs/REPEAT_TASK_MEMORY.md): capsules and freshness.
- [Contributing](docs/CONTRIBUTING.md): local development and test expectations.
- [PyPI release](docs/PYPI_RELEASE.md): trusted publishing and release checks.
