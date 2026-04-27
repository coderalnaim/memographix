# Security

Memographix is local-first.

- No telemetry.
- No required network calls.
- No required LLM API.
- State stays in `.memographix/`.

## Skipped Files

Memographix skips common sensitive files:

- `.env`
- private keys
- certificates
- files with names such as `secret`, `credential`, `password`, or `token`
- dependency, cache, build, and virtual environment folders

## Stale Memory Safety

A remembered answer is safe only when its evidence files still match their
stored hashes.

If a cited file changes, Memographix returns `status: stale`. Agents must inspect
the changed evidence before using the old answer.

## Benchmark Isolation

Docker is used only for benchmarking. Benchmark containers copy repos into
temporary sandboxes and remove those sandboxes after each run.

