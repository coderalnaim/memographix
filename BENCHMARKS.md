# Benchmarks

Memographix is benchmarked for the area it is designed to own: repeated
developer task memory with fresh evidence and small agent-ready context.

Tracked docs include only sanitized public summaries. Raw JSON, logs, cloned
corpora, and sandboxes stay under `.mgx-local/`.

These results support the product promise in the [README](README.md): help AI
agents reuse prior developer task context without silently reusing stale
evidence or sending a whole repository back to the model.

## Corpus

Primary corpus:

```text
https://github.com/kubernetes/kubernetes.git
```

Pinned commit:

```text
75d51c440770f96b9725efea4145ba50fc6aef4b
```

## Run

Fetch the pinned public corpus:

```bash
python3 benchmarks/fetch_corpus.py
```

Smoke benchmark:

```bash
docker compose -f benchmarks/docker/compose.yml up --build --abort-on-container-exit
```

Full reference benchmark:

```bash
docker compose -f benchmarks/docker/compose.yml run --rm benchmark \
  bash -lc 'python -m pip install -q . &&
  PYTHONPATH= python benchmarks/run.py
  --corpus /corpus
  --out /results/full
  --tools memographix,naive,grep,graphify,aider-repomap,graphrag,codegraphcontext,codegraph-cli,gitnexus,narsil
  --allow-external-installs'
```

Docker and competitor tools are benchmark-only. They are not PyPI runtime
dependencies.

For architecture and scoring context, see [Architecture](docs/ARCHITECTURE.md)
and [Repeat task memory](docs/REPEAT_TASK_MEMORY.md).

## Latest Full Run

Run date: 2026-04-27.

Corpus: Kubernetes at `75d51c440770f96b9725efea4145ba50fc6aef4b`.

| Tool | Status | Index ms | Re-index ms | Recall p50 ms | Stale detected | Tokens @500 | Tokens @800 | Quality | Evidence recall | Concept coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| memographix | ok | 5697 | 408 | 13 | yes | 450 | 683 | 0.775 | 0.7333 | 0.9 |
| naive raw repo | ok | 541 | 541 | 481 | no | 57227636 | 57227636 | 0.7536 | 1.0 | 1.0 |
| grep baseline | ok | 949 | 949 | 561 | no | 500 | 757 | 0.4679 | 0.5333 | 0.4 |
| Graphify / graphifyy | ok | 186028 | - | - | no | 4790691 | 4790691 | 0.6504 | 1.0 | 0.7 |
| Aider repo-map | unavailable | - | - | - | - | - | - | - | - | - |
| Microsoft GraphRAG | ok | 677 | - | - | no | - | - | - | - | - |
| CodeGraphContext | ok | 292 | - | - | no | - | - | - | - | - |
| codegraph-cli | ok | 810 | - | - | no | - | - | - | - | - |
| GitNexus | unavailable | - | - | - | - | - | - | - | - | - |
| Narsil / Code Context Graph | unavailable | - | - | - | - | - | - | - | - | - |

Winners in this run:

- First index: CodeGraphContext.
- Warm re-index: Memographix.
- Repeated recall p50: Memographix.
- Repeated tokens at 200, 500, 800, and 1500 budgets: Memographix.
- Stale-evidence detection: Memographix.
- Deterministic quality score: Memographix.
- Raw evidence recall and raw concept coverage: naive raw repo, because it
  returns the whole repository.

## Quality Benchmark

The quality scorer is deterministic. Each Kubernetes task defines expected
evidence paths, required concepts, and forbidden hallucination markers.

Reported fields:

| Metric | Meaning |
|---|---|
| Quality | Weighted score from evidence recall, evidence precision, concept coverage, and hallucination flags |
| Evidence recall | Fraction of expected evidence areas represented |
| Evidence precision | Fraction of returned paths that match expected evidence areas |
| Concept coverage | Fraction of required task concepts present in returned context |
| Hallucination flags | Forbidden markers found in returned context |

Current result: Memographix reduces repeated-task context from tens of millions
of raw-repo tokens to 450-758 tokens while preserving high concept coverage
(`0.9`), no hallucination flags, and the best combined quality score in the
full run.

## Unavailable Tools

Unavailable tools are recorded honestly:

- Aider repo-map: `pip install aider-chat` failed on Python 3.13 while resolving
  or building `aiohttp==3.8.4`.
- GitNexus: `npm install -g gitnexus` failed while building `tree-sitter-c`
  through `node-gyp`; Python `gyp` was unavailable in the Docker image.
- Narsil / Code Context Graph: `cargo install narsil-mcp --locked` failed
  because the benchmark image has Rust 1.85.0 and dependencies require Rust
  1.88.0.

## Claim Policy

Memographix can claim measured wins only for metrics it wins in sanitized
benchmark output. In the latest full run it can claim a clear edge for repeated
developer task memory: lower repeated tokens, faster repeated recall, stale
evidence safety, and higher combined deterministic quality score.

It must not claim first-index leadership. CodeGraphContext and raw scanning are
faster on first index in this run.

Back to the [README](README.md) for installation and normal agent usage.
