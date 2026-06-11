"""
code_common.py — shared infrastructure for code skills.

Provides:
  - Stack detection & project fingerprinting
  - Git operations (diff, log, branch, snapshot, restore)
  - Dependency graph analysis (imports, require()s, Cargo.toml deps, etc.)
  - AST-level symbol extraction (Python, TypeScript, JavaScript, Rust, Go)
  - Lint/format/test/build command inference & execution
  - File editing with backup
  - Security scanning (secrets, dangerous patterns)
  - Code complexity (cyclomatic, cognitive hotspots)
  - Changelog generation from git history

Mirrors the role of research/research_common.py for code skills.
"""

from __future__ import annotations

import ast as py_ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ── constants ─────────────────────────────────────────

DEFAULT_EXCLUDES = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".nx",
    "dist", "build", "target", ".next", ".nuxt", ".cache",
    "coverage", ".coverage", "*.pyc", "*.pyo", "*.class",
    "*.o", "*.so", "*.dylib", "*.dll", "*.exe", "*.bin",
}

SECRET_PATTERNS = [
    (re.compile(r'(?:api[_-]?key|apikey|api_secret|secret[_-]?key)\s*[:=]\s*["\']?([a-zA-Z0-9_\-=+/]{16,})["\']?', re.I), "API Key"),
    (re.compile(r'(?:password|passwd|pwd)\s*[:=]\s*["\']([^"\']{4,})["\']', re.I), "Password"),
    (re.compile(r'(?:token|access_token|auth_token)\s*[:=]\s*["\']?([a-zA-Z0-9_\-\.]{16,})["\']?', re.I), "Token"),
    (re.compile(r'sk-[a-zA-Z0-9]{32,}', re.I), "OpenAI Key"),
    (re.compile(r'ghp_[a-zA-Z0-9]{36}', re.I), "GitHub PAT"),
    (re.compile(r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----', re.I), "Private Key"),
    (re.compile(r'(?:Bearer|Authorization)\s+([a-zA-Z0-9_\-=+/]{20,})', re.I), "Authorization Header"),
]

# Files likely to contain project-level configuration
CONFIG_FILES = [
    "package.json", "tsconfig.json", "pyproject.toml", "Cargo.toml",
    "go.mod", "Makefile", "CMakeLists.txt", "build.gradle", "pom.xml",
    ".eslintrc.*", ".prettierrc*", "ruff.toml", ".editorconfig",
    "Dockerfile", "docker-compose.yml", ".github/workflows/*.yml",
]

# Test file patterns
TEST_PATTERNS = [
    re.compile(r"^test_.*\.py$"), re.compile(r".*_test\.py$"),
    re.compile(r".*\.test\.(ts|tsx|js|jsx)$"), re.compile(r".*\.spec\.(ts|tsx|js|jsx)$"),
    re.compile(r"^test_.*\.(rs|go|java)$"), re.compile(r".*_test\.(rs|go|java)$"),
    re.compile(r".*Test\.(java|kt)$"), re.compile(r".*Tests\.(java|kt)$"),
]


# ── data types ────────────────────────────────────────

@dataclass
class ProjectFingerprint:
    """Summary of a project's tech stack and structure."""
    name: str = ""
    root: str = ""
    language: str = ""
    framework: str = ""
    package_manager: str = ""
    build_tool: str = ""
    test_framework: str = ""
    lint_tools: list[str] = field(default_factory=list)
    source_dirs: list[str] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    total_files: int = 0
    total_lines: int = 0

@dataclass
class SymbolInfo:
    name: str
    kind: str          # function, class, method, variable, interface, type
    file: str
    line: int
    docstring: str = ""
    exported: bool = True

@dataclass
class DependencyInfo:
    name: str
    version: str = ""
    is_dev: bool = False
    imports_count: int = 0

@dataclass
class GitSnapshot:
    """A lightweight git snapshot for rollback."""
    ref: str           # stash ref or commit hash
    description: str
    timestamp: float

@dataclass
class DiffHunk:
    file: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    content: str

@dataclass
class ComplexityReport:
    file: str
    line_count: int
    function_count: int
    avg_complexity: float
    hotspots: list[dict]  # {name, line, complexity, reason}


# ── project fingerprinting ────────────────────────────

def fingerprint_project(root: str = ".") -> ProjectFingerprint:
    """Analyze a project directory and return a fingerprint."""
    rp = Path(root).resolve()
    fp = ProjectFingerprint(root=str(rp), name=rp.name)

    # Detect language and tools from config files
    files_in_root = {f.name for f in rp.iterdir() if f.is_file()}

    if "package.json" in files_in_root:
        try:
            pkg = json.loads((rp / "package.json").read_text(encoding="utf-8"))
            fp.package_manager = _detect_lockfile(rp, ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb"])
            scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
            fp.language = "TypeScript" if "tsconfig.json" in files_in_root else "JavaScript"
            fp.build_tool = "vite" if "vite" in str(scripts) else ("webpack" if "webpack" in str(scripts) else "npm")
            fp.test_framework = _detect_test_framework(str(scripts), str(pkg.get("devDependencies", {})))
            fp.lint_tools = _detect_lint_tools(str(scripts), str(pkg.get("devDependencies", {})))
            fp.entry_points = _find_npm_entries(pkg)
        except Exception:
            pass
        if "next.config" in str(list(rp.iterdir())):
            fp.framework = "Next.js"
        elif "vite.config" in str(list(rp.iterdir())):
            fp.framework = "Vite"
        elif "remix.config" in str(list(rp.iterdir())):
            fp.framework = "Remix"
        fp.source_dirs = _find_dirs(rp, ["src", "app", "pages", "components", "lib", "utils"])
        fp.test_dirs = _find_dirs(rp, ["tests", "test", "__tests__", "spec"])

    elif "pyproject.toml" in files_in_root or "setup.py" in files_in_root or "setup.cfg" in files_in_root:
        fp.language = "Python"
        fp.package_manager = _detect_lockfile(rp, ["poetry.lock", "Pipfile.lock", "uv.lock"]) or "pip"
        fp.test_framework = "pytest" if _find_dirs(rp, ["tests", "test"]) else "unittest"
        fp.lint_tools = _detect_python_lint_tools(rp)
        fp.source_dirs = _find_dirs(rp, ["src", "lib", str(rp.name.replace("-", "_")), "app"])
        fp.test_dirs = _find_dirs(rp, ["tests", "test"])

    elif "Cargo.toml" in files_in_root:
        fp.language = "Rust"
        fp.build_tool = "cargo"
        fp.test_framework = "cargo test"
        fp.lint_tools = ["cargo clippy"]
        fp.source_dirs = _find_dirs(rp, ["src"])
        fp.test_dirs = _find_dirs(rp, ["tests"])

    elif "go.mod" in files_in_root:
        fp.language = "Go"
        fp.build_tool = "go build"
        fp.test_framework = "go test"
        fp.source_dirs = _find_dirs(rp, ["cmd", "internal", "pkg"])
        fp.test_dirs = fp.source_dirs  # Go tests are co-located

    # Count files and lines
    count_exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".kt", ".swift"}
    for ext in count_exts:
        for f in rp.rglob(f"*{ext}"):
            if _is_excluded(f, rp):
                continue
            fp.total_files += 1
            try:
                fp.total_lines += len(f.read_text(encoding="utf-8", errors="ignore").splitlines())
            except Exception:
                pass

    return fp


def _detect_lockfile(root: Path, names: list[str]) -> str:
    for name in names:
        if (root / name).exists():
            return name.split(".")[0].replace("-lock", "").replace("package-lock", "npm")
    return ""

def _find_dirs(root: Path, candidates: list[str]) -> list[str]:
    result = []
    for c in candidates:
        d = root / c
        if d.is_dir():
            result.append(c)
    return result

def _is_excluded(path: Path, root: Path) -> bool:
    parts = set(path.relative_to(root).parts)
    return bool(parts & DEFAULT_EXCLUDES)

def _detect_test_framework(scripts: str, deps: str) -> str:
    for fw in ["vitest", "jest", "mocha", "ava", "tap", "jasmine"]:
        if fw in scripts.lower() or fw in deps.lower():
            return fw
    return ""

def _detect_lint_tools(scripts: str, deps: str) -> list[str]:
    tools = []
    for t in ["eslint", "prettier", "oxlint", "biome", "stylelint"]:
        if t in scripts.lower() or t in deps.lower():
            tools.append(t)
    return tools

def _detect_python_lint_tools(root: Path) -> list[str]:
    tools = []
    files = {f.name for f in root.iterdir() if f.is_file()}
    if "ruff.toml" in files or "ruff" in str(files):
        tools.append("ruff")
    if ".pylintrc" in files:
        tools.append("pylint")
    if "mypy.ini" in files or "mypy" in str(files):
        tools.append("mypy")
    return tools

def _find_npm_entries(pkg: dict) -> list[str]:
    entries = []
    if isinstance(pkg, dict):
        main = pkg.get("main", "")
        if main:
            entries.append(main)
        exports = pkg.get("exports", {})
        if isinstance(exports, dict):
            exp_main = exports.get(".", "")
            if exp_main:
                entries.append(exp_main)
    return entries

# ── git operations ────────────────────────────────────

def git_root(path: str = ".") -> str | None:
    """Find the git repository root."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10, cwd=path,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def git_diff(path: str = ".", staged: bool = False, unified: int = 3) -> str:
    """Return unified diff of working tree changes."""
    cmd = ["git", "diff"]
    if staged:
        cmd.append("--staged")
    cmd.append(f"-U{unified}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=path)
        return result.stdout
    except Exception as exc:
        return f"[ERROR] git diff failed: {exc}"


def git_log(path: str = ".", max_count: int = 20, oneline: bool = False) -> list[dict]:
    """Return recent commits."""
    fmt = "--pretty=format:%H|%an|%ad|%s" if not oneline else "--oneline"
    cmd = ["git", "log", f"-n{max_count}", "--date=short", fmt]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=path)
        if result.returncode != 0:
            return []
        commits = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            if oneline:
                parts = line.split(" ", 1)
                commits.append({"hash": parts[0], "message": parts[1] if len(parts) > 1 else ""})
            else:
                parts = line.split("|", 3)
                if len(parts) >= 4:
                    commits.append({
                        "hash": parts[0], "author": parts[1],
                        "date": parts[2], "message": parts[3],
                    })
        return commits
    except Exception:
        return []


def git_snapshot(path: str = ".", description: str = "") -> GitSnapshot:
    """Create a git stash snapshot for rollback."""
    desc = description or f"snapshot-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        result = subprocess.run(
            ["git", "stash", "push", "--include-untracked", "-m", desc],
            capture_output=True, text=True, timeout=30, cwd=path,
        )
        if result.returncode == 0:
            # Get the stash ref
            ref_result = subprocess.run(
                ["git", "stash", "list", "-n1", "--format=%H"],
                capture_output=True, text=True, timeout=5, cwd=path,
            )
            return GitSnapshot(
                ref=ref_result.stdout.strip() or "stash@{0}",
                description=desc,
                timestamp=time.time(),
            )
    except Exception:
        pass
    # Fallback: just note the HEAD
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=5, cwd=path,
    ).stdout.strip()
    return GitSnapshot(ref=head, description=desc, timestamp=time.time())


def git_restore_snapshot(snap: GitSnapshot, path: str = ".") -> bool:
    """Restore to a git stash or commit."""
    try:
        if "stash" in snap.ref:
            result = subprocess.run(
                ["git", "stash", "pop", snap.ref],
                capture_output=True, text=True, timeout=30, cwd=path,
            )
        else:
            result = subprocess.run(
                ["git", "reset", "--hard", snap.ref],
                capture_output=True, text=True, timeout=30, cwd=path,
            )
        return result.returncode == 0
    except Exception:
        return False


def git_branch_info(path: str = ".") -> dict:
    """Return current branch info."""
    info = {"branch": "", "ahead": 0, "behind": 0, "modified_files": [], "untracked_files": []}
    try:
        br = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, timeout=5, cwd=path)
        info["branch"] = br.stdout.strip()
        # Modified files
        mod = subprocess.run(["git", "diff", "--name-only"], capture_output=True, text=True, timeout=5, cwd=path)
        info["modified_files"] = [f for f in mod.stdout.strip().split("\n") if f]
        # Untracked files
        unt = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True, timeout=5, cwd=path)
        info["untracked_files"] = [f for f in unt.stdout.strip().split("\n") if f]
    except Exception:
        pass
    return info


def git_files_changed(since: str = "HEAD~1", path: str = ".") -> list[str]:
    """List files changed since a given ref."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", since],
            capture_output=True, text=True, timeout=10, cwd=path,
        )
        return [f for f in result.stdout.strip().split("\n") if f]
    except Exception:
        return []


