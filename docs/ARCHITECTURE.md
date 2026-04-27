# Architecture

Memographix has one job: return fresh, compact context for repeated developer
tasks.

## Layers

- Python: CLI, public API, MCP server, setup, savings reports, benchmark harness.
- Rust: native incremental fast path for scanning, hashing, and symbol extraction.
- Fallback: pure Python engine when a native wheel is unavailable.

## Flow

```text
repo
  -> index files and symbols
  -> save hashes in .memographix/graph.sqlite
  -> resolve tasks before agent work
  -> capture task answers with evidence after agent work
  -> check evidence freshness
  -> return ContextPacket
```

## Storage

Memographix writes local state to `.memographix/`.

Main database: `.memographix/graph.sqlite`

Repo-local control lives in `.memographix/config.toml`:

- `setup_completed`: automatic use is configured for this repo
- `enabled`: automatic MCP resolve/capture is on or off
- `disabled_reason`, `last_enabled_at`, `last_disabled_at`: operator-visible
  control state

Key tables:

- `files`: path, hash, size, language
- `symbols`: file, kind, name, line, signature
- `edges`: simple local relationships
- `tasks`: question, normalized intent, answer, validation
- `task_evidence`: cited files, stored hashes, excerpts
- `memory_events`: resolve/capture events and estimated savings

## ContextPacket

A context packet is what an AI agent receives.

It includes:

- status: `new`, `fresh`, `stale`, or `missing`
- matched prior task
- evidence files
- token estimate
- warnings for stale context
- compact text for the agent

Stale evidence is never treated as authoritative.

Automatic MCP calls can also return `status: disabled` when the repo has not
been set up or was turned off with `mgx disable`.

## Automatic Capture

Agents call `capture_task` after useful work. Memographix saves a capsule only
when it can attach safe repo-local evidence. Captures without evidence are
recorded as skipped events so `mgx savings` can show quality-control behavior.
