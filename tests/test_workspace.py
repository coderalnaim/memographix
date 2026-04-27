from __future__ import annotations

from pathlib import Path

from memographix import Freshness, Workspace


def test_repeated_task_memory_is_fresh_then_stale(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def load_user(user_id):\n    return {'id': user_id}\n\n"
        "def handle_auth(user_id):\n    return load_user(user_id)\n",
        encoding="utf-8",
    )
    ws = Workspace.open(tmp_path)
    stats = ws.index()
    assert stats.files == 1
    assert stats.symbols >= 3

    first = ws.context("How does auth load the user?", budget=300)
    assert first.status == Freshness.NEW
    assert "handle_auth" in first.context or "load_user" in first.context

    task_id = ws.remember(
        "How does auth load the user?",
        "Auth calls handle_auth, which delegates to load_user.",
        evidence=["app.py"],
        validation={"tests": "passed"},
    )
    assert task_id == 1

    repeated = ws.context("Can you explain again how auth loads user?", budget=300)
    assert repeated.status == Freshness.FRESH
    assert "prior_answer" in repeated.context
    assert repeated.estimated_tokens <= 300

    (tmp_path / "app.py").write_text("def handle_auth(user_id):\n    return None\n", encoding="utf-8")
    stale = ws.context("Can you explain again how auth loads user?", budget=300)
    assert stale.status == Freshness.STALE
    assert "Do not reuse the prior answer" in stale.context
    assert ws.changed()


def test_missing_evidence_is_not_reused(tmp_path: Path) -> None:
    ws = Workspace.open(tmp_path)
    ws.index()
    ws.remember("Where is missing evidence?", "It used to be in gone.py.", evidence=["gone.py"])

    packet = ws.context("Where is missing evidence?", budget=300)
    assert packet.status == Freshness.STALE
    assert "Do not reuse the prior answer" in packet.context


def test_sensitive_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "secret.py").write_text("API_TOKEN = 'not-real'\n", encoding="utf-8")
    (tmp_path / "public.py").write_text("def visible():\n    return True\n", encoding="utf-8")
    ws = Workspace.open(tmp_path)
    stats = ws.index()

    assert stats.files == 1
    assert stats.skipped_sensitive == 1
    exported_paths = {item["path"] for item in ws.export_json()["files"]}
    assert exported_paths == {"public.py"}


def test_configurable_retrieval_policy_indexes_extra_extensions(tmp_path: Path) -> None:
    config_dir = tmp_path / ".memographix"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        """
[retrieval]
extra_extensions = { ".workflow" = "workflow" }
extra_stopwords = ["customnoise"]
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "release.workflow").write_text("step publish_package\n", encoding="utf-8")
    ws = Workspace.open(tmp_path)
    stats = ws.index()

    assert stats.files == 1
    assert ws.stats()["files"] == 1


def test_export_and_stats(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n\nRuntime memory notes.\n", encoding="utf-8")
    ws = Workspace.open(tmp_path)
    ws.index()
    ws.remember("What are runtime memory notes?", "They are in README.", evidence=["README.md"])
    stats = ws.stats()
    assert stats["files"] == 1
    assert stats["tasks"] == 1
    exported = ws.export_json()
    assert exported["stats"]["tasks"] == 1


def test_capture_and_savings_events(tmp_path: Path) -> None:
    (tmp_path / "cache.py").write_text(
        "def load_cache():\n    return 'cache'\n",
        encoding="utf-8",
    )
    ws = Workspace.open(tmp_path)
    ws.enable(reindex=True)

    skipped = ws.capture("How does cache load?", "It calls load_cache.")
    assert skipped["saved"] is False

    saved = ws.capture(
        "How does cache load?",
        "It calls load_cache.",
        evidence=["cache.py"],
        tests=["pytest -q"],
        outcome="passed",
    )
    assert saved["saved"] is True

    packet = ws.context("Explain again how cache loads", budget=300, record_event=True)
    assert packet.status == Freshness.FRESH
    savings = ws.savings()
    assert savings["fresh_hits"] == 1
    assert savings["captures_saved"] == 1
    assert savings["skipped_captures"] == 1


def test_repo_control_disable_enable_preserves_memory_and_reindexes(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text("def handle():\n    return True\n", encoding="utf-8")
    ws = Workspace.open(tmp_path)
    ws.enable(reindex=True)
    saved = ws.capture(
        "How does service handle work?",
        "handle returns True.",
        evidence=["service.py"],
        tests=["pytest -q"],
        outcome="passed",
    )
    assert saved["saved"] is True

    disabled = ws.disable("paused for this repo")
    assert disabled["enabled"] is False
    assert ws.status()["enabled"] is False
    skipped = ws.capture(
        "How does service handle work?",
        "handle returns True.",
        evidence=["service.py"],
    )
    assert skipped == {
        "saved": False,
        "task_id": None,
        "reason": "paused for this repo",
        "evidence": [],
    }
    assert ws.stats()["tasks"] == 1

    source.write_text("def handle_new():\n    return False\n", encoding="utf-8")
    enabled = ws.enable()
    assert enabled["enabled"] is True
    exported = ws.export_json()
    symbols = {row["name"] for row in exported["symbols"]}
    assert "handle_new" in symbols
    assert "handle" not in symbols


def test_incremental_index_updates_changed_and_deleted_files(tmp_path: Path) -> None:
    (tmp_path / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("def two():\n    return 2\n", encoding="utf-8")
    ws = Workspace.open(tmp_path)
    first = ws.index()
    assert first.files == 2

    (tmp_path / "one.py").write_text("def one_changed():\n    return 10\n", encoding="utf-8")
    (tmp_path / "two.py").unlink()
    second = ws.index()
    assert second.files == 1
    exported = ws.export_json()
    paths = {row["path"] for row in exported["files"]}
    symbols = {row["name"] for row in exported["symbols"]}
    assert paths == {"one.py"}
    assert "one_changed" in symbols
    assert "two" not in symbols
