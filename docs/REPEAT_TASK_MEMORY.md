# Repeat Task Memory

AI agents waste tokens when every new chat starts from zero. Memographix stores
the useful result of a task so the next chat can start with a small, verified
packet.

## Task Capsule

A task capsule stores:

- the question
- the answer
- evidence files
- file hashes
- validation notes from commands, tests, or outcome

In normal use, agents create task capsules through the MCP `capture_task` tool
after useful work. Manual `mgx remember` exists only for advanced fallback
workflows.

## Freshness

When the task is reused, Memographix hashes the evidence again.

- `fresh`: the old answer can be used as context
- `stale`: at least one evidence file changed
- `missing`: an evidence file disappeared
- `new`: no strong prior task match

This prevents a common failure mode: an AI agent confidently repeating an answer
that was correct last week but is wrong today.

## Token Budgets

Use `--budget` to control packet size:

```bash
mgx ask "explain auth again" --budget 500
```

The goal is not to dump the repo. The goal is to send the smallest useful
context to the agent.