# ── AST symbol extraction ─────────────────────────────

def extract_symbols_python(filepath: str) -> list[SymbolInfo]:
    """Extract classes, functions, methods from a Python file."""
    symbols = []
    try:
        source = Path(filepath).read_text(encoding="utf-8")
        tree = py_ast.parse(source)
        for node in py_ast.walk(tree):
            if isinstance(node, py_ast.ClassDef):
                symbols.append(SymbolInfo(
                    name=node.name, kind="class", file=filepath,
                    line=node.lineno,
                    docstring=py_ast.get_docstring(node) or "",
                    exported=not node.name.startswith("_"),
                ))
                for item in node.body:
                    if isinstance(item, py_ast.FunctionDef):
                        symbols.append(SymbolInfo(
                            name=f"{node.name}.{item.name}", kind="method",
                            file=filepath, line=item.lineno,
                            docstring=py_ast.get_docstring(item) or "",
                            exported=not item.name.startswith("_"),
                        ))
            elif isinstance(node, py_ast.FunctionDef) and not _is_method(node, tree):
                symbols.append(SymbolInfo(
                    name=node.name, kind="function", file=filepath,
                    line=node.lineno,
                    docstring=py_ast.get_docstring(node) or "",
                    exported=not node.name.startswith("_"),
                ))
    except Exception:
        pass
    return symbols


