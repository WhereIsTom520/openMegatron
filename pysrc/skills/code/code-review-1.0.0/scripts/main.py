#!/usr/bin/env python3
"""code-review v1.0.0 — comprehensive code review with severity-graded findings."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_skills_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_skills_root) not in sys.path:
    sys.path.insert(0, str(_skills_root))

from code.code_common import (
    fingerprint_project,
    scan_secrets,
    scan_dangerous_patterns,
    analyze_complexity,
    extract_dependencies,
    git_files_changed,
    search_code,
    DEFAULT_EXCLUDES,
)


def _find_source_files(root: Path, exts: set[str] = None) -> list[Path]:
    exts = exts or {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".kt"}
    files = []
    for ext in exts:
        for f in root.rglob(f"*{ext}"):
            parts = set(f.relative_to(root).parts)
            if not (parts & DEFAULT_EXCLUDES):
                files.append(f)
    return files


def _severity_for_secret(label: str) -> str:
    critical = {"Private Key", "OpenAI Key", "GitHub PAT", "Authorization Header"}
    high = {"API Key", "Token"}
    if label in critical:
        return "critical"
    if label in high:
        return "high"
    return "medium"


def _severity_for_dangerous(pattern: str) -> str:
    critical = {"eval() call", "exec() call"}
    high = {"os.system() call", "subprocess with shell=True", "pickle deserialization"}
    if pattern in critical:
        return "critical"
    if pattern in high:
        return "high"
    return "medium"


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "review"
    target_arg = sys.argv[2] if len(sys.argv) > 2 else "."
    root = Path.cwd()
    target = root / target_arg if target_arg != "." else root

    try:
        if action in ("review", "security_audit", "complexity_report", "diff_review"):
            # Determine files to review
            if action == "diff_review":
                since = target_arg if target_arg != "review" else "HEAD~1"
                changed = git_files_changed(since, str(root))
                files = [root / f for f in changed if (root / f).exists()]
                print(json.dumps({"mode": "diff_review", "since": since, "files": len(files)}, indent=2))
            else:
                files = _find_source_files(target if target != root else root)

            findings: list[dict] = []
            complexity_reports: list[dict] = []
            stats = {"scanned": len(files), "secrets": 0, "dangerous": 0, "hotspots": 0}

            for f in files[:300]:
                rel = str(f.relative_to(root))

                # Security: secrets
                for finding in scan_secrets(str(f)):
                    findings.append({
                        "file": rel, "line": finding["line"], "severity": _severity_for_secret(finding["type"]),
                        "category": "secret", "title": f"Exposed {finding['type']}",
                        "detail": finding["match"],
                    })
                    stats["secrets"] += 1

                # Security: dangerous patterns
                for finding in scan_dangerous_patterns(str(f)):
                    findings.append({
                        "file": rel, "line": finding["line"], "severity": _severity_for_dangerous(finding["pattern"]),
                        "category": "dangerous", "title": finding["pattern"],
                        "detail": finding["match"],
                    })
                    stats["dangerous"] += 1

                # Complexity
                report = analyze_complexity(str(f))
                if report and report.hotspots:
                    for hs in report.hotspots:
                        sev = "critical" if hs["complexity"] > 30 else ("high" if hs["complexity"] > 20 else "medium")
                        findings.append({
                            "file": rel, "line": hs["line"], "severity": sev,
                            "category": "complexity", "title": f"Complex function: {hs['name']}",
                            "detail": hs["reason"],
                            "complexity": hs["complexity"],
                        })
                        stats["hotspots"] += 1
                    complexity_reports.append({
                        "file": rel, "line_count": report.line_count,
                        "function_count": report.function_count,
                        "avg_complexity": report.avg_complexity,
                    })

            # Sort by severity
            sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
            findings.sort(key=lambda x: sev_order.get(x["severity"], 99))

            # Group by severity
            by_severity = {}
            for f_item in findings:
                by_severity.setdefault(f_item["severity"], []).append(f_item)

            # Summary
            summary = {
                "critical": len(by_severity.get("critical", [])),
                "high": len(by_severity.get("high", [])),
                "medium": len(by_severity.get("medium", [])),
                "low": len(by_severity.get("low", [])),
                "info": len(by_severity.get("info", [])),
            }

            print(json.dumps({
                "action": action,
                "stats": stats,
                "summary": summary,
                "top_findings": findings[:50],
                "complexity_top": complexity_reports[:10],
            }, indent=2, ensure_ascii=False))

        elif action == "deps_health":
            deps = extract_dependencies(str(root))
            prod = [d for d in deps if not d.is_dev]
            dev = [d for d in deps if d.is_dev]

            # Check for known vulnerability tools
            vuln_info = None
            fp = fingerprint_project(str(root))
            if fp.package_manager in ("npm", "pnpm", "yarn"):
                from code.code_common import run_command
                audit = run_command("npm audit --json 2>&1", str(root), timeout=120)
                if audit.get("ok"):
                    try:
                        vuln_data = json.loads(audit["stdout"])
                        vuln_info = {"advisories": vuln_data.get("metadata", {}).get("vulnerabilities", {}),
                                     "total": vuln_data.get("metadata", {}).get("totalDependencies", 0)}
                    except Exception:
                        vuln_info = {"raw": audit.get("stdout", "")[:1000]}

            print(json.dumps({
                "total_deps": len(deps),
                "production": len(prod),
                "dev": len(dev),
                "production_deps": [{"name": d.name, "version": d.version} for d in prod[:50]],
                "vulnerabilities": vuln_info,
                "unused_hint": "Run 'npx depcheck' or 'deptry' to detect unused dependencies",
            }, indent=2, ensure_ascii=False))

        else:
            print(json.dumps({"error": f"Unknown action: {action}"}))
            sys.exit(1)

    except Exception as exc:
        print(json.dumps({"error": f"{exc.__class__.__name__}: {exc}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
