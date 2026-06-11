#!/usr/bin/env python3
"""code-test v1.0.0 — test generation, coverage, and quality analysis."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_skills_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_skills_root) not in sys.path:
    sys.path.insert(0, str(_skills_root))

from code.code_common import (
    fingerprint_project,
    extract_symbols,
    infer_commands,
    run_command,
    search_code,
    DEFAULT_EXCLUDES,
)


def _find_test_files(root: Path) -> list[Path]:
    """Find test files using naming conventions."""
    patterns = [
        "test_*.py", "*_test.py",
        "*.test.ts", "*.test.tsx", "*.test.js", "*.test.jsx",
        "*.spec.ts", "*.spec.tsx", "*.spec.js", "*.spec.jsx",
        "test_*.rs", "*_test.rs",
        "*_test.go",
    ]
    results = []
    for pat in patterns:
        for f in root.rglob(pat):
            parts = set(f.relative_to(root).parts)
            if not (parts & DEFAULT_EXCLUDES):
                results.append(f)
    return results


def _find_source_files(root: Path) -> list[Path]:
    exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go"}
    files = []
    for ext in exts:
        for f in root.rglob(f"*{ext}"):
            parts = set(f.relative_to(root).parts)
            if not (parts & DEFAULT_EXCLUDES):
                # Skip obvious test files
                name = f.name.lower()
                if not any(p in name for p in ["test_", "_test.", ".test.", ".spec.", "__test"]):
                    files.append(f)
    return files


def _generate_test_stubs(symbols: list, source_file: str) -> list[dict]:
    """Generate test stub suggestions for functions."""
    suggestions = []
    for sym in symbols:
        if sym.kind not in ("function", "method"):
            continue
        name = sym.name
        # Skip private/internal
        if name.startswith("_") and not name.startswith("__"):
            continue

        # Basic scenarios
        scenarios = []

        # Detect parameter hints from docstring
        params = []
        if sym.docstring:
            param_matches = re.findall(r'(?:param|arg)\s+(\w+)', sym.docstring, re.I)
            params = param_matches

        scenarios.append({
            "scenario": f"{name} with valid inputs",
            "input_desc": "typical, well-formed inputs" + (f" for {', '.join(params[:3])}" if params else ""),
            "expected": "returns expected result without errors",
        })
        scenarios.append({
            "scenario": f"{name} with empty/null inputs",
            "input_desc": "None, empty string, empty list, or zero",
            "expected": "handles gracefully (returns default, raises ValueError, or returns empty)",
        })
        if params:
            scenarios.append({
                "scenario": f"{name} with invalid types",
                "input_desc": f"wrong types for {params[0] if params else 'parameter'}",
                "expected": "raises TypeError",
            })
        scenarios.append({
            "scenario": f"{name} with boundary values",
            "input_desc": "extremely large values, negative numbers, or max-length strings",
            "expected": "handles without overflow or crash",
        })

        # Generate Python stub
        indent = "    "
        stub_body = []
        for i, s in enumerate(scenarios):
            stub_body.append(f"{indent}def test_{name}_{s['scenario'].replace(' ', '_').replace('/', '_')[:60]}():")
            stub_body.append(f'{indent}{indent}"""')
            stub_body.append(f"{indent}{indent}{s['scenario']}")
            stub_body.append(f"{indent}{indent}Input: {s['input_desc']}")
            stub_body.append(f"{indent}{indent}Expected: {s['expected']}")
            stub_body.append(f'{indent}{indent}"""')
            stub_body.append(f"{indent}{indent}# TODO: implement test")
            stub_body.append(f"{indent}{indent}pass")
            if i < len(scenarios) - 1:
                stub_body.append("")

        suggestions.append({
            "function": name,
            "kind": sym.kind,
            "file": sym.file,
            "line": sym.line,
            "scenarios": scenarios,
            "stub": "\n".join(stub_body),
        })

    return suggestions


