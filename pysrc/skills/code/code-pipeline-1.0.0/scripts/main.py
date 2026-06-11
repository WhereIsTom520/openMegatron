#!/usr/bin/env python3
"""
Code Pipeline v1.0.0 — automated coding agent loop.

UNDERSTAND → PLAN → SNAPSHOT → IMPLEMENT → VERIFY → REPORT
"""

from __future__ import annotations

import json
import sys
import time
import hashlib
from pathlib import Path

_skills_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_skills_root) not in sys.path:
    sys.path.insert(0, str(_skills_root))

from code.code_common import (
    fingerprint_project,
    extract_symbols,
    infer_commands,
    run_command,
    git_snapshot,
    git_diff,
    git_branch_info,
    git_restore_snapshot,
    search_code,
    scan_dangerous_patterns,
    analyze_complexity,
    GitSnapshot,
    DEFAULT_EXCLUDES,
)


def _phase(label: str, step: int, total: int):
    print(json.dumps({"phase": label, "step": step, "total": total, "timestamp": time.time()}))


def _result(ok: bool, data: dict) -> dict:
    data["ok"] = ok
    return data


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else []
    if not args:
        print(json.dumps({"error": "Usage: code_pipeline run '<task description>' [--auto-fix] [--max-files N] [--language py|ts|rs|go]"}))
        sys.exit(1)

    action = args[0]
    task_start = time.time()
    root = Path.cwd()
    plan_id = hashlib.sha1(f"{action}:{str(args)}:{task_start}".encode()).hexdigest()[:12]

    # Parse options
    task_desc = ""
    mode = "full"
    auto_fix = False
    max_files = 5
    language = ""

    i = 1
    while i < len(args):
        a = args[i]
        if a == "--auto-fix":
            auto_fix = True
        elif a == "--max-files":
            i += 1
            if i < len(args) and args[i].isdigit():
                max_files = int(args[i])
        elif a == "--language":
            i += 1
            if i < len(args):
                language = args[i]
        elif not a.startswith("--"):
            task_desc += " " + a
        i += 1
    task_desc = task_desc.strip()

    if not task_desc and action in ("run", "plan_only"):
        print(json.dumps({"error": "No task description provided."}))
        sys.exit(1)

    TOTAL_PHASES = 6
    step = 0
    snapshot_ref = None
    files_modified = []
    results = {"plan_id": plan_id, "task": task_desc, "phases": {}}

    try:
        # ═══════════════════════════════════════════════
        # PHASE 1: UNDERSTAND
        # ═══════════════════════════════════════════════
        step += 1
        _phase("UNDERSTAND", step, TOTAL_PHASES)

        # 1a. Project fingerprint
        fp = fingerprint_project(str(root))
        if language:
            fp.language = {"py": "Python", "ts": "TypeScript", "js": "JavaScript",
                          "rs": "Rust", "go": "Go"}.get(language, fp.language)

        # 1b. Search for relevant files using task keywords
        keywords = [w for w in task_desc.lower().replace(",", " ").replace("，", " ").split()
                    if len(w) > 2 and w not in {"the", "and", "for", "with", "that", "this"}]
        relevant_files = []
        for kw in keywords[:5]:
            hits = search_code(str(root), kw, max_results=10)
            for h in hits:
                if h["file"] not in relevant_files:
                    relevant_files.append(h["file"])

        # 1c. Extract symbols from top relevant files
        symbols = {}
        for f in relevant_files[:max_files * 2]:
            syms = extract_symbols(str(root / f), fp.language)
            if syms:
                symbols[f] = [{"name": s.name, "kind": s.kind, "line": s.line} for s in syms[:30]]

        results["phases"]["understand"] = {
            "project": {
                "name": fp.name, "language": fp.language, "framework": fp.framework,
                "test_framework": fp.test_framework, "lint_tools": fp.lint_tools,
                "source_dirs": fp.source_dirs, "test_dirs": fp.test_dirs,
                "total_files": fp.total_files, "total_lines": fp.total_lines,
            },
            "relevant_files": relevant_files[:20],
            "symbols": symbols,
        }

        if action == "plan_only" or mode == "plan_only":
            # Stop here and return the plan
            step += 1
            _phase("PLAN", step, TOTAL_PHASES)
            results["phases"]["plan"] = {
                "estimated_files": min(len(relevant_files), max_files),
                "edit_order": relevant_files[:max_files],
                "test_files": [f for f in relevant_files if any(p in f for p in ["test_", "_test", ".test.", ".spec.", "__test"])][:5],
                "commands": infer_commands(str(root)),
                "note": "plan_only mode — no files were modified. Use 'resume' to execute this plan.",
            }
            results["phases"]["plan"]["plan_id"] = plan_id
            print(json.dumps(_result(True, results), indent=2, ensure_ascii=False))
            return

        # ═══════════════════════════════════════════════
        # PHASE 2: PLAN
        # ═══════════════════════════════════════════════
        step += 1
        _phase("PLAN", step, TOTAL_PHASES)

        edit_plan = relevant_files[:max_files]
        test_files = [f for f in edit_plan if any(p in f for p in ["test_", "_test", ".test.", ".spec.", "__test"])]
        commands = infer_commands(str(root))

        results["phases"]["plan"] = {
            "estimated_files": len(edit_plan),
            "edit_order": edit_plan,
            "test_files": test_files,
            "commands": commands,
            "plan_id": plan_id,
        }

        # ═══════════════════════════════════════════════
        # PHASE 3: SNAPSHOT
        # ═══════════════════════════════════════════════
        step += 1
        _phase("SNAPSHOT", step, TOTAL_PHASES)

        # Check if git is available
        git_root = None
        try:
            import subprocess
            r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5, cwd=str(root))
            if r.returncode == 0:
                git_root = r.stdout.strip()
        except Exception:
            pass

        if git_root:
            snap = git_snapshot(str(root), f"code-pipeline:{plan_id}:{task_desc[:60]}")
            snapshot_ref = snap.ref
            results["phases"]["snapshot"] = {"ref": snap.ref, "description": snap.description, "timestamp": snap.timestamp}
        else:
            results["phases"]["snapshot"] = {"warning": "No git repository — snapshot unavailable. Proceeding without safety net."}

        # ═══════════════════════════════════════════════
        # PHASE 4: IMPLEMENT
        # ═══════════════════════════════════════════════
        step += 1
        _phase("IMPLEMENT", step, TOTAL_PHASES)

        # In a real pipeline, this is where the LLM makes edits.
        # The pipeline provides the framework — the LLM (via the agent loop)
        # will call code_assistant edit / code_refactor for each file.
        # Here we record the phase as "ready for LLM-driven edits."
        results["phases"]["implement"] = {
            "status": "ready",
            "files_ready": edit_plan,
            "instruction": "The pipeline has completed UNDERSTAND + PLAN + SNAPSHOT. "
                          "The agent should now call code_assistant edit for each file in the plan. "
                          "After each edit, the agent should verify syntax. "
                          "When all edits are done, the pipeline continues to VERIFY.",
            "files_modified": [],
            "backups_created": [],
        }

        # ═══════════════════════════════════════════════
        # PHASE 5: VERIFY
        # ═══════════════════════════════════════════════
        step += 1
        _phase("VERIFY", step, TOTAL_PHASES)

        gates = {"lint": None, "typecheck": None, "test": None}
        gate_results = {}

        # 5a. Lint
        if commands.get("lint"):
            lint_result = run_command(commands["lint"][0], str(root), timeout=60)
            gates["lint"] = lint_result.get("ok", False)
            gate_results["lint"] = {
                "passed": gates["lint"],
                "command": commands["lint"][0],
                "output": (lint_result.get("stderr", "") or lint_result.get("stdout", ""))[:500],
            }

        # 5b. Typecheck
        if commands.get("typecheck"):
            tc_result = run_command(commands["typecheck"][0], str(root), timeout=60)
            gates["typecheck"] = tc_result.get("ok", False)
            gate_results["typecheck"] = {
                "passed": gates["typecheck"],
                "command": commands["typecheck"][0],
                "output": (tc_result.get("stderr", "") or tc_result.get("stdout", ""))[:500],
            }

        # 5c. Test
        if commands.get("test"):
            test_result = run_command(commands["test"][0], str(root), timeout=120)
            gates["test"] = test_result.get("ok", False)
            gate_results["test"] = {
                "passed": gates["test"],
                "command": commands["test"][0],
                "output": (test_result.get("stderr", "") or test_result.get("stdout", ""))[:1000],
            }

        all_gates_pass = all(v is not False for v in gates.values() if v is not None)

        results["phases"]["verify"] = {
            "all_pass": all_gates_pass,
            "gates": gates,
            "details": gate_results,
        }

        # ═══════════════════════════════════════════════
        # PHASE 6: REPORT
        # ═══════════════════════════════════════════════
        step += 1
        _phase("REPORT", step, TOTAL_PHASES)

        # 6a. Git diff
        diff_text = ""
        if git_root:
            diff_text = git_diff(str(root))

        # 6b. Complexity of modified files
        complexity = {}
        for f in files_modified or edit_plan[:3]:
            fp_path = root / f
            if fp_path.exists():
                cr = analyze_complexity(str(fp_path))
                if cr:
                    complexity[f] = {
                        "line_count": cr.line_count,
                        "avg_complexity": cr.avg_complexity,
                        "hotspots": len(cr.hotspots),
                    }

        # 6c. Summary
        duration = round(time.time() - task_start, 1)
        summary_lines = [
            f"Pipeline: {plan_id}",
            f"Task: {task_desc}",
            f"Duration: {duration}s",
            f"Files in plan: {len(edit_plan)}",
            f"Files modified: {len(files_modified)}",
            f"Snapshot: {snapshot_ref or 'N/A'}",
            "",
            "Quality Gates:",
        ]
        for gate_name, passed in gates.items():
            icon = "PASS" if passed else ("FAIL" if passed is False else "SKIP")
            summary_lines.append(f"  [{icon}] {gate_name}")

        if not all_gates_pass and auto_fix and snapshot_ref:
            summary_lines.append("")
            summary_lines.append("AUTO-FIX: Quality gates failed. Rollback reference preserved.")
            summary_lines.append(f"  To rollback: git stash pop {snapshot_ref}")

        results["phases"]["report"] = {
            "summary": "\n".join(summary_lines),
            "diff": diff_text[:5000] if diff_text else "(no changes detected)",
            "complexity": complexity,
            "duration_seconds": duration,
            "snapshot_ref": snapshot_ref,
            "all_gates_pass": all_gates_pass,
        }

        print(json.dumps(_result(all_gates_pass, results), indent=2, ensure_ascii=False))

    except Exception as exc:
        import traceback
        results["error"] = {"type": exc.__class__.__name__, "message": str(exc), "traceback": traceback.format_exc()[:2000]}
        results["snapshot_ref_for_rollback"] = snapshot_ref
        print(json.dumps(_result(False, results), indent=2, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
