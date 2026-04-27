# Contributing

Keep the project focused: reduce repeated explanation cost for developers using
AI agents.

## Local Tests

```bash
PYTHONPATH=python pytest
```

## Benchmark Smoke Test

```bash
docker compose -f benchmarks/docker/compose.yml up --build --abort-on-container-exit
```

## Rules

- Do not add benchmark tools to runtime dependencies.
- Do not index secrets.
- Do not reuse stale task memory as fact.
- Update benchmark claims only from raw Docker results.

