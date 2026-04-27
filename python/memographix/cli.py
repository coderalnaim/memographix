from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
    remember.add_argument("--resolve-event-id", type=int, default=None)
    remember.add_argument("--commands", nargs="*", default=None)
    remember.add_argument("--tests", nargs="*", default=None)
    remember.add_argument("--outcome", default="")
    remember.add_argument("--agent", default="cli")
    remember.add_argument("--json", action="store_true")

    sub.add_parser("changed", help="list task memories with stale or missing evidence")
    sub.add_parser("stats", help="show index statistics")

    doctor = sub.add_parser("doctor", help="show setup and integration health")
    doctor.add_argument("--live", action="store_true")
    doctor.add_argument("--repair", action="store_true")
    doctor.add_argument("--json", action="store_true")

    savings = sub.add_parser("savings", help="show estimated token savings from memory reuse")
    savings.add_argument("--since", default="30d")
    savings.add_argument("--json", action="store_true")

    verify = sub.add_parser(
        "verify-agent",
        help="verify that an AI agent actually calls Memographix",
    )
    verify.add_argument(
        "--agent",
        default="codex",
        choices=["codex", "claude", "cursor", "copilot", "gemini", "opencode", "aider", "windsurf"],
    )
    verify.add_argument("--wait", type=int, default=120)
    verify.add_argument("--repair", action="store_true")
    verify.add_argument("--json", action="store_true")

    guard = sub.add_parser("guard", help="check for missed Memographix agent usage")
    guard.add_argument("--since", default="24h")
    guard.add_argument("--json", action="store_true")

    export = sub.add_parser("export", help="export graph and task memory as JSON")
    export.add_argument("--out", type=Path, default=Path("memographix-export.json"))

    repos = sub.add_parser("repos", help="list repos registered for Memographix activation")
    repos.add_argument("--json", action="store_true")

    repair = sub.add_parser("repair", help="repair Memographix local integration config")
    repair.add_argument("--mcp", action="store_true", required=True)
    repair.add_argument("--agents", default="all")
    repair.add_argument("--json", action="store_true")

    heal = sub.add_parser("heal", help="self-heal Memographix setup and MCP integrations")
    heal.add_argument("--agents", default="all")
    heal.add_argument("--json", action="store_true")

    serve_cmd = sub.add_parser("serve", help="start MCP stdio server or JSONL fallback")
    serve_cmd.add_argument("--jsonl", action="store_true", help="force JSONL fallback server")

    agent = sub.add_parser("install-agent", help="install per-agent memory instructions")
    agent.add_argument(
        "agent",
        choices=["codex", "claude", "cursor", "copilot", "gemini", "opencode", "aider", "windsurf"],
    )

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
            print(f"Registered repo: {result['registry']['name']}")
            ready = [item for item in result["integrations"] if item["ready"]]
            print(f"MCP integrations ready: {len(ready)}/{len(result['integrations'])}")
            for item in result["integrations"]:
                status = "ready" if item["ready"] else "needs review"
                detail = f" ({item['reason']})" if item["reason"] else ""
                skill = f"; skill {item['skill_path']}" if item.get("skill_path") else ""
                print(f"- {item['agent']}: {status} via {item['path']}{skill}{detail}")
            print("Agent rules installed:")
            for item in result["agents"]:
                print(f"- {item['agent']}: {item['path']}")
            print("Use your AI agent normally; it can resolve and capture Memographix memory.")
            print("Restart already-open agents so they reload MCP tools and Codex skills.")
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
            print(f"strict_mode: {result['strict_mode']}")
            if result["reason"]:
                print(f"reason: {result['reason']}")
            print(f"last_indexed_at: {result['last_indexed_at'] or '-'}")
            print(f"stale_count: {result['stale_count']}")
            print(f"stats: {json.dumps(result['stats'], sort_keys=True)}")
            ready = [item for item in result["integrations"] if item["ready"]]
            print(f"mcp_integrations_ready: {len(ready)}/{len(result['integrations'])}")
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
        packet = ws.resolve(
            args.question,
            budget=args.budget,
            refresh=True,
            record_event=True,
            source="cli",
            agent="cli",
        )
        if args.json:
            print(json.dumps(packet, indent=2))
        else:
            print(f"repo_root: {packet['repo_root']}")
            print(f"event_id: {packet['event_id']}")
            print(f"status: {packet['status']}")
            print(f"summary: {packet['summary']}")
            print(f"estimated_tokens: {packet['estimated_tokens']}/{packet['token_budget']}")
            if packet["warnings"]:
                print("warnings:")
                for warning in packet["warnings"]:
                    print(f"- {warning}")
            print()
            print(packet["context"])
    elif args.cmd == "remember":
        try:
            validation = json.loads(args.validation)
        except json.JSONDecodeError as exc:
            print(f"invalid --validation JSON: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        commands = list(args.commands or [])
        tests = list(args.tests or [])
        if validation:
            commands.extend(str(item) for item in validation.get("commands", []) or [])
            tests.extend(str(item) for item in validation.get("tests", []) or [])
        outcome = args.outcome or str(validation.get("outcome", "") or "")
        result = ws.capture(
            args.question,
            args.answer,
            evidence=args.evidence,
            commands=commands,
            tests=tests,
            outcome=outcome,
            resolve_event_id=args.resolve_event_id,
            source="cli",
            agent=args.agent,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        elif result["saved"]:
            print(f"Remembered task {result['task_id']}")
            print(result["final_status_line"])
        else:
            print(result["final_status_line"])
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
        result = ws.doctor(live=args.live, repair=args.repair)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Memographix doctor")
            print(f"db_exists: {result['db_exists']}")
            print(f"config_exists: {result['config_exists']}")
            print(f"mcp_package_installed: {result['mcp_package_installed']}")
            if result["mcp_runtime_required"]:
                print("mcp_runtime_required: install or upgrade memographix from PyPI")
            print(f"native_index_available: {result['native_index_available']}")
            print(f"enabled: {result['enabled']}")
            print(f"strict_mode: {result['strict_mode']}")
            print(f"registry_registered: {result['registry_registered']}")
            ready = [item for item in result["integrations"] if item["ready"]]
            print(f"mcp_integrations_ready: {len(ready)}/{len(result['integrations'])}")
            activation = result["activation"]
            print(f"agent_tool_calls_seen: {activation['has_agent_calls']}")
            print(f"agent_verified: {activation['agent_verified']}")
            if activation["last_mcp_call_at"]:
                print(f"last_mcp_call_at: {activation['last_mcp_call_at']}")
            if activation["last_capture_at"]:
                print(
                    f"last_capture_at: {activation['last_capture_at']} "
                    f"({activation['last_capture_status'] or 'unknown'})"
                )
            if activation["last_verified_agent_at"]:
                print(
                    f"last_verified_agent_at: {activation['last_verified_agent_at']} "
                    f"({activation['last_verified_agent'] or 'agent'})"
                )
            if activation["last_unverified_warning"]:
                print(f"last_unverified_warning: {activation['last_unverified_warning']}")
            if result["status_reason"]:
                print(f"status_reason: {result['status_reason']}")
            if args.live:
                live = result["live"]
                print(f"live_check_ok: {live['ok']}")
                if not live["ok"]:
                    reason = live.get("reason") or live.get("stderr") or "failed"
                    print(f"live_check_reason: {reason}")
            if args.repair:
                print(f"repaired: {result['repaired']}")
                if result["repair_actions"]:
                    print("repair_actions:")
                    for action in result["repair_actions"]:
                        print(f"- {action}")
                if result["remaining_issues"]:
                    print("remaining_issues:")
                    for issue in result["remaining_issues"]:
                        print(f"- {issue}")
            print(f"stats: {json.dumps(result['stats'], sort_keys=True)}")
            for item in result["integrations"]:
                status = "ready" if item["ready"] else "needs review"
                detail = f" ({item['reason']})" if item["reason"] else ""
                skill = f"; skill {item['skill_path']}" if item.get("skill_path") else ""
                print(f"- {item['agent']}: {status} via {item['path']}{skill}{detail}")
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
            if result.get("capture_events_by_source"):
                source_counts = json.dumps(
                    result["capture_events_by_source"],
                    sort_keys=True,
                )
                print(f"capture_events_by_source: {source_counts}")
            if result.get("legacy_tasks_without_capture_events"):
                print(
                    "legacy_tasks_without_capture_events: "
                    f"{result['legacy_tasks_without_capture_events']}"
                )
            if result.get("diagnostic"):
                print()
                print(result["diagnostic"])
                print("Next checks:")
                print("- mgx doctor --live")
                print("- mgx verify-agent")
                print("- restart your AI agent so it reloads MCP tools")
                print("- open the chat from this repo or mention a registered repo name")
            if result.get("warnings"):
                print()
                print("Warnings:")
                for warning in result["warnings"]:
                    print(f"- {warning}")
    elif args.cmd == "verify-agent":
        if args.json:
            result = ws.verify_agent(
                agent=args.agent,
                wait_seconds=args.wait,
                repair=args.repair,
            )
            print(json.dumps(result, indent=2))
        else:
            if args.repair:
                repair_result = ws.heal(agents=args.agent)
                print("Memographix repair complete.")
                if repair_result["remaining_issues"]:
                    print("remaining_issues:")
                    for issue in repair_result["remaining_issues"]:
                        print(f"- {issue}")
            started = ws.start_agent_verification(agent=args.agent)
            print("Memographix agent verification")
            print(f"agent: {started['agent']}")
            print(f"verification_id: {started['verification_id']}")
            print(f"strict_mode: {started['strict_mode']}")
            print()
            print("Paste this prompt into the AI agent you want to verify:")
            print()
            print(started["prompt"])
            if args.wait > 0:
                print()
                print(f"Waiting up to {args.wait}s for real MCP calls...")
            result = ws.wait_agent_verification(
                started["verification_id"],
                agent=args.agent,
                wait_seconds=args.wait,
            )
            print()
            print(f"status: {result['status']}")
            if result["reason"]:
                print(f"reason: {result['reason']}")
            print(f"resolve_events: {result['resolve_events']}")
            print(f"capture_events: {result['capture_events']}")
    elif args.cmd == "guard":
        result = ws.guard(since_hours=_parse_since_hours(args.since))
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Memographix guard")
            print(f"status: {result['status']}")
            print(f"ok: {result['ok']}")
            if result.get("reason"):
                print(f"reason: {result['reason']}")
            if result.get("issues"):
                print("issues:")
                for issue in result["issues"]:
                    print(f"- {issue}")
            if result.get("modified_files"):
                print("modified_files:")
                for path in result["modified_files"]:
                    print(f"- {path}")
    elif args.cmd == "export":
        out = ws.write_export(args.out)
        print(f"Exported {out.resolve()}")
    elif args.cmd == "repos":
        result = {"repos": ws.repos()}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if not result["repos"]:
                print("No Memographix repos registered.")
            for item in result["repos"]:
                print(f"{item['name']}: {item['root']}")
    elif args.cmd == "repair":
        result = ws.repair_mcp(agents=args.agents)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Removed {result['removed_entries']} stale Memographix MCP entrie(s).")
            print(f"Refreshed {result['refreshed_entries']} Memographix MCP entrie(s).")
            for action in result["actions"]:
                if action["removed"]:
                    print(f"- {action['path']}: {', '.join(action['removed'])}")
    elif args.cmd == "heal":
        result = ws.heal(agents=args.agents)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Memographix heal complete.")
            print(f"ok: {result['ok']}")
            print(f"agents: {', '.join(result['agents'])}")
            if result["remaining_issues"]:
                print("remaining_issues:")
                for issue in result["remaining_issues"]:
                    print(f"- {issue}")
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


def _parse_since_hours(value: str) -> int:
    clean = value.strip().lower()
    multiplier = 1
    if clean.endswith("h"):
        clean = clean[:-1]
    elif clean.endswith("d"):
        clean = clean[:-1]
        multiplier = 24
    try:
        amount = int(clean)
    except ValueError as exc:
        raise SystemExit(f"invalid --since value: {value!r}") from exc
    return max(0, amount * multiplier)
