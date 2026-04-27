# PyPI Release

Package name: `memographix`

## Build

```bash
python -m pip install build maturin twine
python -m build --sdist --wheel
```

Run the release build from a committed checkout. Cargo and maturin can fail in a
brand-new Git repo that has no `HEAD` yet.

## Check Package Contents

Docker and benchmark files must not ship in the runtime package.

```bash
! python -m zipfile -l dist/*.whl | grep -E 'benchmarks|benchmark_results|docker'
! tar -tf dist/*.tar.gz | grep -E 'benchmarks|benchmark_results|docker'
```

## Publish

The preferred release path is GitHub trusted publishing:

1. Configure the PyPI pending publisher once:
   - Project: `memographix`
   - Owner: `coderalnaim`
   - Repository: `memographix`
   - Workflow: `release.yml`
   - Environment: `pypi`
2. Create a GitHub release for a version tag.
3. The `Release` workflow builds Linux, macOS, and Windows ABI3 wheels plus sdist.
4. The publish job uploads to PyPI through OIDC trusted publishing.

Manual publishing is only a fallback:

```bash
twine upload dist/*
```

## Verify

```bash
python -m venv /tmp/mgx-check
/tmp/mgx-check/bin/python -m pip install memographix
/tmp/mgx-check/bin/mgx --help
```

Runtime install must not require Docker, Node, Graphify, GraphRAG, or competitor
benchmark tools.

For user-facing CLI docs, prefer `pipx install memographix`. Keep
`python -m pip install memographix` documented for virtual environments and CI
because Homebrew-managed Python blocks global pip installs through PEP 668.

The README is packaged from the root `README.md` through `pyproject.toml`.
PyPI snapshots that README at publish time. Keep install instructions versionless
and rely on the live PyPI badge for the current published version.