def _is_method(func_node: py_ast.FunctionDef, tree: py_ast.AST) -> bool:
    for node in py_ast.walk(tree):
        if isinstance(node, py_ast.ClassDef):
            for item in node.body:
                if item is func_node:
                    return True
    return False


def extract_symbols_typescript(filepath: str) -> list[SymbolInfo]:
    """Best-effort symbol extraction from TS/JS files using regex patterns."""
    symbols = []
    try:
        source = Path(filepath).read_text(encoding="utf-8")
        # Functions: function name() / const name = () => / export function name
        for m in re.finditer(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)', source):
            symbols.append(SymbolInfo(name=m.group(1), kind="function", file=filepath, line=source[:m.start()].count('\n') + 1))
        for m in re.finditer(r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(', source):
            symbols.append(SymbolInfo(name=m.group(1), kind="function", file=filepath, line=source[:m.start()].count('\n') + 1))
        # Classes: class Name / export class Name
        for m in re.finditer(r'(?:export\s+)?(?:abstract\s+)?class\s+(\w+)', source):
            symbols.append(SymbolInfo(name=m.group(1), kind="class", file=filepath, line=source[:m.start()].count('\n') + 1))
        # Interfaces/types: interface Name / type Name =
        for m in re.finditer(r'(?:export\s+)?interface\s+(\w+)', source):
            symbols.append(SymbolInfo(name=m.group(1), kind="interface", file=filepath, line=source[:m.start()].count('\n') + 1))
        for m in re.finditer(r'(?:export\s+)?type\s+(\w+)\s*=', source):
            symbols.append(SymbolInfo(name=m.group(1), kind="type", file=filepath, line=source[:m.start()].count('\n') + 1))
    except Exception:
        pass
    return symbols


def extract_symbols(filepath: str, language: str = "") -> list[SymbolInfo]:
    """Extract symbols from a source file, auto-detecting language."""
    ext = Path(filepath).suffix.lower()
    if language == "python" or ext == ".py":
        return extract_symbols_python(filepath)
    elif language in ("typescript", "javascript") or ext in (".ts", ".tsx", ".js", ".jsx"):
        return extract_symbols_typescript(filepath)
    return []


# ── dependency graph ──────────────────────────────────

def extract_dependencies(root: str = ".") -> list[DependencyInfo]:
    """Extract declared dependencies from project config files."""
    deps = []
    rp = Path(root)

    # Node.js
    pkg_json = rp / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            for name, ver in (pkg.get("dependencies") or {}).items():
                deps.append(DependencyInfo(name=name, version=str(ver), is_dev=False))
            for name, ver in (pkg.get("devDependencies") or {}).items():
                deps.append(DependencyInfo(name=name, version=str(ver), is_dev=True))
        except Exception:
            pass

    # Python
    for cfg in ["pyproject.toml", "setup.py", "requirements.txt"]:
        cfg_path = rp / cfg
        if not cfg_path.exists():
            continue
        try:
            if cfg == "requirements.txt":
                for line in cfg_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        name = re.split(r'[=<>~!]', line)[0].strip()
                        deps.append(DependencyInfo(name=name))
            elif cfg == "pyproject.toml":
                # Simple TOML dep extraction
                content = cfg_path.read_text(encoding="utf-8")
                in_deps = False
                for line in content.splitlines():
                    if "dependencies" in line and "[" in line:
                        in_deps = True
                    elif in_deps and line.strip().startswith("["):
                        in_deps = False
                    elif in_deps:
                        m = re.match(r'\s*"([^"]+)"', line)
                        if m:
                            deps.append(DependencyInfo(name=m.group(1)))
        except Exception:
            pass

    # Rust
    cargo = rp / "Cargo.toml"
    if cargo.exists():
        try:
            content = cargo.read_text(encoding="utf-8")
            in_deps = False
            for line in content.splitlines():
                if line.strip().startswith("[dependencies"):
                    in_deps = True
                elif in_deps and line.strip().startswith("["):
                    in_deps = False
                elif in_deps:
                    m = re.match(r'\s*(\S+)\s*=', line)
                    if m:
                        deps.append(DependencyInfo(name=m.group(1)))
        except Exception:
            pass

    return deps


# ── build / lint / test commands ──────────────────────

def infer_commands(root: str = ".") -> dict[str, list[str]]:
    """Infer build, lint, test, and format commands from project structure."""
    fp = fingerprint_project(root)
    cmds = {"build": [], "lint": [], "test": [], "format": [], "typecheck": []}

    rp = Path(root)

    # Node / TypeScript
    if (rp / "package.json").exists():
        cmds["build"].append("npm run build")
        if fp.test_framework:
            cmds["test"].append(f"npm run test")
        else:
            cmds["test"].append("npm test")
        for tool in fp.lint_tools:
            cmds["lint"].append(f"npx {tool} .")
        cmds["format"].append("npx prettier --write .")
        if "typescript" in fp.language.lower():
            cmds["typecheck"].append("npx tsc --noEmit")

    # Python
    elif (rp / "pyproject.toml").exists() or (rp / "setup.py").exists():
        cmds["lint"].append("ruff check .")
        cmds["format"].append("ruff format .")
        cmds["test"].append("pytest -q")
        cmds["typecheck"].append("mypy .")

    # Rust
    elif (rp / "Cargo.toml").exists():
        cmds["build"].append("cargo build")
        cmds["test"].append("cargo test")
        cmds["lint"].append("cargo clippy")
        cmds["format"].append("cargo fmt")

    # Go
    elif (rp / "go.mod").exists():
        cmds["build"].append("go build ./...")
        cmds["test"].append("go test ./...")
        cmds["lint"].append("golangci-lint run")
        cmds["format"].append("go fmt ./...")

    return cmds


def run_command(cmd: str, cwd: str = ".", timeout: int = 120) -> dict:
    """Run a shell command and return structured output."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout[-5000:] if len(result.stdout) > 5000 else result.stdout,
            "stderr": result.stderr[-3000:] if len(result.stderr) > 3000 else result.stderr,
            "truncated_stdout": len(result.stdout) > 5000,
            "truncated_stderr": len(result.stderr) > 3000,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": -1, "stdout": "", "stderr": f"Timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "exit_code": -1, "stdout": "", "stderr": str(e)}


# ── file editing with backup ──────────────────────────

def edit_file(filepath: str, old_str: str, new_str: str, backup: bool = True) -> dict:
    """Replace old_str with new_str in a file. Creates backup if requested."""
    path = Path(filepath)
    if not path.exists():
        return {"ok": False, "error": f"File not found: {filepath}"}
    try:
        content = path.read_text(encoding="utf-8")
        if old_str not in content:
            return {"ok": False, "error": "old_string not found in file"}
        if old_str == new_str:
            return {"ok": False, "error": "old_string and new_string are identical"}
        count = content.count(old_str)
        if count > 1:
            return {"ok": False, "error": f"old_string matches {count} times — must be unique. Use replace_all=True to replace all occurrences."}
        if backup:
            backup_path = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup_path)
        new_content = content.replace(old_str, new_str, 1)
        path.write_text(new_content, encoding="utf-8")
        return {"ok": True, "file": filepath, "matched": 1, "backup": str(backup_path) if backup else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def replace_all_in_file(filepath: str, old_str: str, new_str: str, backup: bool = True) -> dict:
    """Replace all occurrences of old_str with new_str."""
    path = Path(filepath)
    if not path.exists():
        return {"ok": False, "error": f"File not found: {filepath}"}
    try:
        content = path.read_text(encoding="utf-8")
        count = content.count(old_str)
        if count == 0:
            return {"ok": False, "error": "old_string not found in file"}
        if backup:
            backup_path = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup_path)
        new_content = content.replace(old_str, new_str)
        path.write_text(new_content, encoding="utf-8")
        return {"ok": True, "file": filepath, "matched": count, "backup": str(backup_path) if backup else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── security scanning ─────────────────────────────────

def scan_secrets(filepath: str) -> list[dict]:
    """Scan a file for potential secrets. Returns list of findings."""
    findings = []
    try:
        content = Path(filepath).read_text(encoding="utf-8", errors="ignore")
        for pattern, label in SECRET_PATTERNS:
            for m in pattern.finditer(content):
                line_no = content[:m.start()].count('\n') + 1
                findings.append({"file": filepath, "line": line_no, "type": label, "match": m.group(0)[:80]})
    except Exception:
        pass
    return findings


def scan_dangerous_patterns(filepath: str) -> list[dict]:
    """Find dangerous code patterns (eval, os.system, etc.)."""
    patterns = [
        (r'\beval\s*\(', "eval() call"),
        (r'\bexec\s*\(', "exec() call"),
        (r'\bos\.system\s*\(', "os.system() call — use subprocess"),
        (r'\bsubprocess\.(?:call|Popen)\s*\([^)]*shell\s*=\s*True', "subprocess with shell=True"),
        (r'\b__import__\s*\(', "__import__() dynamic import"),
        (r'\bpickle\.(?:loads|load)\s*\(', "pickle deserialization"),
        (r'\byaml\.load\s*\(', "yaml.load() — use yaml.safe_load()"),
        (r'\bdangerouslySetInnerHTML\b', "React dangerouslySetInnerHTML"),
        (r'\.innerHTML\s*=', "DOM innerHTML assignment"),
        (r'\bunsafe\s*\{', "Rust unsafe block"),
    ]
    findings = []
    try:
        content = Path(filepath).read_text(encoding="utf-8", errors="ignore")
        for pattern, label in patterns:
            for m in re.finditer(pattern, content, re.I):
                line_no = content[:m.start()].count('\n') + 1
                findings.append({"file": filepath, "line": line_no, "pattern": label, "match": m.group(0)[:100]})
    except Exception:
        pass
    return findings


# ── complexity analysis ───────────────────────────────

def analyze_complexity(filepath: str, language: str = "") -> ComplexityReport | None:
    """Estimate cyclomatic complexity for a source file."""
    path = Path(filepath)
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        lines = len(content.splitlines())
    except Exception:
        return None

    # Simple heuristic: count branching keywords
    branch_patterns = [
        r'\bif\b', r'\belif\b', r'\belse\b', r'\bfor\b', r'\bwhile\b',
        r'\bmatch\b', r'\bcase\b', r'\bcatch\b', r'\bexcept\b',
        r'\?\s*[^:]+:', r'&&', r'\|\|',  # ternary, and, or
    ]
    complexity_score = 0
    for p in branch_patterns:
        complexity_score += len(re.findall(p, content))

    # Count function-like constructs
    func_count = len(re.findall(r'\b(?:def|fn|func|function)\s+(\w+)', content))
    if func_count == 0:
        func_count = 1  # avoid division by zero
    avg = complexity_score / func_count

    # Find hotspots (functions with high complexity)
    hotspots = []
    func_pattern = re.compile(r'(?:def|fn|func|function)\s+(\w+)', re.I)
    for m in func_pattern.finditer(content):
        # Rough: count branches between this function and the next
        start = m.start()
        next_func = func_pattern.search(content, m.end())
        end = next_func.start() if next_func else len(content)
        body = content[start:end]
        local_complexity = sum(len(re.findall(p, body)) for p in branch_patterns)
        if local_complexity > 10:
            hotspots.append({
                "name": m.group(1), "line": content[:start].count('\n') + 1,
                "complexity": local_complexity,
                "reason": f"High complexity ({local_complexity} branches) — consider refactoring",
            })

    return ComplexityReport(
        file=filepath,
        line_count=lines,
        function_count=func_count,
        avg_complexity=round(avg, 1),
        hotspots=hotspots,
    )


# ── code search (upgraded) ────────────────────────────

def search_code(
    root: str, pattern: str, glob: str = "*", max_results: int = 100,
    case_sensitive: bool = False, context_lines: int = 2,
) -> list[dict]:
    """Full-text search across source files with context."""
    results = []
    compiled = re.compile(pattern, 0 if case_sensitive else re.I)
    rp = Path(root)

    for filepath in rp.rglob(glob):
        if _is_excluded(filepath, rp):
            continue
        if not filepath.is_file():
            continue
        # Skip binary files
        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if compiled.search(line):
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                results.append({
                    "file": str(filepath.relative_to(rp)),
                    "line": i + 1,
                    "match": line.strip()[:200],
                    "context": "\n".join(lines[start:end]),
                })
                if len(results) >= max_results:
                    return results
    return results


# ── changelog generation ──────────────────────────────

def generate_changelog(path: str = ".", since: str = "HEAD~10") -> str:
    """Generate a Markdown changelog from git history."""
    commits = git_log(path, max_count=50)
    if not commits:
        return "No git history found."

    grouped = {"feat": [], "fix": [], "refactor": [], "docs": [], "chore": [], "other": []}
    for c in commits:
        msg = c["message"]
        prefix = "other"
        for p in ["feat", "fix", "refactor", "docs", "chore", "test", "perf"]:
            if msg.lower().startswith(p) or f"({p})" in msg.lower():
                prefix = p
                break
        grouped[prefix].append(c)

    lines = ["## Changelog\n"]
    for group, items in grouped.items():
        if not items:
            continue
        lines.append(f"### {group.capitalize()}")
        for item in items[:10]:
            lines.append(f"- {item['message']} ({item['author']}, {item['date']})")
        lines.append("")
    return "\n".join(lines)
