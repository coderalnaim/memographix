from __future__ import annotations

import json
from pathlib import Path

from memographix.cli import main


def test_cli_index_ask_remember(tmp_path: Path, capsys) -> None:
    (tmp_path / "service.py").write_text("class RuntimeService:\n    pass\n", encoding="utf-8")
    main(["--root", str(tmp_path), "index"])
    out = capsys.readouterr().out
    assert "Indexed" in out

    main(["--root", str(tmp_path), "ask", "runtime service", "--json"])
    packet = json.loads(capsys.readouterr().out)
    assert packet["status"] == "new"

    main(
        [
            "--root",
            str(tmp_path),
            "remember",
            "--question",
            "runtime service",
            "--answer",
            "RuntimeService is in service.py.",
            "--evidence",
            "service.py",
        ]
    )
    assert "Remembered task" in capsys.readouterr().out

    main(["--root", str(tmp_path), "ask", "runtime service", "--json"])
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["status"] == "fresh"


def test_cli_setup_doctor_and_savings(tmp_path: Path, capsys) -> None:
    (tmp_path / "service.py").write_text("def handle():\n    return True\n", encoding="utf-8")

    main(["--root", str(tmp_path), "setup", "--agents", "codex", "--json"])
    setup = json.loads(capsys.readouterr().out)
    assert setup["index"]["files"] == 1
    assert setup["status"]["enabled"] is True
    assert (tmp_path / ".memographix" / "config.toml").exists()
    assert (tmp_path / ".memographix" / "mcp.json").exists()
    assert (tmp_path / "AGENTS.md").exists()

    main(["--root", str(tmp_path), "doctor", "--json"])
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["db_exists"] is True
    assert doctor["config_exists"] is True
    assert doctor["enabled"] is True

    main(["--root", str(tmp_path), "savings", "--json"])
    savings = json.loads(capsys.readouterr().out)
    assert savings["estimated"] is True
    assert "saved_tokens = raw_evidence_file_tokens" in savings["formula"]


def test_cli_enable_disable_status(tmp_path: Path, capsys) -> None:
    source = tmp_path / "service.py"
    source.write_text("def handle():\n    return True\n", encoding="utf-8")

    main(["--root", str(tmp_path), "setup", "--agents", "codex"])
    capsys.readouterr()

    main(["--root", str(tmp_path), "disable", "--reason", "manual pause", "--json"])
    disabled = json.loads(capsys.readouterr().out)
    assert disabled["enabled"] is False
    assert disabled["reason"] == "manual pause"

    main(["--root", str(tmp_path), "status", "--json"])
    status = json.loads(capsys.readouterr().out)
    assert status["enabled"] is False
    assert status["reason"] == "manual pause"

    source.write_text("def handle_new():\n    return False\n", encoding="utf-8")
    main(["--root", str(tmp_path), "enable", "--json"])
    enabled = json.loads(capsys.readouterr().out)
    assert enabled["enabled"] is True
    assert enabled["index"]["files"] >= 1
