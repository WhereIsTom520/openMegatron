#!/usr/bin/env python3
"""code-refactor v1.0.0 — safe git-backed refactoring."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

_skills_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_skills_root) not in sys.path:
    sys.path.insert(0, str(_skills_root))

from code.code_common import (
    git_snapshot,
    git_restore_snapshot,
    git_diff,
    edit_file,
    replace_all_in_file,
    extract_symbols,
    search_code,
    GitSnapshot,
    DEFAULT_EXCLUDES,
)


def _find_source_files(root: Path, exts: set[str] = None) -> list[Path]:
    exts = exts or {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java"}
    files = []
    for ext in exts:
        for f in root.rglob(f"*{ext}"):
            parts = set(f.relative_to(root).parts)
            if not (parts & DEFAULT_EXCLUDES):
                files.append(f)
    return files


def _auto_snapshot(root: Path) -> dict | None:
    """Create a snapshot if none exists in the last 5 minutes."""
    print(json.dumps({"status": "auto_snapshot", "message": "Creating safety snapshot before refactoring..."}))
    snap = git_snapshot(str(root), f"auto-refactor-{time.strftime('%Y%m%d-%H%M%S')}")
    return {"ref": snap.ref, "description": snap.description, "timestamp": snap.timestamp}


def _detect_dead_imports(filepath: Path) -> list[dict]:
    """Heuristic dead import detection."""
    findings = []
    try:
        content = filepath.read_text(encoding="utf-8")
        # Python: "import X" or "from X import Y" — check if X is used
        for m in re.finditer(r'^import\s+(\S+)', content, re.MULTILINE):
            name = m.group(1).split(" as ")[-1].strip()
            rest = content[m.end():]
            if name not in rest:
                findings.append({
                    "file": str(filepath), "line": content[:m.start()].count('\n') + 1,
                    "type": "unused_import", "name": name,
                })
        for m in re.finditer(r'^from\s+\S+\s+import\s+(.+)$', content, re.MULTILINE):
            imports = m.group(1)
            for imp in imports.split(","):
                imp_name = imp.strip().split(" as ")[-1].strip()
                rest = content[m.end():]
                if imp_name not in rest and imp_name != "*":
                    findings.append({
                        "file": str(filepath), "line": content[:m.start()].count('\n') + 1,
                        "type": "unused_import", "name": imp_name,
                    })
    except Exception:
        pass
    return findings


def _detect_unused_variables(filepath: Path) -> list[dict]:
    """Heuristic: variables assigned but never referenced after assignment."""
    findings = []
    try:
        content = filepath.read_text(encoding="utf-8")
        for m in re.finditer(r'(?:^|\s)(\w+)\s*=\s*\S+', content):
            var_name = m.group(1)
            if var_name in ("self", "cls", "this", "i", "j", "k", "_"):
                continue
            rest = content[m.end():]
            if var_name not in rest:
                findings.append({
                    "file": str(filepath), "line": content[:m.start()].count('\n') + 1,
                    "type": "unused_variable", "name": var_name,
                })
    except Exception:
        pass
    return findings


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No action. Use: snapshot, restore, rename_symbol, extract_function, dead_code, preview"}))
        sys.exit(1)

    action = sys.argv[1]
    args = sys.argv[2:]
    root = Path.cwd()

    try:
        if action == "snapshot":
            desc = " ".join(args) if args else f"manual-{time.strftime('%H%M%S')}"
            snap = git_snapshot(str(root), desc)
            print(json.dumps({"ref": snap.ref, "description": snap.description, "timestamp": snap.timestamp}))

        elif action == "restore":
            ref = args[0] if args else "stash@{0}"
            snap = GitSnapshot(ref=ref, description="manual", timestamp=0)
            ok = git_restore_snapshot(snap, str(root))
            print(json.dumps({"ok": ok, "ref": ref}))

        elif action == "rename_symbol":
            if len(args) < 3:
                print(json.dumps({"error": "Usage: rename_symbol <file> <old_name> <new_name> [--dry-run]"}))
                sys.exit(1)
            filepath, old_name, new_name = args[0], args[1], args[2]
            dry_run = "--dry-run" in args

            # Auto snapshot
            snap_info = _auto_snapshot(root)

            target = root / filepath
            if not target.exists():
                print(json.dumps({"error": f"File not found: {filepath}"}))
                sys.exit(1)

            content = target.read_text(encoding="utf-8")
            # Smart rename: match whole-word occurrences of old_name
            pattern = re.compile(r'\b' + re.escape(old_name) + r'\b')
            matches = list(pattern.finditer(content))
            occurrences = len(matches)

            if occurrences == 0:
                print(json.dumps({"error": f"Symbol '{old_name}' not found in {filepath}"}))
                sys.exit(1)

            if dry_run:
                print(json.dumps({
                    "dry_run": True,
                    "file": filepath,
                    "symbol": old_name,
                    "new_name": new_name,
                    "occurrences": occurrences,
                    "preview": [{"line": content[:m.start()].count('\n') + 1, "context": content[max(0, m.start()-20):m.end()+20].strip()} for m in matches[:10]],
                    "snapshot": snap_info,
                }))
            else:
                new_content = pattern.sub(new_name, content)
                target.write_text(new_content, encoding="utf-8")
                print(json.dumps({
                    "ok": True,
                    "file": filepath,
                    "symbol": old_name,
                    "new_name": new_name,
                    "occurrences": occurrences,
                    "snapshot": snap_info,
                }))

        elif action == "extract_function":
            if len(args) < 4:
                print(json.dumps({"error": "Usage: extract_function <file> <start_line> <end_line> <new_name> [--dry-run]"}))
                sys.exit(1)
            filepath, start_line, end_line, new_name = args[0], int(args[1]), int(args[2]), args[3]
            dry_run = "--dry-run" in args

            snap_info = _auto_snapshot(root)
            target = root / filepath
            content = target.read_text(encoding="utf-8")
            lines = content.splitlines()

            if start_line < 1 or end_line > len(lines) or start_line >= end_line:
                print(json.dumps({"error": f"Invalid line range: {start_line}-{end_line} (file has {len(lines)} lines)"}))
                sys.exit(1)

            extracted = "\n".join(lines[start_line - 1:end_line])
            indent = re.match(r'^(\s*)', lines[start_line - 1]).group(1)

            if "return " in extracted:
                new_func = f"{indent}def {new_name}():  # extracted\n{extracted}\n"
            else:
                new_func = f"{indent}def {new_name}():  # extracted\n{extracted}\n"

            replacement = f"{indent}{new_name}()  # extracted function"

            if dry_run:
                print(json.dumps({
                    "dry_run": True, "file": filepath,
                    "extracted_lines": f"{start_line}-{end_line}",
                    "new_name": new_name,
                    "extracted_code": extracted[:500],
                    "replacement": replacement,
                    "snapshot": snap_info,
                }))
            else:
                # Insert new function after the extracted block, replace block with call
                new_lines = lines[:start_line - 1] + [replacement] + lines[end_line:]
                new_lines.insert(end_line + 1, "")  # blank line
                new_lines.insert(end_line + 2, new_func.strip())
                target.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                print(json.dumps({
                    "ok": True, "file": filepath, "new_name": new_name, "snapshot": snap_info,
                }))

        elif action == "dead_code":
            path = root / args[0] if args else root
            files = _find_source_files(path) if path.is_dir() else [path]
            all_findings = []
            for f in files[:100]:
                all_findings.extend(_detect_dead_imports(f))
                all_findings.extend(_detect_unused_variables(f))
            print(json.dumps({
                "count": len(all_findings),
                "findings": all_findings[:100],
            }, indent=2, ensure_ascii=False))

        elif action == "preview":
            filepath = args[0] if args else "."
            diff = git_diff(str(root))
            if filepath != ".":
                # Filter diff for specific file
                sections = diff.split("diff --git ")
                relevant = [s for s in sections if filepath in s]
                diff = "diff --git " + "\ndiff --git ".join(relevant) if relevant else "(no changes)"
            print(json.dumps({"diff": diff[:8000]}, indent=2, ensure_ascii=False))

        else:
            print(json.dumps({"error": f"Unknown action: {action}"}))
            sys.exit(1)

    except Exception as exc:
        print(json.dumps({"error": f"{exc.__class__.__name__}: {exc}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
