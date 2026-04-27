from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import install_agent_rules
from .benchmark import run_benchmark
from .mcp import serve
from .models import Freshness
from .workspace import Workspace


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="mgx",
        description="Memographix local AI agent memory and low-token context packets.",
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="cmd", required=True)

    setup = sub.add_parser("setup", help="initialize indexing, MCP config, and agent rules")
    setup.add_argument("--agents", default="all")
    setup.add_argument("--json", action="store_true")

    enable = sub.add_parser("enable", help="enable automatic Memographix memory for this repo")
    enable_group = enable.add_mutually_exclusive_group()
    enable_group.add_argument("--reindex", dest="reindex", action="store_true", default=True)
    enable_group.add_argument("--no-reindex", dest="reindex", action="store_false")
    enable.add_argument("--json", action="store_true")

    disable = sub.add_parser("disable", help="disable automatic Memographix memory for this repo")
    disable.add_argument("--reason", default="")
    disable.add_argument("--json", action="store_true")

    status_cmd = sub.add_parser("status", help="show repo-local Memographix status")
    status_cmd.add_argument("--json", action="store_true")

    sub.add_parser("init", help="initialize .memographix storage")
    sub.add_parser("index", help="index repository files and symbols")

    ask = sub.add_parser("ask", help="return a fresh context packet for a task")
    ask.add_argument("question")
    ask.add_argument("--budget", type=int, default=800)
    ask.add_argument("--json", action="store_true")

    recall = sub.add_parser("recall", help="alias for ask")
    recall.add_argument("question")
    recall.add_argument("--budget", type=int, default=800)
    recall.add_argument("--json", action="store_true")

    remember = sub.add_parser("remember", help="save a task answer for future chats")
    remember.add_argument("--question", required=True)
    remember.add_argument("--answer", required=True)
    remember.add_argument("--evidence", nargs="*", default=None)
    remember.add_argument("--validation", default="{}")

    sub.add_parser("changed", help="list task memories with stale or missing evidence")
    sub.add_parser("stats", help="show index statistics")

    doctor = sub.add_parser("doctor", help="show setup and integration health")
    doctor.add_argument("--json", action="store_true")

    savings = sub.add_parser("savings", help="show estimated token savings from memory reuse")
    savings.add_argument("--since", default="30d")
    savings.add_argument("--json", action="store_true")

    export = sub.add_parser("export", help="export graph and task memory as JSON")
    export.add_argument("--out", type=Path, default=Path("memographix-export.json"))

    serve_cmd = sub.add_parser("serve", help="start MCP stdio server or JSONL fallback")
    serve_cmd.add_argument("--jsonl", action="store_true", help="force JSONL fallback server")

    agent = sub.add_parser("install-agent", help="install per-agent memory instructions")
    agent.add_argument("agent", choices=["codex", "claude", "cursor", "copilot", "gemini", "opencode", "aider", "windsurf"])

    bench = sub.add_parser("bench", help="run sandboxed benchmark")
    bench.add_argument("corpus", type=Path)
    bench.add_argument("--tasks", type=Path, required=True)
    bench.add_argument("--out", type=Path, default=Path(".mgx-local/benchmark-results"))

    args = parser.parse_args(argv)
    root = args.root.resolve()
    ws = Workspace.open(root)

    if args.cmd == "setup":
        try:
            result = ws.setup(agents=args.agents)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from exc
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Memographix setup complete.")
            print(
                f"Indexed {result['index']['files']} files, {result['index']['symbols']} symbols, "
                f"{result['index']['edges']} edges in {result['index']['duration_ms']}ms"
            )
            print(f"MCP config: {result['mcp_config']}")
            print("Agent rules installed:")
            for item in result["agents"]:
                print(f"- {item['agent']}: {item['path']}")
            print("Use your AI agent normally; it can resolve and capture Memographix memory.")
    elif args.cmd == "enable":
        result = ws.enable(reindex=args.reindex)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Memographix automatic memory enabled for this repo.")
            if result["reindexed"] and result["index"]:
                stats = result["index"]
                print(
                    f"Re-indexed {stats['files']} files, {stats['symbols']} symbols, "
                    f"{stats['edges']} edges"
                )
    elif args.cmd == "disable":
        result = ws.disable(reason=args.reason)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Memographix automatic memory disabled: {result['reason']}")
    elif args.cmd == "status":
        result = ws.status()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Memographix status")
            print(f"configured: {result['configured']}")
            print(f"setup_completed: {result['setup_completed']}")
            print(f"enabled: {result['enabled']}")
            if result["reason"]:
                print(f"reason: {result['reason']}")
            print(f"last_indexed_at: {result['last_indexed_at'] or '-'}")
            print(f"stale_count: {result['stale_count']}")
            print(f"stats: {json.dumps(result['stats'], sort_keys=True)}")
    elif args.cmd == "init":
        print(f"Initialized {ws.init()}")
    elif args.cmd == "index":
        stats = ws.index()
        print(
            f"Indexed {stats.files} files, {stats.symbols} symbols, {stats.edges} edges "
            f"in {stats.duration_ms}ms"
        )
        if stats.skipped_sensitive:
            print(f"Skipped {stats.skipped_sensitive} sensitive file(s)")
    elif args.cmd in {"ask", "recall"}:
        packet = ws.context(args.question, budget=args.budget, refresh=True, record_event=True)
        if args.json:
            print(json.dumps(packet.to_dict(), indent=2))
        else:
            print(f"status: {packet.status.value}")
            print(f"summary: {packet.summary}")
            print(f"estimated_tokens: {packet.estimated_tokens}/{packet.token_budget}")
            if packet.warnings:
                print("warnings:")
                for warning in packet.warnings:
                    print(f"- {warning}")
            print()
            print(packet.context)
    elif args.cmd == "remember":
        try:
            validation = json.loads(args.validation)
        except json.JSONDecodeError as exc:
            print(f"invalid --validation JSON: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        task_id = ws.remember(
            args.question,
            args.answer,
            evidence=args.evidence,
            validation=validation,
        )
        print(f"Remembered task {task_id}")
    elif args.cmd == "changed":
        stale = ws.changed()
        if not stale:
            print("No stale task memories.")
        for task in stale:
            print(f"[{task.id}] {task.question}")
            for ev in task.evidence:
                if ev.status != Freshness.FRESH:
                    print(f"  - {ev.path}: {ev.status.value}")
    elif args.cmd == "stats":
        print(json.dumps(ws.stats(), indent=2))
    elif args.cmd == "doctor":
        result = ws.doctor()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Memographix doctor")
            print(f"db_exists: {result['db_exists']}")
            print(f"config_exists: {result['config_exists']}")
            print(f"mcp_package_installed: {result['mcp_package_installed']}")
            print(f"native_index_available: {result['native_index_available']}")
            print(f"enabled: {result['enabled']}")
            if result["status_reason"]:
                print(f"status_reason: {result['status_reason']}")
            print(f"stats: {json.dumps(result['stats'], sort_keys=True)}")
            missing = [a["agent"] for a in result["agents"] if not a["rules_installed"]]
            if missing:
                print(f"agent_rules_missing: {', '.join(missing)}")
    elif args.cmd == "savings":
        result = ws.savings(since_days=_parse_since_days(args.since))
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Estimated Memographix savings")
            print(result["formula"])
            print(f"fresh_hits: {result['fresh_hits']}")
            print(f"stale_preventions: {result['stale_preventions']}")
            print(f"captures_saved: {result['captures_saved']}")
            print(f"skipped_captures: {result['skipped_captures']}")
            print(f"estimated_saved_tokens: {result['estimated_saved_tokens']}")
    elif args.cmd == "export":
        out = ws.write_export(args.out)
        print(f"Exported {out.resolve()}")
    elif args.cmd == "serve":
        if args.jsonl:
            from .mcp import serve_jsonl

            serve_jsonl(str(root))
        else:
            serve(str(root))
    elif args.cmd == "install-agent":
        path = install_agent_rules(root, args.agent)
        print(f"Installed Memographix rules at {path}")
    elif args.cmd == "bench":
        result = run_benchmark(args.corpus.resolve(), args.tasks.resolve(), args.out)
        print(json.dumps(result, indent=2))


def _parse_since_days(value: str) -> int:
    clean = value.strip().lower()
    if clean.endswith("d"):
        clean = clean[:-1]
    try:
        days = int(clean)
    except ValueError as exc:
        raise SystemExit(f"invalid --since value: {value!r}") from exc
    return max(0, days)
