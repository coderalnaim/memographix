from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import DEFAULT_STOPWORDS as STOPWORDS
from .config import DEFAULT_SUPPORTED_EXTENSIONS as SUPPORTED_EXTENSIONS
from .config import load_retrieval_config
from .models import ContextPacket, Evidence, Freshness, TaskMemory
from .security import (
    SENSITIVE_PATTERNS,
    SKIP_DIRS,
    SKIP_FILES,
    is_ignored,
    is_sensitive,
    load_ignore_patterns,
)
from .storage import connect, json_loads
from .tokens import estimate_tokens, trim_to_budget

PY_DEF_RE = re.compile(r"^\s*(async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)\s*([(:].*)?")
JS_DEF_RE = re.compile(
    r"^\s*(export\s+)?(async\s+)?(function|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
GO_DEF_RE = re.compile(r"^\s*(func|type)\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)")
RS_DEF_RE = re.compile(r"^\s*(pub\s+)?(fn|struct|enum|trait|impl)\s+([A-Za-z_][A-Za-z0-9_]*)?")
JAVA_DEF_RE = re.compile(
    r"^\s*(public|private|protected)?\s*(static\s+)?(class|interface|enum|void|[A-Za-z0-9_<>\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
IMPORT_RE = re.compile(
    r"^\s*(import\s+.+|from\s+\S+\s+import\s+.+|use\s+.+|require\s*\(.+|#include\s+[<\"].+[>\"]|package\s+\S+)"
)


@dataclass(slots=True)
class IndexStats:
    files: int
    symbols: int
    edges: int
    skipped_sensitive: int
    duration_ms: int

    def to_dict(self) -> dict[str, int]:
        return {
            "files": self.files,
            "symbols": self.symbols,
            "edges": self.edges,
            "skipped_sensitive": self.skipped_sensitive,
            "duration_ms": self.duration_ms,
        }


class LocalEngine:
    """Pure Python implementation used directly and as a fallback for native wheels."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state_dir = self.root / ".memographix"
        self.db_path = self.state_dir / "graph.sqlite"
        self.blob_dir = self.state_dir / "blobs"
        self.config = load_retrieval_config(self.root)

    def init(self) -> Path:
        with closing(connect(self.db_path)):
            pass
        return self.db_path

    def index(self) -> IndexStats:
        start = datetime.now(timezone.utc)
        with closing(connect(self.db_path)) as conn:
            records = self._scan_native(conn)
            if records is None:
                records = self._scan_python(conn)
            self._apply_index(conn, records)
        duration = datetime.now(timezone.utc) - start
        counts = self.stats()
        return IndexStats(
            files=counts["files"],
            symbols=counts["symbols"],
            edges=counts["edges"],
            skipped_sensitive=int(records.get("skipped_sensitive", 0)),
            duration_ms=int(duration.total_seconds() * 1000),
        )

    def _existing_file_map(self, conn) -> dict[str, dict[str, Any]]:
        return {
            row["path"]: {
                "hash": row["hash"],
                "size": int(row["size"]),
                "mtime": float(row["mtime"]),
            }
            for row in conn.execute("SELECT path, hash, size, mtime FROM files").fetchall()
        }

    def _scan_native(self, conn) -> dict[str, Any] | None:
        try:
            from . import _native
        except ImportError:
            return None
        config = {
            "supported_extensions": self.config.supported_extensions,
            "max_file_bytes": self.config.max_file_bytes,
            "skip_dirs": sorted(SKIP_DIRS),
            "skip_files": sorted(SKIP_FILES),
            "ignore_patterns": load_ignore_patterns(self.root),
            "sensitive_patterns": [pattern.pattern for pattern in SENSITIVE_PATTERNS],
            "known_files": self._existing_file_map(conn),
        }
        try:
            return json.loads(_native.scan_repo_config_json(str(self.root), json.dumps(config)))
        except (AttributeError, RuntimeError, json.JSONDecodeError):
            return None

    def _scan_python(self, conn) -> dict[str, Any]:
        existing = self._existing_file_map(conn)
        files: list[dict[str, Any]] = []
        symbols: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        for path in self._iter_files():
            rel = self._rel(path)
            stat = path.stat()
            lang = self.config.supported_extensions.get(path.suffix.lower(), "text")
            old = existing.get(rel)
            if old and old["size"] == stat.st_size and abs(old["mtime"] - stat.st_mtime) < 0.000001:
                files.append(
                    {
                        "path": rel,
                        "hash": old["hash"],
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "language": lang,
                        "unchanged": True,
                    }
                )
                continue
            digest = file_hash(path)
            file_symbols, file_edges = self._extract(path, rel, digest, lang)
            files.append(
                {
                    "path": rel,
                    "hash": digest,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "language": lang,
                    "unchanged": False,
                }
            )
            symbols.extend(_symbol_tuple_to_dict(row) for row in file_symbols)
            edges.extend(_edge_tuple_to_dict(row) for row in file_edges)
        return {
            "files": files,
            "symbols": symbols,
            "edges": edges,
            "skipped_sensitive": self._last_skipped_sensitive,
            "errors": [],
        }

    def _apply_index(self, conn, records: dict[str, Any]) -> None:
        now = _utc_now()
        files = list(records.get("files", []))
        seen = {str(row["path"]) for row in files}
        existing = set(self._existing_file_map(conn))
        removed = existing - seen
        for path in removed:
            conn.execute("DELETE FROM files WHERE path=?", (path,))
            conn.execute("DELETE FROM symbols WHERE path=?", (path,))
            conn.execute("DELETE FROM edges WHERE path=?", (path,))
        for row in files:
            rel = str(row["path"])
            digest = str(row["hash"])
            conn.execute(
                """
                INSERT INTO files(path, hash, size, mtime, language, indexed_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                    hash=excluded.hash,
                    size=excluded.size,
                    mtime=excluded.mtime,
                    language=excluded.language,
                    indexed_at=excluded.indexed_at
                """,
                (
                    rel,
                    digest,
                    int(row.get("size", 0)),
                    float(row.get("mtime", 0.0)),
                    str(row.get("language", "text")),
                    now,
                ),
            )
            if not bool(row.get("unchanged")):
                conn.execute("DELETE FROM symbols WHERE path=?", (rel,))
                conn.execute("DELETE FROM edges WHERE path=?", (rel,))
                self._write_blob(digest, self.root / rel)
        conn.executemany(
            """
            INSERT OR REPLACE INTO symbols(id, path, kind, name, line, signature, fingerprint)
            VALUES(?,?,?,?,?,?,?)
            """,
            [
                (
                    row["id"],
                    row["path"],
                    row["kind"],
                    row["name"],
                    int(row["line"]),
                    row["signature"],
                    row["fingerprint"],
                )
                for row in records.get("symbols", [])
            ],
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO edges(source, target, relation, path, line, confidence)
            VALUES(?,?,?,?,?,?)
            """,
            [
                (
                    row["source"],
                    row["target"],
                    row["relation"],
                    row["path"],
                    row.get("line"),
                    row.get("confidence", "EXTRACTED"),
                )
                for row in records.get("edges", [])
            ],
        )
        conn.commit()

    def remember(
        self,
        question: str,
        answer: str,
        evidence_paths: list[str] | None = None,
        validation: dict | None = None,
    ) -> int:
        with closing(connect(self.db_path)) as conn:
            normalized = self._normalize_intent(question)
            task_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            now = _utc_now()
            token_count = estimate_tokens(question + "\n" + answer)
            cur = conn.execute(
                """
                INSERT INTO tasks(task_hash, normalized_intent, question, answer, validation_json,
                                  token_count, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    task_hash,
                    normalized,
                    question,
                    answer,
                    json.dumps(validation or {}, sort_keys=True),
                    token_count,
                    now,
                    now,
                ),
            )
            task_id = int(cur.lastrowid)
            evidence = evidence_paths if evidence_paths is not None else self._infer_evidence(question, limit=5)
            for raw_path in evidence:
                ev = self._make_evidence(raw_path)
                conn.execute(
                    """
                    INSERT INTO task_evidence(task_id, path, hash, symbol_id, line_start, line_end, excerpt)
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        ev.path,
                        ev.hash,
                        ev.symbol,
                        ev.line_start,
                        ev.line_end,
                        ev.excerpt,
                    ),
                )
            conn.commit()
            return task_id

    def capture(
        self,
        question: str,
        answer: str,
        evidence: list[str] | None = None,
        changed_files: list[str] | None = None,
        commands: list[str] | None = None,
        tests: list[str] | None = None,
        outcome: str | None = None,
        resolve_event_id: int | None = None,
        source: str = "api",
        verification_id: str = "",
        agent: str = "",
    ) -> dict[str, Any]:
        verification_id = verification_id or _verification_id_from_text(question)
        validation_artifact = bool(commands or tests or outcome)
        candidate_paths = [*(evidence or []), *(changed_files or [])]
        if not candidate_paths and resolve_event_id:
            candidate_paths.extend(self._evidence_from_resolve_event(resolve_event_id))
        safe_evidence = self._safe_evidence_paths(candidate_paths)
        if not safe_evidence and validation_artifact:
            safe_evidence = self._safe_evidence_paths(self._infer_evidence(question, limit=5))
        if not safe_evidence:
            reason = (
                "no safe repo-local evidence file could be attached"
                if validation_artifact
                else "no evidence or validation artifact supplied"
            )
            self._record_event(
                event_type="capture_task",
                question=question,
                status="skipped",
                skipped_reason=reason,
                data={
                    "commands": commands or [],
                    "tests": tests or [],
                    "outcome": outcome or "",
                    "source": source,
                    "resolve_event_id": resolve_event_id,
                    "verification_id": verification_id,
                    "agent": agent,
                },
            )
            return {
                "saved": False,
                "task_id": None,
                "reason": reason,
                "evidence": [],
                "final_status_line": f"Memographix: not saved - {reason}",
            }
        validation = {
            "commands": commands or [],
            "tests": tests or [],
            "outcome": outcome or "",
            "capture": "automatic",
        }
        task_id = self.remember(
            question=question,
            answer=answer,
            evidence_paths=safe_evidence,
            validation=validation,
        )
        self._record_event(
            event_type="capture_task",
            question=question,
            status="saved",
            task_id=task_id,
            data={
                "evidence": safe_evidence,
                "validation": validation,
                "source": source,
                "resolve_event_id": resolve_event_id,
                "verification_id": verification_id,
                "agent": agent,
            },
        )
        return {
            "saved": True,
            "task_id": task_id,
            "reason": "",
            "evidence": safe_evidence,
            "final_status_line": "Memographix: saved task memory",
        }

    def record_resolve_event(
        self,
        packet: ContextPacket,
        *,
        source: str = "api",
        verification_id: str = "",
        agent: str = "",
    ) -> int:
        verification_id = verification_id or _verification_id_from_text(packet.question)
        baseline_tokens = self._baseline_tokens_for_evidence(packet.evidence)
        estimated_saved = (
            max(0, baseline_tokens - packet.estimated_tokens)
            if packet.status == Freshness.FRESH
            else 0
        )
        return self._record_event(
            event_type="resolve_task",
            question=packet.question,
            status=packet.status.value,
            task_id=packet.matched_task.id if packet.matched_task else None,
            packet_tokens=packet.estimated_tokens,
            baseline_tokens=baseline_tokens,
            estimated_saved_tokens=estimated_saved,
            stale_prevention=1 if packet.status == Freshness.STALE else 0,
            data={
                "confidence": packet.confidence,
                "warnings": packet.warnings,
                "evidence": [ev.to_dict() for ev in packet.evidence],
                "source": source,
                "verification_id": verification_id,
                "agent": agent,
            },
        )

    def record_capture_skip(self, question: str, reason: str) -> None:
        self._record_event(
            event_type="capture_task",
            question=question,
            status="skipped",
            skipped_reason=reason,
            data={"reason": reason},
        )

    def savings(self, since_days: int = 30) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=since_days)
        with closing(connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT * FROM memory_events WHERE created_at >= ? ORDER BY created_at DESC",
                (since.isoformat(),),
            ).fetchall()
            resolve_rows = [row for row in rows if row["event_type"] == "resolve_task"]
            capture_rows = [row for row in rows if row["event_type"] == "capture_task"]
            last_resolve = next((row for row in rows if row["event_type"] == "resolve_task"), None)
            last_capture = next((row for row in rows if row["event_type"] == "capture_task"), None)
            last_tool = next(
                (row for row in rows if row["event_type"] in {"resolve_task", "capture_task"}),
                None,
            )
            last_mcp = next(
                (
                    row
                    for row in rows
                    if row["event_type"] in {"resolve_task", "capture_task"}
                    and self._event_source(row) == "mcp"
                ),
                None,
            )
            task_counts: Counter[int] = Counter(
                int(row["task_id"])
                for row in resolve_rows
                if row["status"] == Freshness.FRESH.value and row["task_id"] is not None
            )
            top_tasks = []
            for task_id, hits in task_counts.most_common(5):
                task = conn.execute("SELECT question FROM tasks WHERE id=?", (task_id,)).fetchone()
                top_tasks.append(
                    {
                        "task_id": task_id,
                        "fresh_hits": hits,
                        "question": task["question"] if task else "",
                    }
                )
            result = {
                "estimated": True,
                "formula": "saved_tokens = raw_evidence_file_tokens - returned_packet_tokens",
                "since_days": since_days,
                "resolve_events": len(resolve_rows),
                "fresh_hits": sum(
                    1 for row in resolve_rows if row["status"] == Freshness.FRESH.value
                ),
                "new_contexts": sum(
                    1 for row in resolve_rows if row["status"] == Freshness.NEW.value
                ),
                "stale_preventions": sum(int(row["stale_prevention"]) for row in resolve_rows),
                "captures_saved": sum(1 for row in capture_rows if row["status"] == "saved"),
                "skipped_captures": sum(1 for row in capture_rows if row["status"] == "skipped"),
                "estimated_saved_tokens": sum(int(row["estimated_saved_tokens"]) for row in rows),
                "top_repeated_tasks": top_tasks,
                "last_resolve_at": last_resolve["created_at"] if last_resolve else "",
                "last_capture_at": last_capture["created_at"] if last_capture else "",
                "last_capture_status": last_capture["status"] if last_capture else "",
                "last_mcp_call_at": last_mcp["created_at"] if last_mcp else "",
                "last_tool_source": self._event_source(last_tool) if last_tool else "",
                "warnings": [],
            }
            if not resolve_rows and not capture_rows:
                result["diagnostic"] = (
                    "No agent tool calls recorded yet. Run `mgx doctor --live`, restart your "
                    "agent, and open the chat from this repo or mention a registered repo name."
                )
            elif last_resolve and (
                not last_capture
                or str(last_capture["created_at"]) < str(last_resolve["created_at"])
            ):
                result["warnings"].append(
                    "Memographix saw resolve_task but no later capture_task. The agent may be "
                    "retrieving context without saving completed work."
                )
            if self._recent_changed_files() and not capture_rows:
                result["warnings"].append(
                    "This repo has modified files but no capture_task event in the selected window."
                )
            return result

    def record_agent_verification_start(
        self,
        *,
        verification_id: str,
        agent: str,
        prompt: str,
    ) -> int:
        return self._record_event(
            event_type="agent_verification",
            question=f"Memographix agent verification {verification_id}",
            status="started",
            data={
                "verification_id": verification_id,
                "agent": agent,
                "prompt": prompt,
                "source": "cli",
            },
        )

    def record_agent_verification_result(
        self,
        *,
        verification_id: str,
        agent: str,
        status: str,
        reason: str = "",
    ) -> int:
        return self._record_event(
            event_type="agent_verification",
            question=f"Memographix agent verification {verification_id}",
            status=status,
            skipped_reason=reason,
            data={
                "verification_id": verification_id,
                "agent": agent,
                "reason": reason,
                "source": "cli",
            },
        )

    def verification_result(self, verification_id: str) -> dict[str, Any]:
        with closing(connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT * FROM memory_events ORDER BY created_at DESC",
            ).fetchall()
        matching = [row for row in rows if _event_verification_id(row) == verification_id]
        resolves = [
            row
            for row in matching
            if row["event_type"] == "resolve_task" and self._event_source(row) == "mcp"
        ]
        captures = [
            row
            for row in matching
            if row["event_type"] == "capture_task" and self._event_source(row) == "mcp"
        ]
        saved_capture = next((row for row in captures if row["status"] == "saved"), None)
        last_capture = captures[0] if captures else None
        agent = ""
        for row in matching:
            agent = str(json_loads(row["data_json"]).get("agent", ""))
            if agent:
                break
        verified = bool(resolves and saved_capture)
        return {
            "verification_id": verification_id,
            "agent": agent,
            "verified": verified,
            "status": "verified" if verified else "pending",
            "resolve_events": len(resolves),
            "capture_events": len(captures),
            "last_resolve_at": resolves[0]["created_at"] if resolves else "",
            "last_capture_at": last_capture["created_at"] if last_capture else "",
            "last_capture_status": last_capture["status"] if last_capture else "",
        }

    def verification_status(self) -> dict[str, Any]:
        with closing(connect(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT * FROM memory_events ORDER BY created_at DESC",
            ).fetchall()
        verified_capture = next(
            (
                row
                for row in rows
                if row["event_type"] == "capture_task"
                and row["status"] == "saved"
                and self._event_source(row) == "mcp"
                and _event_verification_id(row)
            ),
            None,
        )
        last_verification = next(
            (row for row in rows if row["event_type"] == "agent_verification"),
            None,
        )
        warning = ""
        if last_verification and last_verification["status"] != "verified":
            warning = str(last_verification["skipped_reason"] or last_verification["status"])
        data = json_loads(verified_capture["data_json"]) if verified_capture else {}
        return {
            "agent_verified": verified_capture is not None,
            "last_verified_agent_at": verified_capture["created_at"] if verified_capture else "",
            "last_verified_agent": str(data.get("agent", "")) if data else "",
            "last_verification_id": str(data.get("verification_id", "")) if data else "",
            "last_unverified_warning": warning,
        }

    def recent_changed_files(self) -> set[str]:
        return self._recent_changed_files()

    def _record_event(
        self,
        event_type: str,
        question: str,
        status: str,
        task_id: int | None = None,
        packet_tokens: int = 0,
        baseline_tokens: int = 0,
        estimated_saved_tokens: int = 0,
        stale_prevention: int = 0,
        skipped_reason: str = "",
        data: dict[str, Any] | None = None,
    ) -> int:
        with closing(connect(self.db_path)) as conn:
            cursor = conn.execute(
                """
                INSERT INTO memory_events(
                    event_type, question, status, task_id, packet_tokens, baseline_tokens,
                    estimated_saved_tokens, stale_prevention, skipped_reason, data_json, created_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_type,
                    question,
                    status,
                    task_id,
                    packet_tokens,
                    baseline_tokens,
                    estimated_saved_tokens,
                    stale_prevention,
                    skipped_reason,
                    json.dumps(data or {}, sort_keys=True),
                    _utc_now(),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def recall(self, question: str, budget: int = 800) -> ContextPacket:
        match = self._best_task(question)
        if match and match.score >= self.config.task_match_threshold:
            match.evidence = self._task_evidence(match.id)
            statuses = {ev.status for ev in match.evidence}
            if Freshness.STALE in statuses or Freshness.MISSING in statuses:
                warnings = [
                    "Prior task memory matched, but one or more evidence files changed or disappeared.",
                    "The prior answer is not reused as authoritative context until the evidence is repaired.",
                ]
                context = self._stale_context(match, budget)
                return ContextPacket(
                    question=question,
                    status=Freshness.STALE,
                    token_budget=budget,
                    estimated_tokens=estimate_tokens(context),
                    summary="Matched prior task, but freshness validation failed.",
                    confidence=match.score,
                    matched_task=match,
                    evidence=match.evidence,
                    warnings=warnings,
                    context=context,
                )
            context = self._fresh_context(match, budget)
            return ContextPacket(
                question=question,
                status=Freshness.FRESH,
                token_budget=budget,
                estimated_tokens=estimate_tokens(context),
                summary="Fresh repeated-task memory found.",
                confidence=match.score,
                matched_task=match,
                evidence=match.evidence,
                context=context,
            )
        context, evidence = self._graph_context(question, budget)
        confidence = max((similarity(self._term_set(question), self._term_set(ev.excerpt)) for ev in evidence), default=0.0)
        return ContextPacket(
            question=question,
            status=Freshness.NEW,
            token_budget=budget,
            estimated_tokens=estimate_tokens(context),
            summary="No strong prior task memory found; returned repo graph context.",
            confidence=confidence,
            evidence=evidence,
            context=context,
        )

    def changed(self) -> list[TaskMemory]:
        with closing(connect(self.db_path)) as conn:
            tasks = []
            for row in conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall():
                task = self._task_from_row(row, score=1.0)
                task.evidence = self._task_evidence(task.id)
                if any(e.status != Freshness.FRESH for e in task.evidence):
                    tasks.append(task)
            return tasks

    def stats(self) -> dict[str, int]:
        with closing(connect(self.db_path)) as conn:
            return {
                "files": conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
                "symbols": conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
                "edges": conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
                "tasks": conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            }

    def last_indexed_at(self) -> str:
        with closing(connect(self.db_path)) as conn:
            row = conn.execute("SELECT MAX(indexed_at) AS last_indexed_at FROM files").fetchone()
            return row["last_indexed_at"] or ""

    def export_json(self) -> dict:
        with closing(connect(self.db_path)) as conn:
            return {
                "root": str(self.root),
                "stats": self.stats(),
                "files": [dict(r) for r in conn.execute("SELECT * FROM files ORDER BY path").fetchall()],
                "symbols": [dict(r) for r in conn.execute("SELECT * FROM symbols ORDER BY path,line").fetchall()],
                "edges": [dict(r) for r in conn.execute("SELECT * FROM edges ORDER BY path,line").fetchall()],
                "tasks": [dict(r) for r in conn.execute("SELECT * FROM tasks ORDER BY updated_at").fetchall()],
            }

    def _iter_files(self) -> Iterable[Path]:
        patterns = load_ignore_patterns(self.root)
        skipped_sensitive = 0
        for dirpath, dirnames, filenames in os.walk(self.root):
            dp = Path(dirpath)
            dirnames[:] = [
                d
                for d in dirnames
                if d not in SKIP_DIRS
                and not d.startswith(".cache")
                and not is_ignored(dp / d, self.root, patterns)
            ]
            for filename in filenames:
                if filename in SKIP_FILES:
                    continue
                path = dp / filename
                if path.suffix.lower() not in self.config.supported_extensions:
                    continue
                if is_ignored(path, self.root, patterns):
                    continue
                if is_sensitive(path):
                    skipped_sensitive += 1
                    continue
                try:
                    if path.stat().st_size > self.config.max_file_bytes:
                        continue
                except OSError:
                    continue
                yield path
        self._last_skipped_sensitive = skipped_sensitive

    _last_skipped_sensitive = 0

    def _extract(self, path: Path, rel: str, digest: str, lang: str) -> tuple[list[tuple], list[tuple]]:
        lines = read_text(path).splitlines()
        symbols: list[tuple] = []
        edges: list[tuple] = []
        file_id = stable_id("file", rel)
        symbols.append((file_id, rel, "file", Path(rel).name, 1, rel, digest[:16]))
        current_container = file_id
        defined_names: dict[str, str] = {}
        for idx, line in enumerate(lines, start=1):
            found = self._extract_symbol_from_line(line, lang)
            if found:
                kind, name, signature = found
                symbol_id = stable_id(kind, rel, name, str(idx))
                fingerprint = hashlib.sha256(f"{digest}:{idx}:{signature}".encode()).hexdigest()[:16]
                symbols.append((symbol_id, rel, kind, name, idx, signature.strip(), fingerprint))
                edges.append((file_id, symbol_id, "contains", rel, idx, "EXTRACTED"))
                current_container = symbol_id
                defined_names[name.lower()] = symbol_id
            imp = IMPORT_RE.match(line)
            if imp:
                target = stable_id("external", normalize_import(imp.group(1)))
                edges.append((file_id, target, "imports", rel, idx, "EXTRACTED"))
            for call in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", line):
                target = defined_names.get(call.lower())
                if target and target != current_container:
                    edges.append((current_container, target, "calls", rel, idx, "INFERRED"))
        return symbols, edges

    def _extract_symbol_from_line(self, line: str, lang: str) -> tuple[str, str, str] | None:
        if lang == "python":
            m = PY_DEF_RE.match(line)
            if m:
                kind = "class" if m.group(1) == "class" else "function"
                return kind, m.group(2), line.strip()
        if lang in {"javascript", "typescript"}:
            m = JS_DEF_RE.match(line)
            if m:
                return m.group(3), m.group(4), line.strip()
        if lang == "go":
            m = GO_DEF_RE.match(line)
            if m:
                return m.group(1), m.group(2), line.strip()
        if lang == "rust":
            m = RS_DEF_RE.match(line)
            if m and m.group(3):
                return m.group(2), m.group(3), line.strip()
        if lang in {"java", "csharp", "kotlin", "swift"}:
            m = JAVA_DEF_RE.match(line)
            if m:
                return m.group(3), m.group(4), line.strip()
        return None

    def _best_task(self, question: str) -> TaskMemory | None:
        with closing(connect(self.db_path)) as conn:
            q_terms = self._term_set(question)
            rows = conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
            best: TaskMemory | None = None
            for row in rows:
                score = similarity(q_terms, self._term_set(row["normalized_intent"]))
                raw_question = row["question"].lower()
                if question.lower().strip() in raw_question or raw_question in question.lower().strip():
                    score += 0.25
                task = self._task_from_row(row, score=min(score, 1.0))
                if best is None or task.score > best.score:
                    best = task
            return best

    def _task_from_row(self, row, score: float) -> TaskMemory:
        return TaskMemory(
            id=int(row["id"]),
            normalized_intent=row["normalized_intent"],
            question=row["question"],
            answer=row["answer"],
            validation=json_loads(row["validation_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            score=score,
        )

    def _task_evidence(self, task_id: int) -> list[Evidence]:
        with closing(connect(self.db_path)) as conn:
            evidence: list[Evidence] = []
            for row in conn.execute(
                "SELECT * FROM task_evidence WHERE task_id=? ORDER BY id",
                (task_id,),
            ).fetchall():
                path = row["path"]
                full = self.root / path
                stored_hash = row["hash"]
                if not full.exists():
                    status = Freshness.MISSING
                    current = None
                else:
                    current = file_hash(full)
                    status = Freshness.FRESH if current == stored_hash else Freshness.STALE
                evidence.append(
                    Evidence(
                        path=path,
                        hash=stored_hash,
                        current_hash=current,
                        status=status,
                        symbol=row["symbol_id"],
                        line_start=row["line_start"],
                        line_end=row["line_end"],
                        excerpt=row["excerpt"],
                    )
                )
            return evidence

    def _fresh_context(self, task: TaskMemory, budget: int) -> str:
        lines = [
            "MEMOGRAPHIX_TASK_CAPSULE",
            f"status: fresh",
            f"match_score: {task.score:.3f}",
            f"prior_question: {task.question}",
            f"validation: {json.dumps(task.validation, sort_keys=True)}",
            "",
            "prior_answer:",
            task.answer,
            "",
            "fresh_evidence:",
        ]
        for index, ev in enumerate(task.evidence):
            loc = f":{ev.line_start}" if ev.line_start else ""
            lines.append(f"- {ev.path}{loc} hash={ev.hash}")
            if ev.excerpt and index < 4:
                lines.append(indent(trim_to_budget(ev.excerpt, 70), "  "))
        capsule_budget = min(budget, max(160, int(budget * 0.9)))
        return trim_to_budget("\n".join(lines), capsule_budget)

    def _stale_context(self, task: TaskMemory, budget: int) -> str:
        lines = [
            "MEMOGRAPHIX_STALE_TASK_MATCH",
            f"status: stale",
            f"match_score: {task.score:.3f}",
            f"prior_question: {task.question}",
            "",
            "Do not reuse the prior answer as fact. Inspect changed evidence below.",
            "",
            "evidence_status:",
        ]
        for ev in task.evidence:
            lines.append(
                f"- {ev.path} status={ev.status.value} stored={ev.hash} current={ev.current_hash}"
            )
        return trim_to_budget("\n".join(lines), budget)

    def _graph_context(self, question: str, budget: int) -> tuple[str, list[Evidence]]:
        with closing(connect(self.db_path)) as conn:
            terms = self._term_set(question)
            rows = conn.execute("SELECT * FROM symbols ORDER BY path,line").fetchall()
            edge_terms = self._edge_terms_by_path(conn)
            changed_files = self._recent_changed_files()
            file_hashes = {
                row["path"]: row["hash"]
                for row in conn.execute("SELECT path, hash FROM files").fetchall()
            }
            scored_by_path: dict[str, tuple[float, dict]] = {}
            for row in rows:
                text = f"{row['path']} {row['name']} {row['signature']} {edge_terms.get(row['path'], '')}"
                score = similarity(terms, self._term_set(text))
                if row["path"] in changed_files:
                    score += 0.08
                if score > 0:
                    existing = scored_by_path.get(row["path"])
                    if (
                        existing is None
                        or score > existing[0]
                        or (existing[1]["kind"] == "file" and row["kind"] != "file")
                    ):
                        scored_by_path[row["path"]] = (score, dict(row))
            scored = sorted(scored_by_path.values(), key=lambda x: x[0], reverse=True)
            top = scored[:12]
            lines = ["MEMOGRAPHIX_REPO_CONTEXT", "status: no_prior_task", "", "relevant_symbols:"]
            evidence: list[Evidence] = []
            for score, row in top:
                lines.append(
                    f"- {row['kind']} {row['name']} in {row['path']}:{row['line']} score={score:.3f}"
                )
                evidence.append(
                    Evidence(
                        path=row["path"],
                        hash=file_hashes.get(row["path"]),
                        status=Freshness.FRESH,
                        symbol=row["id"],
                        line_start=row["line"],
                        excerpt=row["signature"],
                    )
                )
            if not top:
                lines.append("- no direct symbol match; index may need richer language support")
            return trim_to_budget("\n".join(lines), budget), evidence

    def _edge_terms_by_path(self, conn) -> dict[str, str]:
        terms: dict[str, list[str]] = {}
        for row in conn.execute("SELECT path, relation, target FROM edges").fetchall():
            terms.setdefault(row["path"], []).append(f"{row['relation']} {row['target']}")
        return {path: " ".join(values) for path, values in terms.items()}

    def _recent_changed_files(self) -> set[str]:
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=self.root,
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return set()
        if proc.returncode != 0:
            return set()
        return {line.strip() for line in proc.stdout.splitlines() if line.strip()}

    def _file_hash_for_path(self, rel: str) -> str | None:
        with closing(connect(self.db_path)) as conn:
            row = conn.execute("SELECT hash FROM files WHERE path=?", (rel,)).fetchone()
            return row["hash"] if row else None

    def _infer_evidence(self, question: str, limit: int) -> list[str]:
        _context, evidence = self._graph_context(question, 1200)
        paths = []
        for ev in evidence:
            if ev.path not in paths:
                paths.append(ev.path)
            if len(paths) >= limit:
                break
        return paths

    def _evidence_from_resolve_event(self, event_id: int) -> list[str]:
        with closing(connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT data_json FROM memory_events WHERE id=? AND event_type='resolve_task'",
                (event_id,),
            ).fetchone()
        if not row:
            return []
        data = json_loads(row["data_json"])
        paths = []
        for item in data.get("evidence", []) or []:
            if isinstance(item, dict) and item.get("path"):
                paths.append(str(item["path"]))
        return paths

    def _event_source(self, row: Any) -> str:
        if row is None:
            return ""
        return str(json_loads(row["data_json"]).get("source", ""))

    def _make_evidence(self, raw_path: str) -> Evidence:
        path = Path(raw_path)
        if path.is_absolute():
            try:
                rel = path.resolve().relative_to(self.root)
            except ValueError:
                rel = Path(path.name)
        else:
            rel = path
        full = self.root / rel
        if not full.exists():
            return Evidence(path=str(rel), status=Freshness.MISSING)
        digest = file_hash(full)
        excerpt = excerpt_file(full)
        return Evidence(
            path=str(rel),
            hash=digest,
            current_hash=digest,
            status=Freshness.FRESH,
            line_start=1,
            line_end=min(20, len(excerpt.splitlines())),
            excerpt=excerpt,
        )

    def _safe_evidence_paths(self, raw_paths: list[str]) -> list[str]:
        patterns = load_ignore_patterns(self.root)
        safe: list[str] = []
        for raw_path in raw_paths:
            try:
                path = Path(raw_path)
                full = path.resolve() if path.is_absolute() else (self.root / path).resolve()
                rel = full.relative_to(self.root)
            except (OSError, ValueError):
                continue
            if str(rel).startswith("..") or not full.exists() or not full.is_file():
                continue
            if full.name in SKIP_FILES or is_sensitive(full) or is_ignored(full, self.root, patterns):
                continue
            try:
                if full.stat().st_size > self.config.max_file_bytes:
                    continue
            except OSError:
                continue
            rel_text = str(rel).replace(os.sep, "/")
            if rel_text not in safe:
                safe.append(rel_text)
        return safe

    def _baseline_tokens_for_evidence(self, evidence: list[Evidence]) -> int:
        total = 0
        for ev in evidence:
            if ev.status != Freshness.FRESH:
                continue
            try:
                full = (self.root / ev.path).resolve()
                full.relative_to(self.root)
            except (OSError, ValueError):
                continue
            if full.exists() and full.is_file() and not is_sensitive(full):
                total += estimate_tokens(read_text(full))
        return total

    def _write_blob(self, digest: str, path: Path) -> None:
        blob = self.blob_dir / digest[:2] / digest
        if blob.exists():
            return
        blob.parent.mkdir(parents=True, exist_ok=True)
        try:
            blob.write_bytes(path.read_bytes())
        except OSError:
            return

    def _rel(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root)).replace(os.sep, "/")

    def _normalize_intent(self, text: str) -> str:
        return " ".join(sorted(self._term_set(text)))

    def _term_set(self, text: str) -> set[str]:
        return term_set(text, stopwords=self.config.stopwords)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_id(*parts: str) -> str:
    raw = "::".join(parts)
    readable = re.sub(r"[^A-Za-z0-9]+", "_", "_".join(parts[:3])).strip("_").lower()[:80]
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{readable}_{digest}" if readable else digest


def normalize_import(text: str) -> str:
    text = text.strip().strip(";")
    text = re.sub(r"^(from|import|use|package)\s+", "", text)
    text = text.replace(" import ", ".")
    return re.split(r"\s|,|\(|;", text)[0].strip("\"'<>")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def excerpt_file(path: Path, max_lines: int = 20) -> str:
    lines = read_text(path).splitlines()[:max_lines]
    return "\n".join(lines)


def normalize_intent(text: str) -> str:
    terms = sorted(term_set(text))
    return " ".join(terms)


def term_set(text: str, stopwords: frozenset[str] | set[str] = STOPWORDS) -> set[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    terms = {
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", expanded.lower())
        if token not in stopwords
    }
    # Keep snake_case parts too, since dev tasks often reference symbols.
    split_terms = set()
    for term in terms:
        split_terms.update(part for part in term.split("_") if len(part) > 2 and part not in stopwords)
    expanded_terms = terms | split_terms
    singular_terms = {
        term[:-1]
        for term in expanded_terms
        if len(term) > 4 and term.endswith("s") and term[:-1] not in stopwords
    }
    return expanded_terms | singular_terms


def similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    containment = intersection / max(1, min(len(a), len(b)))
    jaccard = intersection / union
    return (0.65 * containment) + (0.35 * jaccard)


def keyword_counts(text: str) -> Counter:
    return Counter(term_set(text))


def indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def _event_verification_id(row: Any) -> str:
    if row is None:
        return ""
    return str(json_loads(row["data_json"]).get("verification_id", ""))


def _verification_id_from_text(text: str) -> str:
    match = re.search(r"\bmgx-verify-[a-f0-9]{12}\b", text, flags=re.IGNORECASE)
    return match.group(0).lower() if match else ""


def _symbol_tuple_to_dict(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0],
        "path": row[1],
        "kind": row[2],
        "name": row[3],
        "line": row[4],
        "signature": row[5],
        "fingerprint": row[6],
    }


def _edge_tuple_to_dict(row: tuple) -> dict[str, Any]:
    return {
        "source": row[0],
        "target": row[1],
        "relation": row[2],
        "path": row[3],
        "line": row[4],
        "confidence": row[5],
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