def _analyze_test_quality(filepath: Path) -> dict:
    """Quick heuristic test quality score."""
    try:
        content = filepath.read_text(encoding="utf-8")
        lines = content.splitlines()
        assertions = len(re.findall(r'\bassert\b|\.toEqual\(|expect\(|\.toBe\(|\.assert', content))
        mocks = len(re.findall(r'\bMock\b|\.mock\(|jest\.fn\(|mock\.patch', content, re.I))
        test_funcs = len(re.findall(r'\bdef test_|def it\(|test\(\(|it\(\(|describe\(', content, re.I))
        todo_count = len(re.findall(r'# TODO|# FIXME|# HACK|\.skip\(|\.todo\(', content, re.I))

        issues = []
        if assertions == 0:
            issues.append("No assertions found — tests may not be verifying anything")
        if test_funcs == 0:
            issues.append("No test functions detected")
        if assertions > 0 and test_funcs > 0:
            ratio = assertions / max(test_funcs, 1)
            if ratio < 1:
                issues.append(f"Low assertion density ({ratio:.1f} assertions per test function)")
        if todo_count > 0:
            issues.append(f"{todo_count} TODO/skip markers found — incomplete coverage")

        # Quality score: 0-100
        score = 50  # baseline
        if assertions > 0:
            score += min(30, assertions)
        if mocks > 0:
            score += 10
        if test_funcs > 3:
            score += 10
        if todo_count == 0:
            score += 10
        score = min(100, score)

        return {
            "file": str(filepath),
            "test_functions": test_funcs,
            "assertions": assertions,
            "mocks": mocks,
            "todos": todo_count,
            "quality_score": score,
            "issues": issues,
        }
    except Exception:
        return {"file": str(filepath), "error": "Could not analyze"}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No action. Use: test, coverage, suggest_tests, test_quality, find_untested"}))
        sys.exit(1)

    action = sys.argv[1]
    args = sys.argv[2:]
    root = Path.cwd()

    try:
        if action == "test":
            cmds = infer_commands(str(root))
            cmd = cmds.get("test", ["pytest -q"])[0]
            result = run_command(cmd, str(root))
            result["command"] = cmd
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif action == "coverage":
            fp = fingerprint_project(str(root))
            if fp.language == "Python":
                cmd = "pytest --cov=. --cov-report=term -q 2>&1"
            elif fp.language in ("TypeScript", "JavaScript"):
                cmd = "npx vitest run --coverage 2>&1 || npx jest --coverage 2>&1"
            else:
                cmd = "cargo test 2>&1"
            result = run_command(cmd, str(root))
            result["command"] = cmd

            # Parse coverage percentage
            cov_pct = None
            for line in (result.get("stdout", "") + result.get("stderr", "")).splitlines():
                m = re.search(r'(?:TOTAL|Coverage|All files).*?(\d{1,3})\s*%', line, re.I)
                if m:
                    cov_pct = int(m.group(1))
                    break
            result["coverage_percent"] = cov_pct
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif action == "suggest_tests":
            target = root / args[0] if args else root
            if target.is_file():
                files = [target]
            else:
                files = _find_source_files(target)[:20]

            all_suggestions = []
            for f in files:
                syms = extract_symbols(str(f))
                stubs = _generate_test_stubs(syms, str(f.relative_to(root)))
                all_suggestions.extend(stubs)

            print(json.dumps({
                "count": len(all_suggestions),
                "suggestions": all_suggestions[:30],
            }, indent=2, ensure_ascii=False))

        elif action == "test_quality":
            target = root / args[0] if args else root
            if target.is_file():
                files = [target]
            else:
                files = _find_test_files(target)

            results = []
            for f in files[:50]:
                results.append(_analyze_test_quality(f))

            avg_score = sum(r.get("quality_score", 0) for r in results) / max(len(results), 1)
            print(json.dumps({
                "files_analyzed": len(results),
                "average_quality_score": round(avg_score, 1),
                "results": results,
            }, indent=2, ensure_ascii=False))

        elif action == "find_untested":
            sources = _find_source_files(root)
            tests = {f.stem.replace("_test", "").replace("test_", "").replace(".test", "").replace(".spec", ""): f for f in _find_test_files(root)}

            untested = []
            for src in sources[:100]:
                stem = src.stem
                # Check if there's a corresponding test file
                has_test = False
                for test_key, test_path in tests.items():
                    if test_key in stem or stem in test_key:
                        has_test = True
                        break
                # Also check if the file itself contains test functions
                if not has_test:
                    try:
                        content = src.read_text(encoding="utf-8", errors="ignore")
                        if "def test_" in content or "describe(" in content:
                            has_test = True
                    except Exception:
                        pass

                if not has_test:
                    syms = extract_symbols(str(src))
                    funcs = [s for s in syms if s.kind in ("function", "method")]
                    untested.append({
                        "file": str(src.relative_to(root)),
                        "functions": len(funcs),
                        "priority": "high" if len(funcs) > 5 else ("medium" if len(funcs) > 0 else "low"),
                    })

            untested.sort(key=lambda x: x["functions"], reverse=True)
            print(json.dumps({
                "count": len(untested),
                "untested": untested[:30],
            }, indent=2, ensure_ascii=False))

        else:
            print(json.dumps({"error": f"Unknown action: {action}"}))
            sys.exit(1)

    except Exception as exc:
        print(json.dumps({"error": f"{exc.__class__.__name__}: {exc}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
