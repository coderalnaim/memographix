# Agent Integrations

Memographix is MCP-first. Run setup once per repo:

```bash
mgx setup
```

Setup writes `.memographix/mcp.json` and installs project rules for Codex,
Claude, Cursor, Copilot, Gemini, OpenCode, Aider, and Windsurf-style agents.

## Expected Agent Behavior

Agents should use these tools automatically:

- `resolve_task`: call before implementation, debugging, architecture, or
  test-failure work.
- `capture_task`: call after useful work with the answer, changed files,
  commands, tests, and outcome.
- `freshness_check`: inspect stale memories.
- `graph_stats`: inspect index health.

If `resolve_task` returns `status: "disabled"` or `enabled: false`, the agent
must continue normally without Memographix context and must not call
`capture_task` for that turn. Developers control this per repo with:

```bash
mgx disable --reason "not needed here"
mgx enable
mgx status
```

`remember_task` remains available as a backward-compatible alias for
`capture_task`.

## Fallback CLI

If an agent cannot use MCP yet, project rules tell it to call:

```bash
mgx ask "<developer task>" --budget 800
```

Manual memory saving is an advanced fallback only. The normal flow is automatic
capture through MCP.

## Health Check

```bash
mgx doctor
```

Use this to confirm that local state, MCP config, native indexing, and project
rules are present.
