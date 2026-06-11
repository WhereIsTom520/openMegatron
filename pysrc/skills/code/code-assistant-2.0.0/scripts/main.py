#!/usr/bin/env python3
"""code-assistant v2.0.0 — full-stack code engineering entry point."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add skills root to path for code_common import
_skills_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_skills_root) not in sys.path:
    sys.path.insert(0, str(_skills_root))

from code.code_common import (
    fingerprint_project,
    extract_symbols,
    extract_dependencies,
    infer_commands,
    run_command,
    edit_file,
    replace_all_in_file,
    scan_secrets,
    scan_dangerous_patterns,
    analyze_complexity,
    search_code,
    git_diff,
    git_log,
    git_snapshot,
    git_restore_snapshot,
    git_branch_info,
    git_files_changed,
    generate_changelog,
    GitSnapshot,
)


def _find_files(root: str, pattern: str = "*") -> list[str]:
    """Glob files, excluding known noise directories."""
    from code.code_common import DEFAULT_EXCLUDES, _is_excluded
    rp = Path(root).resolve()
    results = []
    for f in rp.rglob(pattern):
        if f.is_file() and not _is_excluded(f, rp):
            results.append(str(f.relative_to(rp)))
    return results


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No action specified. Use: inspect, search, read, edit, replace_all, build, test, lint, format, scan_secrets, scan_dangerous, complexity, deps, symbols, git_diff, git_log, git_snapshot, git_restore, git_branch, git_changed, changelog, commands, stack"}))
        sys.exit(1)

    action = sys.argv[1]
    args = sys.argv[2:]
    root = Path.cwd()

    try:
        # ── Repository Understanding ──────────────────
        if action in ("inspect", "stack"):
            fp = fingerprint_project(str(root))
            print(json.dumps({
                "name": fp.name,
                "root": fp.root,
                "language": fp.language,
                "framework": fp.framework,
                "package_manager": fp.package_manager,
                "build_tool": fp.build_tool,
                "test_framework": fp.test_framework,
                "lint_tools": fp.lint_tools,
                "source_dirs": fp.source_dirs,
                "test_dirs": fp.test_dirs,
                "entry_points": fp.entry_points,
                "total_files": fp.total_files,
                "total_lines": fp.total_lines,
            }, indent=2, ensure_ascii=False))

        # ── Search ────────────────────────────────────
        elif action == "search":
            pattern = args[0] if args else ""
            if not pattern:
                print(json.dumps({"error": "search requires a pattern"}))
                sys.exit(1)
            glob = args[1] if len(args) > 1 else "*"
            case_sensitive = "--case" in args
            results = search_code(str(root), pattern, glob=glob, case_sensitive=case_sensitive, max_results=80)
            print(json.dumps({"count": len(results), "results": results}, indent=2, ensure_ascii=False))

        # ── Read ──────────────────────────────────────
        elif action == "read":
            filepath = args[0] if args else ""
            if not filepath:
                print(json.dumps({"error": "read requires a file path"}))
                sys.exit(1)
            p = root / filepath
            if not p.exists():
                print(json.dumps({"error": f"File not found: {filepath}"}))
                sys.exit(1)
            content = p.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()
            start = int(args[1]) - 1 if len(args) > 1 and args[1].isdigit() else 0
            end = int(args[2]) if len(args) > 2 and args[2].isdigit() else len(lines)
            selected = lines[max(0, start):min(len(lines), end)]
            # Redact secrets
            from code.code_common import SECRET_PATTERNS
            redacted = "\n".join(selected)
            for pat, label in SECRET_PATTERNS:
                redacted = pat.sub(f"[REDACTED {label}]", redacted)
            print(json.dumps({
                "file": filepath,
                "total_lines": len(lines),
                "start_line": max(0, start) + 1,
                "end_line": min(len(lines), end),
                "content": redacted,
            }, indent=2, ensure_ascii=False))

        # ── Edit ──────────────────────────────────────
        elif action == "edit":
            if len(args) < 3:
                print(json.dumps({"error": "edit requires: <file> <old_string> <new_string>"}))
                sys.exit(1)
            result = edit_file(str(root / args[0]), args[1], " ".join(args[2:]))
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif action == "replace_all":
            if len(args) < 3:
                print(json.dumps({"error": "replace_all requires: <file> <old_string> <new_string>"}))
                sys.exit(1)
            result = replace_all_in_file(str(root / args[0]), args[1], " ".join(args[2:]))
            print(json.dumps(result, indent=2, ensure_ascii=False))

        # ── Build / Test / Lint / Format ──────────────
        elif action in ("build", "test", "lint", "format", "typecheck"):
            cmds = infer_commands(str(root))
            cmd_list = cmds.get(action, [])
            if not cmd_list:
                print(json.dumps({"error": f"No {action} command inferred for this project"}))
                sys.exit(1)
            result = run_command(cmd_list[0], str(root))
            result["command"] = cmd_list[0]
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif action == "commands":
            cmds = infer_commands(str(root))
            print(json.dumps(cmds, indent=2, ensure_ascii=False))

        # ── Security ──────────────────────────────────
        elif action == "scan_secrets":
            target = root / args[0] if args else root
            files = _find_files(str(target)) if target.is_dir() else [str(target.relative_to(root))]
            all_findings = []
            for f in files[:200]:  # limit to 200 files
                all_findings.extend(scan_secrets(str(root / f)))
            print(json.dumps({"count": len(all_findings), "findings": all_findings}, indent=2, ensure_ascii=False))

        elif action == "scan_dangerous":
            target = root / args[0] if args else root
            files = _find_files(str(target)) if target.is_dir() else [str(target.relative_to(root))]
            all_findings = []
            for f in files[:200]:
                all_findings.extend(scan_dangerous_patterns(str(root / f)))
            print(json.dumps({"count": len(all_findings), "findings": all_findings}, indent=2, ensure_ascii=False))

        # ── Complexity ────────────────────────────────
        elif action == "complexity":
            if not args:
                print(json.dumps({"error": "complexity requires a file path"}))
                sys.exit(1)
            report = analyze_complexity(str(root / args[0]))
            if report:
                print(json.dumps({
                    "file": report.file,
                    "line_count": report.line_count,
                    "function_count": report.function_count,
                    "avg_complexity": report.avg_complexity,
                    "hotspots": report.hotspots,
                }, indent=2, ensure_ascii=False))
            else:
                print(json.dumps({"error": f"Could not analyze: {args[0]}"}))

        # ── Symbols & Dependencies ────────────────────
        elif action == "symbols":
            target = args[0] if args else "."
            p = root / target
            if p.is_file():
                syms = extract_symbols(str(p))
            else:
                syms = []
                for ext in [".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go"]:
                    for f in p.rglob(f"*{ext}"):
                        syms.extend(extract_symbols(str(f)))
            print(json.dumps({"count": len(syms), "symbols": [{"name": s.name, "kind": s.kind, "file": s.file, "line": s.line, "docstring": s.docstring[:100], "exported": s.exported} for s in syms[:200]]}, indent=2, ensure_ascii=False))

        elif action == "deps":
            deps = extract_dependencies(str(root))
            print(json.dumps({"count": len(deps), "dependencies": [{"name": d.name, "version": d.version, "is_dev": d.is_dev} for d in deps]}, indent=2, ensure_ascii=False))

        # ── Git ───────────────────────────────────────
        elif action == "git_diff":
            staged = "--staged" in args
            diff = git_diff(str(root), staged=staged)
            print(json.dumps({"diff": diff[:10000]}, indent=2, ensure_ascii=False))

        elif action == "git_log":
            count = int(args[0]) if args and args[0].isdigit() else 20
            commits = git_log(str(root), max_count=count)
            print(json.dumps(commits, indent=2, ensure_ascii=False))

        elif action == "git_snapshot":
            desc = " ".join(args) if args else ""
            snap = git_snapshot(str(root), description=desc)
            print(json.dumps({"ref": snap.ref, "description": snap.description, "timestamp": snap.timestamp}))

        elif action == "git_restore":
            ref = args[0] if args else "stash@{0}"
            snap = GitSnapshot(ref=ref, description="manual restore", timestamp=0)
            ok = git_restore_snapshot(snap, str(root))
            print(json.dumps({"ok": ok, "ref": ref}))

        elif action == "git_branch":
            info = git_branch_info(str(root))
            print(json.dumps(info, indent=2, ensure_ascii=False))

        elif action == "git_changed":
            since = args[0] if args else "HEAD~1"
            files = git_files_changed(since, str(root))
            print(json.dumps({"since": since, "count": len(files), "files": files}, indent=2, ensure_ascii=False))

        elif action == "changelog":
            cl = generate_changelog(str(root))
            print(json.dumps({"changelog": cl}, indent=2, ensure_ascii=False))

        else:
            print(json.dumps({"error": f"Unknown action: {action}"}))
            sys.exit(1)

    except Exception as exc:
        print(json.dumps({"error": f"{exc.__class__.__name__}: {exc}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
