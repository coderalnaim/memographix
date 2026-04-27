# Agent Integrations

Memographix is MCP-first. Install the CLI once with pipx:

```bash
pipx install memographix
```

If you are already inside a virtual environment or CI job, use pip instead:

```bash
python -m pip install memographix
```

Run setup once per repo:

```bash
mgx setup
mgx doctor --live
mgx verify-agent --repair
```

Setup writes `.memographix/mcp.json`, configures supported MCP clients, and
registers the repo for global routing. It also installs project rules for
Codex, Claude, Cursor, Copilot, Gemini, OpenCode, Aider, and Windsurf-style
agents. For Codex, setup also installs a global `memographix` Codex skill under
`~/.codex/skills/` so new chats are told to check Memographix before any
repo-specific work. Restart already-open agents after setup so they reload MCP
tools and skills.
`mgx doctor --live` verifies the local server and routing. `mgx verify-agent`
verifies that the active agent actually calls Memographix. Add `--repair` when
you want Memographix to refresh stale MCP config before verification.
The old `pip install "memographix[mcp]"` form remains accepted for backward
compatibility, but it is no longer required.

## Configured Files

`mgx setup` writes the native MCP config where the client has a stable local
configuration file:

| Agent | Config written by setup |
| --- | --- |
| Codex | `~/.codex/config.toml` plus `~/.codex/skills/memographix/SKILL.md` |
| Claude Code | `.mcp.json` |
| Cursor | `.cursor/mcp.json` |
| GitHub Copilot in VS Code | `.vscode/mcp.json` |
| Gemini CLI | `.gemini/settings.json` |
| OpenCode | `opencode.json` |
| Windsurf | `~/.codeium/mcp_config.json` |
| Aider | project rules fallback |

Run `mgx doctor --live` to verify that the MCP server starts, expected tools are
available, and the router can resolve the current repo.
Aider does not currently have a stable native MCP config path in Memographix, so
setup installs project rules and the CLI fallback for it.

## Expected Agent Behavior

Strict mode is enabled by default. Agents must use these tools automatically:

- `resolve_task`: call before repo-specific answers, file inspection, shell
  exploration, implementation, debugging, architecture, documentation edits, or
  test-failure work. Pass `repo` when the chat is outside the repo or the user
  mentions a repo name.
- `capture_task`: call after useful work with the answer, changed files,
  commands, tests, outcome, and `resolve_event_id` when available.
- `list_repos`: list repos registered for global routing.
- `activation_status`: confirm that a repo can be resolved and has seen calls.
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

Every final answer after repo work must include exactly one concise status line:

```text
Memographix: saved task memory
Memographix: not saved - <reason>
Memographix: disabled for this repo
```

Agents must reuse the `final_status_line` returned by `capture_task` and must
not claim memory was saved unless `capture_task` returns `saved: true`.

## Fallback CLI

If an agent cannot use MCP yet, project rules tell it to self-heal first:

```bash
mgx --root "<absolute repo root>" doctor --live --repair
```

The agent then retries MCP once. If MCP still cannot be used, it falls back to
explicit repo-root CLI commands:

```bash
mgx --root "<absolute repo root>" ask "<developer task>" --budget 800
mgx --root "<absolute repo root>" remember --question "<developer task>" --answer "<final answer>" --evidence <repo-local evidence files>
```

Manual memory saving is an advanced fallback only. The normal flow is automatic
capture through MCP, and `mgx remember` now records the same capture events as
MCP `capture_task`.

## Health Check

```bash
mgx doctor --live
```

Use this to confirm that local state, MCP config, native indexing, project
rules, live MCP startup, and repo routing are working. Use `mgx verify-agent` to
confirm that the active agent actually calls Memographix. If `mgx savings`
reports zero events, the agent has not called Memographix yet.

Useful diagnostics:

```bash
mgx repos
mgx doctor --live --repair
mgx heal
mgx verify-agent --repair
mgx guard
mgx savings
```
