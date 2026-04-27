from __future__ import annotations

import json
from pathlib import Path

from memographix.cli import main
from memographix.workspace import Workspace


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


def test_cli_setup_doctor_and_savings(tmp_path: Path, capsys, monkeypatch) -> None:
    codex_config = tmp_path / "codex-config.toml"
    monkeypatch.setenv("MEMOGRAPHIX_CODEX_CONFIG", str(codex_config))
    (tmp_path / "service.py").write_text("def handle():\n    return True\n", encoding="utf-8")

    main(["--root", str(tmp_path), "setup", "--agents", "codex", "--json"])
    setup = json.loads(capsys.readouterr().out)
    assert setup["index"]["files"] == 1
    assert setup["status"]["enabled"] is True
    assert setup["status"]["setup_agents"] == ["codex"]
    assert any(item["agent"] == "codex" for item in setup["integrations"])
    assert setup["codex_mcp"]["registered"] is True
    assert (tmp_path / ".memographix" / "config.toml").exists()
    assert (tmp_path / ".memographix" / "mcp.json").exists()
    assert (tmp_path / "AGENTS.md").exists()
    codex_config_text = codex_config.read_text(encoding="utf-8")
    assert "[mcp_servers.memographix_" in codex_config_text
    assert json.dumps(str(tmp_path)) in codex_config_text

    main(["--root", str(tmp_path), "doctor", "--json"])
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["db_exists"] is True
    assert doctor["config_exists"] is True
    assert doctor["enabled"] is True
    assert doctor["setup_agents"] == ["codex"]
    assert doctor["codex_mcp_registered"] is True
    assert doctor["manual_mcp_config_required"] is False

    main(["--root", str(tmp_path), "savings", "--json"])
    savings = json.loads(capsys.readouterr().out)
    assert savings["estimated"] is True
    assert "saved_tokens = raw_evidence_file_tokens" in savings["formula"]


def test_setup_writes_supported_mcp_integrations(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MEMOGRAPHIX_CODEX_CONFIG", str(tmp_path / "codex-config.toml"))
    monkeypatch.setenv("MEMOGRAPHIX_WINDSURF_CONFIG", str(tmp_path / "windsurf-mcp.json"))
    (tmp_path / "service.py").write_text("def handle():\n    return True\n", encoding="utf-8")

    result = Workspace.open(tmp_path).setup(
        agents="codex,claude,cursor,copilot,gemini,opencode,windsurf,aider"
    )

    integrations = {item["agent"]: item for item in result["integrations"]}
    assert integrations["codex"]["ready"] is True
    assert integrations["claude"]["ready"] is True
    assert integrations["cursor"]["ready"] is True
    assert integrations["copilot"]["ready"] is True
    assert integrations["gemini"]["ready"] is True
    assert integrations["opencode"]["ready"] is True
    assert integrations["windsurf"]["ready"] is True
    assert integrations["aider"]["mode"] == "rules"

    assert "mcpServers" in json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    cursor_config = json.loads(
        (tmp_path / ".cursor" / "mcp.json").read_text(encoding="utf-8")
    )
    assert "mcpServers" in cursor_config
    assert "servers" in json.loads((tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    assert "mcpServers" in json.loads(
        (tmp_path / ".gemini" / "settings.json").read_text(encoding="utf-8")
    )
    opencode = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    server = next(iter(opencode["mcp"].values()))
    assert server["type"] == "local"
    assert server["command"][-3:] == ["--root", str(tmp_path), "serve"]
    assert "mcpServers" in json.loads((tmp_path / "windsurf-mcp.json").read_text(encoding="utf-8"))


def test_cli_enable_disable_status(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("MEMOGRAPHIX_CODEX_CONFIG", str(tmp_path / "codex-config.toml"))
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
